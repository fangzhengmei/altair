# Altair 变换序列化与规格校验分析报告

## 1. 概述

本文档是 [transform_chain_analysis.md](./transform_chain_analysis.md) 的续篇，聚焦于从 Python 对象到 Vega-Lite JSON 输出的**后半段流程**：

1. **序列化路径**：`to_dict()` 如何将 transform 列表递归转换为字典结构
2. **类型解析机制**：联合类型的变换如何在序列化时完成类型判断与展开
3. **规格校验路径**：schema 校验在哪一层触发，如何遍历数组中的每一项
4. **与运行时的边界**：Altair 层能捕获哪些问题，哪些只能在 Vega-Lite 运行时发现

---

## 2. 序列化路径：从 Python 对象到 Vega-Lite 字典

### 2.1 核心序列化入口：`SchemaBase.to_dict()`

所有 Altair 对象的序列化都始于 `SchemaBase.to_dict()` 方法，这是整个序列化流程的核心入口：

```python
# 文件: altair/utils/schemapi.py, 行 1174-1227
def to_dict(
    self,
    validate: bool = True,
    *,
    ignore: list[str] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = context or {}
    ignore = ignore or []
    opts = _get_optional_modules(np_opt="numpy", pd_opt="pandas")

    # 处理单值参数（如字符串字面量）
    if self._args and not self._kwds:
        kwds = self._args[0]
    elif not self._args:
        # 复制关键字参数，排除不需要的键
        kwds = self._kwds.copy()
        exclude = {*ignore, "shorthand", "_cached_hash"}
        if parsed := context.pop("parsed_shorthand", None):
            kwds = _replace_parsed_shorthand(parsed, kwds)
        kwds = {k: v for k, v in kwds.items() if k not in exclude}
        # 字符串标记类型转换为字典
        if (mark := kwds.get("mark")) and isinstance(mark, str):
            kwds["mark"] = {"type": mark}
    else:
        msg = f"{type(self)} instance has both a value and properties..."
        raise ValueError(msg)
    
    # 核心转换：递归转换为字典
    result = _todict(kwds, context=context, **opts)
    
    # 校验（默认启用）
    if validate:
        try:
            self.validate(result)
        except jsonschema.ValidationError as err:
            raise SchemaValidationError(self, err) from None
    return result
```

### 2.2 递归转换核心：`_todict()` 函数

`_todict()` 是实际执行递归转换的函数，它处理各种类型的对象：

```python
# 文件: altair/utils/schemapi.py, 行 526-559
def _todict(obj: Any, context: dict[str, Any] | None, np_opt: Any, pd_opt: Any) -> Any:
    """Convert an object to a dict representation."""
    
    # 1. NumPy 特殊类型处理
    if np_opt is not None:
        np = np_opt
        if isinstance(obj, np.ndarray):
            return [_todict(v, context, np_opt, pd_opt) for v in obj]
        elif isinstance(obj, np.number):
            return float(obj)
        elif isinstance(obj, np.datetime64):
            # ... 日期时间转换
    
    # 2. SchemaBase 子类：递归调用 to_dict(validate=False)
    if isinstance(obj, SchemaBase):
        return obj.to_dict(validate=False, context=context)
    
    # 3. 列表/元组：递归转换每个元素
    elif isinstance(obj, (list, tuple)):
        return [_todict(v, context, np_opt, pd_opt) for v in obj]
    
    # 4. 字典：过滤 Undefined 值，递归转换值
    elif isinstance(obj, dict):
        return {
            k: _todict(v, context, np_opt, pd_opt)
            for k, v in obj.items()
            if v is not Undefined  # 关键：过滤 Undefined 值
        }
    
    # 5. SchemaLike 协议：调用 to_dict()
    elif isinstance(obj, SchemaLike):
        return obj.to_dict()
    
    # 6. 其他类型：直接返回（基本类型、pandas 时间戳等）
    elif pd_opt is not None and isinstance(obj, pd_opt.Timestamp):
        return pd_opt.Timestamp(obj).isoformat()
    elif _is_iterable(obj, exclude=(str, bytes)):
        return _todict(_from_array_like(obj), context, np_opt, pd_opt)
    elif isinstance(obj, dt.date):
        return _from_date_datetime(obj)
    else:
        return obj
```

### 2.3 Undefined 值的过滤机制

**Undefined 是 Altair 中的特殊单例值**，用于区分"用户未指定"和"用户指定为 None"：

```python
# 文件: altair/utils/schemapi.py, 行 967-983
class UndefinedType:
    """A singleton object for marking undefined parameters."""
    
    __instance = None
    
    def __new__(cls, *args, **kwargs) -> Self:
        if not isinstance(cls.__instance, cls):
            cls.__instance = object.__new__(cls, *args, **kwargs)
        return cls.__instance

Undefined = UndefinedType()
```

**过滤发生在字典转换阶段**：

```python
elif isinstance(obj, dict):
    return {
        k: _todict(v, context, np_opt, pd_opt)
        for k, v in obj.items()
        if v is not Undefined  # 仅在值不是 Undefined 时保留键
    }
```

### 2.4 Transform 列表的完整序列化流程

以一个包含多次变换的 Chart 为例，跟踪完整的序列化路径：

```python
import altair as alt
from altair import datum

chart = (
    alt.Chart("data.csv")
    .mark_point()
    .transform_filter(datum.x > 0)
    .transform_aggregate(sum_y="sum(y)", groupby=["category"])
    .transform_window(running_total="sum(sum_y)")
)

# 触发序列化
spec = chart.to_dict()
```

**序列化执行路径**：

```
chart.to_dict()
│
├── 进入 SchemaBase.to_dict(validate=True)
│   │
│   ├── kwds = self._kwds.copy()  # 包含:
│   │   # - mark: "point"
│   │   # - data: {"url": "data.csv"}
│   │   # - transform: [FilterTransform, AggregateTransform, WindowTransform]
│   │
│   └── result = _todict(kwds, ...)
│       │
│       └── 遇到 transform 列表: isinstance(obj, list)
│           │
│           └── 遍历每个元素:
│               │
│               ├── _todict(FilterTransform, ...)
│               │   └── isinstance(obj, SchemaBase)
│               │       └── FilterTransform.to_dict(validate=False, ...)
│               │           │
│               │           ├── kwds = {"filter": {"test": "datum.x > 0"}}
│               │           └── _todict(kwds, ...)
│               │               │
│               │               └── 递归展开，过滤 Undefined
│               │                   → {"filter": {"test": "datum.x > 0"}}
│               │
│               ├── _todict(AggregateTransform, ...)
│               │   → {"aggregate": [...], "groupby": ["category"]}
│               │
│               └── _todict(WindowTransform, ...)
│                   → {"window": [...]}
│
├── 校验阶段: self.validate(result)
│   └── validate_jsonschema(result, cls._schema, rootschema=...)
│       └── jsonschema 库验证
│
└── 返回最终结果
```

**最终输出的 transform 数组**：

```json
{
  "transform": [
    {"filter": {"test": "datum.x > 0"}},
    {
      "aggregate": [{"op": "sum", "field": "y", "as": "sum_y"}],
      "groupby": ["category"]
    },
    {
      "window": [{"op": "sum", "field": "sum_y", "as": "running_total"}]
    }
  ]
}
```

### 2.5 关键设计：子对象序列化时禁用校验

注意 `_todict()` 中的这一行：

```python
if isinstance(obj, SchemaBase):
    return obj.to_dict(validate=False, context=context)
```

**为什么子对象序列化时 `validate=False`？**

1. **性能优化**：避免重复校验（顶层会完整校验一次）
2. **容错性**：某些对象单独校验可能失败，但在上下文中是合法的
3. **单一校验点**：仅在顶层调用 `to_dict()` 时触发完整校验

---

## 3. 类型解析机制：联合类型的展开

### 3.1 谓词的联合类型支持

`transform_filter` 的 `predicate` 参数支持多种输入形式：

```python
# 文件: altair/vegalite/v6/api.py, 行 577-590
_TestPredicateType: TypeAlias = str | _expr_core.Expression | core.PredicateComposition
"""https://vega.github.io/vega-lite/docs/predicate.html"""

_PredicateType: TypeAlias = Union[
    Parameter,
    core.Expr,
    "_ConditionExtra",
    _TestPredicateType,
    _expr_core.OperatorMixin,
]
"""Permitted types for `predicate`."""
```

### 3.2 谓词解析流程

`_parse_when()` 是解析谓词的核心函数：

```python
# 文件: altair/vegalite/v6/api.py, 行 868-889
def _parse_when(
    predicate: Optional[_PredicateType],
    *more_predicates: _ComposablePredicateType,
    empty: Optional[bool],
    **constraints: _FieldEqualType,
) -> _Condition:
    composed: _PredicateType
    if utils.is_undefined(predicate):
        if more_predicates or constraints:
            # 组合多个谓词
            composed = _parse_when_compose(more_predicates, constraints)
        else:
            raise TypeError("At least one predicate...")
    elif more_predicates or constraints:
        # 组合主谓词和其他谓词
        predicates = predicate, *more_predicates
        composed = _parse_when_compose(predicates, constraints)
    else:
        composed = predicate
    
    # 转换为条件格式
    return _predicate_to_condition(composed, empty=empty)
```

### 3.3 `_predicate_to_condition()`：类型分发

这个函数根据谓词的实际类型进行分发：

```python
# 文件: altair/vegalite/v6/api.py, 行 633-659
def _predicate_to_condition(
    predicate: _PredicateType, *, empty: Optional[bool] = Undefined
) -> _Condition:
    condition: _Condition
    
    # 1. Parameter 类型（选择参数）
    if isinstance(predicate, Parameter):
        predicate_expr = _get_predicate_expr(predicate)
        if predicate.param_type == "selection" or utils.is_undefined(predicate_expr):
            # 转换为 param 引用格式
            condition = {"param": predicate.name}
            if isinstance(empty, bool):
                condition["empty"] = empty
            elif isinstance(predicate.empty, bool):
                condition["empty"] = predicate.empty
        else:
            # 转换为 test 表达式
            condition = {"test": predicate_expr}
    
    # 2. TestPredicateType（字符串、表达式、谓词组合）
    elif _is_test_predicate(predicate):
        condition = {"test": predicate}
    
    # 3. 字典类型（已格式化的条件）
    elif isinstance(predicate, dict):
        condition = predicate
    
    # 4. OperatorMixin（表达式对象，如 datum.x > 0）
    elif isinstance(predicate, _expr_core.OperatorMixin):
        condition = {"test": predicate._to_expr()}  # 关键：调用 _to_expr()
    
    else:
        msg = f"Expected a predicate, but got: {type(predicate).__name__!r}..."
        raise TypeError(msg)
    
    return condition
```

### 3.4 表达式的 `_to_expr()` 机制

`OperatorMixin` 类提供了表达式的构建和序列化：

```python
# 文件: altair/expr/core.py, 行 93-212
class OperatorMixin:
    def _to_expr(self) -> str:
        return repr(self)
    
    # 算术运算符
    def __add__(self, other):
        comp_value = BinaryExpression("+", self, other)
        return self._from_expr(comp_value)
    
    # 比较运算符
    def __eq__(self, other):
        comp_value = BinaryExpression("===", self, other)
        return self._from_expr(comp_value)
    
    def __gt__(self, other):
        comp_value = BinaryExpression(">", self, other)
        return self._from_expr(comp_value)
    
    # 逻辑运算符
    def __and__(self, other):
        comp_value = BinaryExpression("&&", self, other)
        return self._from_expr(comp_value)
    
    def __or__(self, other):
        comp_value = BinaryExpression("||", self, other)
        return self._from_expr(comp_value)
    
    def __invert__(self):
        comp_value = UnaryExpression("!", self)
        return self._from_expr(comp_value)
```

**BinaryExpression 的 `__repr__` 方法**：

```python
# 文件: altair/expr/core.py, 行 246-251
class BinaryExpression(Expression):
    def __init__(self, op, lhs, rhs) -> None:
        super().__init__(op=op, lhs=lhs, rhs=rhs)
    
    def __repr__(self):
        return f"({_js_repr(self.lhs)} {self.op} {_js_repr(self.rhs)})"
```

**`_js_repr()` 函数**确保 JavaScript 兼容的字符串表示：

```python
# 文件: altair/expr/core.py, 行 37-52
def _js_repr(val) -> str:
    """Return a javascript-safe string representation of val."""
    if val is True:
        return "true"
    elif val is False:
        return "false"
    elif val is None:
        return "null"
    elif isinstance(val, OperatorMixin):
        return val._to_expr()  # 递归处理嵌套表达式
    elif isinstance(val, dt.date):
        return _from_date_datetime(val)
    # ... 其他类型处理
    else:
        return repr(val)
```

### 3.5 完整示例：谓词解析流程

```python
import altair as alt
from altair import datum

# 复杂谓词
predicate = (datum.age > 18) & (datum.category == "A") | ~(datum.score < 60)

# 实际的类型层次
# BinaryExpression (op="||")
# ├── lhs: BinaryExpression (op="&&")
# │   ├── lhs: BinaryExpression (op=">", lhs=GetAttrExpression("datum", "age"), rhs=18)
# │   └── rhs: BinaryExpression (op="===", lhs=GetAttrExpression("datum", "category"), rhs="A")
# └── rhs: UnaryExpression (op="!")
#     └── arg: BinaryExpression (op="<", lhs=GetAttrExpression("datum", "score"), rhs=60)

# 调用 _to_expr()
expr_str = predicate._to_expr()
# 结果: "((datum.age > 18) && (datum.category === 'A')) || (!(datum.score < 60))"
```

### 3.6 PredicateComposition 的 to_dict()

`PredicateComposition` 类处理逻辑组合的谓词对象：

```python
# 文件: altair/vegalite/v6/schema/core.py, 行 16309-16319
class PredicateComposition(VegaLiteSchema):
    """PredicateComposition schema wrapper."""
    
    _schema = {"$ref": "#/definitions/PredicateComposition"}
    
    def __invert__(self) -> PredicateComposition:
        return PredicateComposition({"not": self.to_dict()})
```

**测试用例验证**（来自 `test_api.py`）：

```python
# 文件: tests/vegalite/v6/test_api.py, 行 1267-1301
def test_filter_transform_selection_predicates():
    selector1 = alt.selection_interval(name="s1")
    selector2 = alt.selection_interval(name="s2")
    base = alt.Chart("data.txt").mark_point()
    
    # 单个选择参数
    chart = base.transform_filter(selector1)
    assert chart.to_dict()["transform"] == [{"filter": {"param": "s1"}}]
    
    # 取反
    chart = base.transform_filter(~selector1)
    assert chart.to_dict()["transform"] == [{"filter": {"not": {"param": "s1"}}}]
    
    # 与组合
    chart = base.transform_filter(selector1 & selector2)
    assert chart.to_dict()["transform"] == [
        {"filter": {"and": [{"param": "s1"}, {"param": "s2"}]}}
    ]
    
    # 或组合
    chart = base.transform_filter(selector1 | selector2)
    assert chart.to_dict()["transform"] == [
        {"filter": {"or": [{"param": "s1"}, {"param": "s2"}]}}
    ]
```

---

## 4. 规格校验路径：Schema 校验触发机制

### 4.1 校验触发时机

校验在 `to_dict()` 的最后阶段触发，且**仅在顶层调用时默认启用**：

```python
# 文件: altair/utils/schemapi.py, 行 1220-1227
result = _todict(kwds, context=context, **opts)

if validate:  # 默认 validate=True
    try:
        self.validate(result)
    except jsonschema.ValidationError as err:
        # 包装为更友好的错误
        raise SchemaValidationError(self, err) from None
return result
```

### 4.2 `validate()` 方法实现

```python
# 文件: altair/utils/schemapi.py, 行 1337-1347
@classmethod
def validate(
    cls, instance: dict[str, Any], schema: dict[str, Any] | None = None
) -> None:
    """Validate the instance against the class schema..."""
    if schema is None:
        schema = cls._schema
    assert schema is not None
    validate_jsonschema(instance, schema, rootschema=cls._rootschema or cls._schema)
```

### 4.3 `validate_jsonschema()` 核心校验逻辑

```python
# 文件: altair/utils/schemapi.py, 行 124-159
def validate_jsonschema(
    spec,
    schema: dict[str, Any],
    rootschema: dict[str, Any] | None = None,
    *,
    raise_error: bool = True,
) -> jsonschema.exceptions.ValidationError | None:
    # 获取所有错误
    errors = _get_errors_from_spec(spec, schema, rootschema=rootschema)
    
    if errors:
        # 错误处理：去重、分组、优先级排序
        leaf_errors = _get_leaves_of_error_tree(errors)
        grouped_errors = _group_errors_by_json_path(leaf_errors)
        grouped_errors = _subset_to_most_specific_json_paths(grouped_errors)
        grouped_errors = _deduplicate_errors(grouped_errors)
        
        # 选择主错误用于抛出
        main_error: Any = next(iter(grouped_errors.values()))[0]
        # 附加所有错误信息
        main_error._all_errors = grouped_errors
        
        if raise_error:
            raise main_error
        else:
            return main_error
    else:
        return None
```

### 4.4 `_get_errors_from_spec()`：实际的校验执行

```python
# 文件: altair/utils/schemapi.py, 行 162-206
def _get_errors_from_spec(
    spec: dict[str, Any],
    schema: dict[str, Any],
    rootschema: dict[str, Any] | None = None,
) -> ValidationErrorList:
    # 1. 获取合适的 jsonschema 验证器类
    json_schema_draft_url = _get_json_schema_draft_url(rootschema or schema)
    validator_cls = jsonschema.validators.validator_for(
        {"$schema": json_schema_draft_url}
    )
    
    # 2. 准备引用解析（处理 $ref）
    validator_kwargs: dict[str, Any] = {}
    
    if _use_referencing_library():
        # 新版本 jsonschema 使用 referencing 库
        schema = _prepare_references_in_schema(schema)
        validator_kwargs["registry"] = _get_referencing_registry(
            rootschema or schema, json_schema_draft_url
        )
    else:
        # 旧版本使用 RefResolver
        validator_kwargs["resolver"] = (
            jsonschema.RefResolver.from_schema(rootschema)
            if rootschema is not None
            else None
        )
    
    # 3. 创建验证器并迭代获取所有错误
    validator = validator_cls(schema, **validator_kwargs)
    errors = list(validator.iter_errors(spec))  # 关键：收集所有错误
    return errors
```

### 4.5 数组校验的工作原理

当验证 `transform` 数组时，jsonschema 会自动遍历每一项：

```python
# Vega-Lite schema 中 transform 的定义（简化）
transform_schema = {
    "type": "array",
    "items": {
        "anyOf": [
            {"$ref": "#/definitions/AggregateTransform"},
            {"$ref": "#/definitions/FilterTransform"},
            {"$ref": "#/definitions/WindowTransform"},
            # ... 其他变换类型
        ]
    }
}
```

**校验流程**：

```
spec = {"transform": [
    {"filter": {"test": "datum.x > 0"}},      # 项 0
    {"aggregate": [...], "groupby": ["cat"]},  # 项 1
    {"unknown_key": "invalid"}                  # 项 2（无效）
]}

validator.iter_errors(spec)
│
└── 发现 transform 是数组，检查每一项
    │
    ├── 项 0: {"filter": ...}
    │   └── 匹配 FilterTransform 定义 ✓
    │
    ├── 项 1: {"aggregate": ...}
    │   └── 匹配 AggregateTransform 定义 ✓
    │
    └── 项 2: {"unknown_key": ...}
        └── 不匹配 anyOf 中的任何定义 ✗
            └── 产生 ValidationError
                - path: ["transform", 2]
                - message: "{'unknown_key': 'invalid'} is not valid under any of the given schemas"
```

### 4.6 错误增强：`SchemaValidationError`

原始的 `jsonschema.ValidationError` 被包装为更友好的 `SchemaValidationError`：

```python
# 文件: altair/utils/schemapi.py, 行 626-692
class SchemaValidationError(jsonschema.ValidationError):
    _JS_TO_PY: ClassVar[Mapping[str, str]] = {
        "boolean": "bool",
        "integer": "int",
        "number": "float",
        "string": "str",
        "null": "None",
        "object": "Mapping[str, Any]",
        "array": "Sequence",
    }
    
    def __init__(self, obj: SchemaBase, err: jsonschema.ValidationError) -> None:
        super().__init__(**err._contents())
        self.obj = obj
        self._errors: GroupedValidationErrors = getattr(
            err, "_all_errors", {getattr(err, "json_path", _json_path(err)): [err]}
        )
        self._original_message = self.message
        self.message = self._get_message()  # 增强错误消息
    
    def _get_message(self) -> str:
        # 处理多错误情况
        error_messages: list[str] = []
        for errors in list(self._errors.values())[:3]:
            error_messages.append(self._get_message_for_errors_group(errors))
        
        # 格式化消息
        if len(error_messages) > 1:
            message = "Multiple errors were found.\n\n"
            # ... 格式化多错误
        else:
            message = error_messages[0]
        return message
```

### 4.7 特殊错误处理：`additionalProperties`

对于未知参数，提供特别友好的错误消息：

```python
# 文件: altair/utils/schemapi.py, 行 709-728
def _get_additional_properties_error_message(
    self,
    error: jsonschema.exceptions.ValidationError,
) -> str:
    # 1. 获取对应的 Altair 类
    altair_cls = self._get_altair_class_for_error(error)
    
    # 2. 获取该类的所有参数签名
    param_dict_keys = inspect.signature(altair_cls).parameters.keys()
    
    # 3. 格式化为表格显示
    param_names_table = self._format_params_as_table(param_dict_keys)
    
    # 4. 提取未知参数名
    parameter_name = error.message.split("('")[-1].split("'")[0]
    
    # 5. 构建友好的错误消息
    message = f"""\
`{altair_cls.__name__}` has no parameter named '{parameter_name}'

Existing parameter names are:
{param_names_table}
See the help for `{altair_cls.__name__}` to read the full description of these parameters"""
    return message
```

---

## 5. 与运行时的边界：Altair 层 vs Vega-Lite 运行时

### 5.1 职责边界概览

| 层级 | 能捕获的问题 | 不能捕获的问题 |
|------|-------------|---------------|
| **Altair (Python 层)** | 结构错误、类型错误、参数名错误、枚举值错误 | 字段存在性、变换顺序语义、数据运行时行为 |
| **Vega-Lite (浏览器运行时)** | 数据语义、字段引用、变换执行 | 结构合法性（已通过 Altair 校验） |

### 5.2 Altair 层能捕获的错误

#### 5.2.1 结构和类型错误

```python
# 无效参数名：Altair 会捕获
chart = alt.Chart().mark_point().encode(
    x=alt.X("field:Q", unknown_param=123)  # 错误！unknown_param 不存在
)
# SchemaValidationError: `X` has no parameter named 'unknown_param'
# Existing parameter names are:
#   aggregate   field    sort     type
#   bin         legend   scale
#   ...
```

#### 5.2.2 枚举值错误

```python
# 无效枚举值：Altair 会捕获
chart = alt.Chart().mark_point().encode(
    y=alt.Y("sum(value):Q", stack="invalid_value")  # 错误！无效的 stack 值
)
# SchemaValidationError: 'invalid_value' is an invalid value for `stack`
# Valid values are:
# - One of ['zero', 'center', 'normalize']
# - Of type `bool | None`
```

#### 5.2.3 类型不匹配

```python
# 类型错误：Altair 会捕获
chart = alt.Chart().mark_point().encode(
    x="field:Q",
    y=alt.Y(123)  # 错误！Y 通道需要字符串或字典
)
# SchemaValidationError: 123 is an invalid value...
```

### 5.3 Vega-Lite 运行时才能发现的问题

#### 5.3.1 字段引用错误

**大多数情况下，Altair 不检查字段是否存在**：

```python
import altair as alt
import pandas as pd

data = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})

# 引用不存在的字段：Altair 不会报错
chart = alt.Chart(data).mark_point().encode(
    x="a:Q",
    y="c:Q"  # 'c' 字段不存在！
)

# to_dict() 成功，无警告
spec = chart.to_dict()
# {"encoding": {"x": {"field": "a", "type": "quantitative"},
#               "y": {"field": "c", "type": "quantitative"}}}

# 但 Vega-Lite 运行时会显示空白图表
```

**例外：DataFrame 数据源的简单情况**

在某些情况下，当直接使用 DataFrame 且未进行复杂变换时，Altair 会检查字段：

```python
# 文件: tests/utils/test_schemapi.py, 行 905-915
@pytest.mark.parametrize("tp", [pd.DataFrame, pl.DataFrame])
def test_non_existent_column_name(tp: Callable[..., IntoDataFrame]) -> None:
    df = tp({"a": [1, 2], "b": [4, 5]})
    msg = (
        'Unable to determine data type for the field "c"; verify that the field name '
        "is not misspelled. If you are referencing a field from a transform, also "
        "confirm that the data type is specified correctly."
    )
    with pytest.raises(ValueError, match=msg):
        alt.Chart(df).mark_line().encode(x="a", y="c").to_json()
```

**注意**：这个检查仅在特定条件下触发，当使用 `transform` 创建新字段时不适用。

#### 5.3.2 变换顺序语义问题

**这是最重要的运行时问题**，Altair 完全不检查：

```python
import altair as alt
from altair import datum

data = pd.DataFrame({
    "category": ["A", "A", "B", "B"],
    "value": [10, 20, 30, 40]
})

# 问题场景：先聚合再过滤聚合前的字段
chart = (
    alt.Chart(data)
    .transform_aggregate(total="sum(value)", groupby=["category"])
    # 聚合后的数据只有: category, total
    # value 字段已不存在！
    .transform_filter(datum.value > 25)  # 静默失败！
)

# Altair 不报错
spec = chart.to_dict()
# {
#   "transform": [
#     {"aggregate": [...], "groupby": ["category"]},
#     {"filter": {"test": "datum.value > 25"}}  # value 不存在！
#   ]
# }

# Vega-Lite 运行时行为：
# - datum.value 在聚合后的行中不存在
# - 表达式求值为 null/undefined
# - null > 25 通常被视为 false（取决于 JavaScript 语义）
# - 结果：所有行被过滤掉，图表为空
```

#### 5.3.3 表达式运行时错误

```python
# 语法合法但运行时错误
chart = (
    alt.Chart(data)
    .transform_calculate(result="datum.value / datum.nonexistent")
)

# Altair 校验通过（字符串表达式不解析）
# Vega-Lite 运行时可能返回 NaN 或 Infinity
```

### 5.4 文档中的明确说明

Altair 官方文档明确说明了这种设计选择：

```
# 文件: doc/user_guide/display_frontends.rst, 行 416-420
Altair does not check whether fields are valid, because there are many avenues
by which a field can be specified within the full schema, and it is too difficult
to account for all corner cases. Improving the user experience in this is a
development priority...
```

### 5.5 两层的完整职责划分

| 方面 | Altair (Python) | Vega-Lite (运行时) |
|------|----------------|-------------------|
| **参数名检查** | ✓ 检查参数是否存在 | - |
| **类型检查** | ✓ 检查值类型是否匹配 | - |
| **枚举检查** | ✓ 检查枚举值是否合法 | - |
| **结构检查** | ✓ 检查对象结构完整性 | - |
| **字段存在性** | ✗ 仅在简单 DataFrame 情况下 | ✓ 运行时检查 |
| **变换顺序** | ✗ 无检查 | ✓ 按顺序执行，语义由运行时决定 |
| **表达式求值** | ✗ 不解析表达式字符串 | ✓ 实际执行表达式 |
| **数据依赖** | ✗ 不访问实际数据（除简单情况） | ✓ 处理实际数据 |

---

## 6. 完整执行流程示例

### 6.1 端到端示例

```python
import altair as alt
from altair import datum
import pandas as pd

# 准备数据
data = pd.DataFrame({
    "category": ["A", "A", "B", "B"],
    "value": [10, 20, 30, 40]
})

# 构建图表
chart = (
    alt.Chart(data)
    .mark_point()
    .transform_filter(datum.value > 15)           # 变换 1
    .transform_aggregate(
        total="sum(value)",
        groupby=["category"]
    )                                               # 变换 2
    .transform_window(
        pct="total / sum(total)",
        sort=[]
    )                                               # 变换 3
    .encode(
        x="category:N",
        y="total:Q",
        color="pct:Q"
    )
)

# 触发序列化和校验
spec = chart.to_dict()
```

### 6.2 执行时序图

```
用户调用 chart.to_dict()
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ 阶段 1: 准备                                                │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ 1. kwds = self._kwds.copy()                             │ │
│ │    包含: data, mark, transform (3项), encoding         │ │
│ │                                                          │ │
│ │ 2. 排除特殊键: shorthand, _cached_hash                  │ │
│ │                                                          │ │
│ │ 3. mark 字符串转换: "point" → {"type": "point"}        │ │
│ └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ 阶段 2: 递归序列化 (_todict)                                │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ 处理 transform 列表:                                      │ │
│ │                                                          │ │
│ │ 项 0: FilterTransform                                    │ │
│ │   └── to_dict(validate=False)                            │ │
│ │       └── {"filter": {"test": "(datum.value > 15)"}}   │ │
│ │                                                          │ │
│ │ 项 1: AggregateTransform                                 │ │
│ │   └── to_dict(validate=False)                            │ │
│ │       └── {"aggregate": [...], "groupby": ["category"]} │ │
│ │                                                          │ │
│ │ 项 2: WindowTransform                                    │ │
│ │   └── to_dict(validate=False)                            │ │
│ │       └── {"window": [...], "sort": []}                 │ │
│ │                                                          │ │
│ │ 处理 encoding:                                            │ │
│ │   └── x, y, color 通道的递归转换                         │ │
│ │                                                          │ │
│ │ 过滤所有 Undefined 值                                    │ │
│ └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ 阶段 3: Schema 校验 (validate=True)                         │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ 1. self.validate(result)                                 │ │
│ │    └── validate_jsonschema(result, Chart._schema, ...)  │ │
│ │                                                          │ │
│ │ 2. jsonschema 验证器检查:                                │ │
│ │    ├─ transform 数组的每一项                              │ │
│ │    │   ├─ [0] 匹配 FilterTransform ✓                     │ │
│ │    │   ├─ [1] 匹配 AggregateTransform ✓                 │ │
│ │    │   └─ [2] 匹配 WindowTransform ✓                    │ │
│ │    ├─ encoding 结构                                      │ │
│ │    └─ 其他顶层属性                                        │ │
│ │                                                          │ │
│ │ 3. 无错误 → 继续                                         │ │
│ │    有错误 → 包装为 SchemaValidationError 并抛出          │ │
│ └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ 阶段 4: 返回结果                                            │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ {                                                        │ │
│ │   "data": {"values": [...]},                            │ │
│ │   "mark": {"type": "point"},                            │ │
│ │   "transform": [                                         │ │
│ │     {"filter": {"test": "(datum.value > 15)"}},        │ │
│ │     {"aggregate": [...], "groupby": ["category"]},      │ │
│ │     {"window": [...], "sort": []}                        │ │
│ │   ],                                                     │ │
│ │   "encoding": {...}                                      │ │
│ │ }                                                        │ │
│ └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
   Vega-Lite 运行时
         │
         ▼
   实际执行变换
   (检查字段存在性、执行顺序语义等)
```

---

## 7. 代码位置汇总

| 功能 | 文件 | 行号 |
|------|------|------|
| `SchemaBase.to_dict()` 核心方法 | `altair/utils/schemapi.py` | 1174-1227 |
| `_todict()` 递归转换函数 | `altair/utils/schemapi.py` | 526-559 |
| `UndefinedType` 单例定义 | `altair/utils/schemapi.py` | 967-983 |
| `_parse_when()` 谓词解析 | `altair/vegalite/v6/api.py` | 868-889 |
| `_predicate_to_condition()` 类型分发 | `altair/vegalite/v6/api.py` | 633-659 |
| `OperatorMixin` 表达式混合类 | `altair/expr/core.py` | 93-212 |
| `_js_repr()` JavaScript 兼容转换 | `altair/expr/core.py` | 37-52 |
| `validate_jsonschema()` 校验入口 | `altair/utils/schemapi.py` | 124-159 |
| `_get_errors_from_spec()` 错误收集 | `altair/utils/schemapi.py` | 162-206 |
| `SchemaValidationError` 错误包装 | `altair/utils/schemapi.py` | 626-692 |
| 变换方法测试用例 | `tests/vegalite/v6/test_api.py` | 1141+ |
| 校验错误测试用例 | `tests/utils/test_schemapi.py` | 874+ |

---

## 8. 总结

### 8.1 序列化机制核心要点

1. **递归转换**：`_todict()` 递归处理各种类型，包括 SchemaBase 子类、列表、字典等

2. **Undefined 过滤**：在字典转换阶段过滤掉值为 `Undefined` 的键，实现"用户未指定则不输出"的语义

3. **子对象禁用校验**：递归转换子对象时使用 `validate=False`，仅在顶层进行一次完整校验

4. **惰性标记转换**：字符串类型的 `mark` 值（如 `"point"`）在序列化时转换为 `{"type": "point"}`

### 8.2 类型解析机制核心要点

1. **联合类型支持**：`predicate` 参数支持 `Parameter`、字符串、`Expression`、`OperatorMixin`、`PredicateComposition`、字典等多种类型

2. **类型分发**：`_predicate_to_condition()` 根据实际类型进行分发，转换为统一的 `{"test": ...}` 或 `{"param": ...}` 格式

3. **表达式构建**：`OperatorMixin` 重载运算符，构建 `BinaryExpression`、`UnaryExpression` 等表达式对象

4. **表达式序列化**：`_to_expr()` 方法（通过 `__repr__`）将表达式对象转换为 JavaScript 兼容的字符串

### 8.3 规格校验机制核心要点

1. **默认启用校验**：`to_dict()` 默认 `validate=True`，子对象递归时 `validate=False`

2. **jsonschema 集成**：使用 `jsonschema` 库进行实际的 schema 校验，支持 Draft-07

3. **引用解析**：处理 Vega-Lite schema 中的 `$ref` 引用，支持新旧版本的 jsonschema 库

4. **错误增强**：原始的 `jsonschema.ValidationError` 被包装为更友好的 `SchemaValidationError`，提供参数列表表格、类型映射等增强信息

5. **数组自动遍历**：jsonschema 自动遍历数组中的每一项，检查是否匹配 `items` schema

### 8.4 与运行时的边界要点

1. **Altair 职责**：结构校验、类型校验、参数名校验、枚举值校验。这些是静态的、不依赖实际数据的检查。

2. **Vega-Lite 职责**：字段存在性检查、变换顺序语义、表达式求值、实际数据处理。这些是动态的、依赖运行时数据的检查。

3. **字段检查的例外**：在简单的 DataFrame 情况下，Altair 会检查字段是否存在。但当使用 `transform` 创建新字段时，此检查不适用。

4. **变换顺序无静态检查**：Altair 完全不检查变换顺序的语义正确性，这是用户需要自行理解和测试的部分。

### 8.5 对用户的启示

1. **利用 Altair 的静态校验**：参数名错误、类型错误、枚举值错误会被 Altair 快速捕获，善用这一点进行早期错误检测

2. **警惕字段引用**：特别是在使用 `transform_aggregate` 等改变数据结构的变换后，注意后续变换引用的字段是否仍然存在

3. **测试变换顺序**：由于 Altair 不提供顺序敏感性检查，建议使用小样本数据测试变换链的实际效果

4. **空白图表的常见原因**：如果图表显示为空白但 `to_dict()` 未报错，首先检查：
   - 字段名是否拼写错误
   - 变换顺序是否导致字段不存在
   - 过滤条件是否过于严格（或因字段不存在而静默失败）
