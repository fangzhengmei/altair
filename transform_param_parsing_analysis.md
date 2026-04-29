# Altair 变换参数解析机制分析报告

## 1. 概述

本文档是前两篇分析报告（[transform_chain_analysis.md](./transform_chain_analysis.md) 和 [transform_serialization_analysis.md](./transform_serialization_analysis.md)）的补充，聚焦于**参数解析机制**这一关键细节：

1. **速记字符串语法解析**：如 `mean_acc="mean(Acceleration)"` 如何被解析为结构化变换定义
2. **谓词参数类型分发**：`transform_filter` 如何根据输入类型生成不同的 JSON 结构（表达式字符串、参数引用、逻辑组合对象）

---

## 2. 速记字符串语法解析机制

### 2.1 核心解析函数：`parse_shorthand()`

`parse_shorthand()` 是整个解析系统的核心，位于 `altair/utils/core.py:526-710`。它支持多种速记格式：

```python
def parse_shorthand(
    shorthand: dict[str, Any] | str,
    data: IntoDataFrame | None = None,
    parse_aggregates: bool = True,      # 是否解析聚合操作
    parse_window_ops: bool = False,     # 是否解析窗口操作
    parse_timeunits: bool = True,       # 是否解析时间单元
    parse_types: bool = True,            # 是否解析类型码
) -> dict[str, Any]:
```

**支持的速记格式**：

| 输入 | 解析结果 |
|------|---------|
| `"field_name"` | `{"field": "field_name"}` |
| `"field_name:Q"` | `{"field": "field_name", "type": "quantitative"}` |
| `"mean(Acceleration)"` | `{"aggregate": "mean", "field": "Acceleration"}` |
| `"min(foo):Q"` | `{"aggregate": "min", "field": "foo", "type": "quantitative"}` |
| `"month(col)"` | `{"timeUnit": "month", "field": "col", "type": "temporal"}` |
| `"count()"` | `{"aggregate": "count", "type": "quantitative"}` |

### 2.2 正则表达式模式构建

解析器使用**动态构建的正则表达式**匹配不同的速记格式。首先定义了基本的正则片段：

```python
# 文件: altair/utils/core.py, 行 82-225
AGGREGATES = [
    "argmax", "argmin", "average", "count", "distinct",
    "max", "mean", "median", "min", "missing", "product",
    "q1", "q3", "ci0", "ci1", "stderr", "stdev", "stdevp",
    "sum", "valid", "values", "variance", "variancep",
    "exponential", "exponentialb"
]

WINDOW_AGGREGATES = [
    "row_number", "rank", "dense_rank", "percent_rank",
    "cume_dist", "ntile", "lag", "lead",
    "first_value", "last_value", "nth_value"
]

TIMEUNITS = [
    "year", "quarter", "month", "week", "day", "dayofyear",
    "date", "hours", "minutes", "seconds", "milliseconds",
    "yearquarter", "yearquartermonth", "yearmonth",
    # ... 更多组合形式
]

SHORTHAND_UNITS = {
    "field": "(?P<field>.*)",
    "type": "(?P<type>{})".format("|".join(VALID_TYPECODES)),
    "agg_count": "(?P<aggregate>count)",
    "op_count": "(?P<op>count)",
    "aggregate": "(?P<aggregate>{})".format("|".join(AGGREGATES)),
    "window_op": "(?P<op>{})".format("|".join(AGGREGATES + WINDOW_AGGREGATES)),
    "timeUnit": "(?P<timeUnit>{})".format("|".join(TIMEUNITS)),
}
```

**模式优先级**：根据解析选项动态构建匹配模式，优先级从高到低：

```python
# 文件: altair/utils/core.py, 行 633-652
patterns = []

if parse_aggregates:
    patterns.extend([r"{agg_count}\(\)"])        # count() - 特殊处理
    patterns.extend([r"{aggregate}\({field}\)"])  # mean(field), sum(field) 等
if parse_window_ops:
    patterns.extend([r"{op_count}\(\)"])
    patterns.extend([r"{window_op}\({field}\)"])   # row_number(), sum(field) 等
if parse_timeunits:
    patterns.extend([r"{timeUnit}\({field}\)"])    # month(field), year(field) 等

patterns.extend([r"{field}"])  # 纯字段名

if parse_types:
    # 类型码版本优先级高于非类型码版本
    patterns = list(itertools.chain(*((p + ":{type}", p) for p in patterns)))

# 编译为正则表达式
regexps = (
    re.compile(r"\A" + p.format(**SHORTHAND_UNITS) + r"\Z", re.DOTALL)
    for p in patterns
)
```

**匹配执行**：使用 `next()` 找到第一个匹配的模式：

```python
# 文件: altair/utils/core.py, 行 654-662
if isinstance(shorthand, dict):
    attrs = shorthand
else:
    attrs = next(
        exp.match(shorthand).groupdict()
        for exp in regexps
        if exp.match(shorthand) is not None
    )
```

### 2.3 不同变换类型的解析选项差异

不同的 `transform_*` 方法使用**不同的解析选项组合**，这决定了哪些语法被识别：

| 变换方法 | `parse_aggregates` | `parse_window_ops` | `parse_timeunits` | `parse_types` |
|---------|---------------------|-------------------|-------------------|---------------|
| `transform_aggregate` | `True` | `False` | `False` | 默认 |
| `transform_window` | `False` | `True` | `False` | `False` |
| `transform_timeunit` | `False` | `False` | `True` | `False` |

#### 2.3.1 transform_aggregate 的解析方式

```python
# 文件: altair/vegalite/v6/api.py, 行 2754-2767
if aggregate is Undefined:
    aggregate = []
for key, val in kwds.items():
    # 关键：使用默认解析选项，parse_aggregates=True
    parsed = utils.parse_shorthand(val)
    dct = {
        "as": key,           # 关键字参数名作为输出别名
        "field": parsed.get("field", Undefined),
        "op": parsed.get("aggregate", Undefined),  # 注意："aggregate" → "op"
    }
    assert isinstance(aggregate, list)
    aggregate.append(core.AggregatedFieldDef(**dct))
```

**示例**：

```python
chart = alt.Chart().transform_aggregate(
    mean_acc="mean(Acceleration)",
    total="sum(value)",
    groupby=["Origin"]
)

# 解析过程：
# "mean(Acceleration)" 
#   → parse_shorthand 返回 {"aggregate": "mean", "field": "Acceleration"}
#   → dct = {"as": "mean_acc", "field": "Acceleration", "op": "mean"}
#
# "sum(value)"
#   → parse_shorthand 返回 {"aggregate": "sum", "field": "value"}
#   → dct = {"as": "total", "field": "value", "op": "sum"}

# 最终 JSON 输出：
# {
#   "aggregate": [
#     {"op": "mean", "field": "Acceleration", "as": "mean_acc"},
#     {"op": "sum", "field": "value", "as": "total"}
#   ],
#   "groupby": ["Origin"]
# }
```

#### 2.3.2 transform_window 的解析方式

```python
# 文件: altair/vegalite/v6/api.py, 行 3772-3795
w = window if isinstance(window, list) else []
if kwargs:
    for as_, shorthand in kwargs.items():
        kwds: dict[str, Any] = {"as": as_}
        kwds.update(
            utils.parse_shorthand(
                shorthand,
                parse_aggregates=False,   # 关键：禁用聚合
                parse_window_ops=True,    # 关键：启用窗口操作
                parse_timeunits=False,
                parse_types=False,
            )
        )
        # 注意：这里直接使用 "op" 键，不需要重映射
        w.append(core.WindowFieldDef(**kwds))
```

**关键差异**：
- `parse_aggregates=False` + `parse_window_ops=True`
- 结果中使用 `"op"` 键而非 `"aggregate"`
- `parse_shorthand` 返回的键名已经是 `"op"`

**示例**：

```python
chart = alt.Chart().transform_window(
    ycuml="sum(y)",           # 普通聚合
    rn="row_number()",        # 窗口专属操作
    groupby=["category"]
)

# 解析过程：
# "sum(y)" 
#   → parse_shorthand(parse_window_ops=True) 
#   → 返回 {"op": "sum", "field": "y"}
#
# "row_number()"
#   → parse_shorthand(parse_window_ops=True)
#   → 返回 {"op": "row_number"}  # 无 field

# 最终 JSON 输出：
# {
#   "window": [
#     {"op": "sum", "field": "y", "as": "ycuml"},
#     {"op": "row_number", "as": "rn"}
#   ],
#   "groupby": ["category"],
#   "frame": [null, 0]  # 默认值
# }
```

#### 2.3.3 transform_timeunit 的解析方式

```python
# 文件: altair/vegalite/v6/api.py, 行 3678-3693
for as_, shorthand in kwargs.items():
    dct = utils.parse_shorthand(
        shorthand,
        parse_timeunits=True,    # 关键：启用时间单元
        parse_aggregates=False,  # 禁用聚合
        parse_types=False,
    )
    dct.pop("type", None)        # 移除自动推断的 type
    dct["as"] = as_               # 设置输出别名
    if "timeUnit" not in dct:
        msg = f"'{shorthand}' must include a valid timeUnit"
        raise ValueError(msg)
    self = self._add_transform(core.TimeUnitTransform(**dct))
```

**示例**：

```python
chart = alt.Chart().transform_timeunit(
    month_date="month(date)",
    year_week="yearweek(timestamp)"
)

# 解析过程：
# "month(date)"
#   → parse_shorthand(parse_timeunits=True)
#   → 返回 {"timeUnit": "month", "field": "date", "type": "temporal"}
#   → dct.pop("type", None) 后
#   → {"timeUnit": "month", "field": "date", "as": "month_date"}

# 最终 JSON 输出：
# {
#   "timeUnit": "month",
#   "field": "date",
#   "as": "month_date"
# }
```

### 2.4 键名重映射机制

注意到一个重要细节：**聚合操作中，`parse_shorthand` 返回 `"aggregate"` 键，但最终 JSON 使用 `"op"` 键**。

```python
# transform_aggregate 中的重映射
dct = {
    "as": key,
    "field": parsed.get("field", Undefined),
    "op": parsed.get("aggregate", Undefined),  # "aggregate" → "op"
}
```

**为什么需要这个重映射？**

查看 `AggregatedFieldDef` 的 schema 定义：

```python
# AggregatedFieldDef 的字段名是 "op", "field", "as"
# 但 parse_shorthand 返回 "aggregate" 作为键名
#
# 这是因为 parse_shorthand 是为 encoding 通道设计的，
# 在 encoding 中使用 {"aggregate": "sum", "field": "x"}
# 但在 transform 的 AggregatedFieldDef 中使用 {"op": "sum", ...}
```

**对比**：

| 上下文 | parse_shorthand 返回 | 最终使用 |
|--------|----------------------|----------|
| Encoding 通道 | `{"aggregate": "sum", "field": "x"}` | 直接使用 |
| transform_aggregate | `{"aggregate": "sum", "field": "x"}` | 重映射为 `{"op": "sum", ...}` |
| transform_window | `{"op": "sum", "field": "x"}` | 直接使用（`parse_window_ops=True` 时） |

### 2.5 输出别名的来源

**速记字符串中的关键字参数名**决定了输出字段的别名：

```python
# 关键字参数名作为 "as" 字段的值
chart.transform_aggregate(
    output_alias="sum(original_field)",  # "output_alias" → {"as": "output_alias"}
)
```

这意味着：
1. **速记字符串**：`"sum(Acceleration)"` → 解析出 `op="sum"`, `field="Acceleration"`
2. **关键字参数名**：`mean_acc` → 成为 `"as": "mean_acc"`

---

## 3. 谓词参数的类型分发机制

### 3.1 谓词类型概览

`transform_filter` 的 `predicate` 参数支持**多种语义完全不同的输入类型**：

```python
# 文件: altair/vegalite/v6/api.py, 行 574-621
_TestPredicateType: TypeAlias = str | _expr_core.Expression | core.PredicateComposition
"""https://vega.github.io/vega-lite/docs/predicate.html"""

_PredicateType: TypeAlias = Union[
    Parameter,                    # 交互式选择参数
    core.Expr,                    # 表达式引用
    "_ConditionExtra",            # 条件对象
    _TestPredicateType,           # 测试谓词（字符串、Expression、PredicateComposition）
    _expr_core.OperatorMixin,     # 表达式运算符对象（datum.x > 0 等）
]
```

**不同谓词类型的输出格式差异巨大**：

| 输入类型 | 示例 | 最终 JSON 格式 |
|---------|------|----------------|
| **字符串** | `"datum.x > 0"` | `{"filter": {"test": "datum.x > 0"}}` |
| **OperatorMixin** | `datum.x > 0` | `{"filter": {"test": "(datum.x > 0)"}}` |
| **Parameter** | `selection = alt.selection_interval()` | `{"filter": {"param": "selection_name", "empty": true}}` |
| **PredicateComposition** | `FieldRangePredicate(...) & ...` | `{"filter": {"and": [...]}}` |
| **字典** | `{"test": "..."}` | 直接使用 |

### 3.2 核心分发函数：`_predicate_to_condition()`

这是所有谓词的**统一入口**，根据输入类型分发到不同的输出路径：

```python
# 文件: altair/vegalite/v6/api.py, 行 633-659
def _predicate_to_condition(
    predicate: _PredicateType, *, empty: Optional[bool] = Undefined
) -> _Condition:
    condition: _Condition
    
    # 路径 1: Parameter（交互式选择参数）
    if isinstance(predicate, Parameter):
        predicate_expr = _get_predicate_expr(predicate)
        # 检查是否为 selection 类型或无表达式
        if predicate.param_type == "selection" or utils.is_undefined(predicate_expr):
            # 输出: {"param": name, "empty": ...}
            condition = {"param": predicate.name}
            if isinstance(empty, bool):
                condition["empty"] = empty
            elif isinstance(predicate.empty, bool):
                condition["empty"] = predicate.empty
        else:
            # 有自定义表达式: {"test": expr}
            condition = {"test": predicate_expr}
    
    # 路径 2: TestPredicateType（字符串、Expression、PredicateComposition）
    elif _is_test_predicate(predicate):
        # 输出: {"test": predicate}
        condition = {"test": predicate}
    
    # 路径 3: 字典（已格式化的条件）
    elif isinstance(predicate, dict):
        # 直接使用
        condition = predicate
    
    # 路径 4: OperatorMixin（表达式运算符对象，如 datum.x > 0）
    elif isinstance(predicate, _expr_core.OperatorMixin):
        # 调用 _to_expr() 转为字符串表达式
        # 输出: {"test": "(datum.x > 0)"}
        condition = {"test": predicate._to_expr()}
    
    else:
        msg = (
            f"Expected a predicate, but got: {type(predicate).__name__!r}\n\n"
            f"From `predicate={predicate!r}`."
        )
        raise TypeError(msg)
    
    return condition
```

### 3.3 各路径详细分析

#### 路径 1：Parameter（交互式选择参数）

```python
# 示例
selection = alt.selection_interval(name="brush")
chart.transform_filter(selection)
```

**分发逻辑**：

```python
if isinstance(predicate, Parameter):
    predicate_expr = _get_predicate_expr(predicate)
    # 获取参数的 expr 属性（如果有的话）
    
    if predicate.param_type == "selection" or utils.is_undefined(predicate_expr):
        # 大多数情况：selection 类型
        condition = {"param": predicate.name}  # {"param": "brush"}
        # empty 参数处理
        if isinstance(empty, bool):
            condition["empty"] = empty
        elif isinstance(predicate.empty, bool):
            condition["empty"] = predicate.empty
    else:
        # 有自定义表达式的 Parameter
        condition = {"test": predicate_expr}
```

**最终输出**：

```json
{"filter": {"param": "brush", "empty": true}}
```

#### 路径 2：TestPredicateType（字符串、Expression、PredicateComposition）

```python
# 字符串示例
chart.transform_filter("datum.x > 0")

# Expression 示例
expr = alt.expr.datum.x > 0
chart.transform_filter(expr)

# PredicateComposition 示例
from altair import FieldRangePredicate
pred = FieldRangePredicate(field="year", range=[1950, 1960])
chart.transform_filter(pred)
```

**类型检查函数**：

```python
# 文件: altair/vegalite/v6/api.py, 行 624-625
def _is_test_predicate(obj: Any) -> TypeIs[_TestPredicateType]:
    return isinstance(obj, (str, _expr_core.Expression, core.PredicateComposition))
```

**输出格式**：

```json
// 字符串
{"filter": {"test": "datum.x > 0"}}

// PredicateComposition
{"filter": {"test": {"and": [{"range": ...}, ...]}}}
```

#### 路径 3：OperatorMixin（表达式运算符对象）

这是**最常用的路径**，对应 `datum.x > 0` 等表达式：

```python
# 示例
from altair import datum
chart.transform_filter(datum.age > 18)
chart.transform_filter((datum.x > 0) & (datum.y < 100))
```

**分发逻辑**：

```python
elif isinstance(predicate, _expr_core.OperatorMixin):
    condition = {"test": predicate._to_expr()}
```

**关键：`_to_expr()` 方法**（通过 `__repr__` 实现）：

```python
# 文件: altair/expr/core.py, 行 93-100
class OperatorMixin:
    def _to_expr(self) -> str:
        return repr(self)
```

**运算符重载**：

```python
# 文件: altair/expr/core.py, 行 101-140
def __add__(self, other):
    comp_value = BinaryExpression("+", self, other)
    return self._from_expr(comp_value)

def __eq__(self, other):
    comp_value = BinaryExpression("===", self, other)  # 注意：=== 而非 ==
    return self._from_expr(comp_value)

def __gt__(self, other):
    comp_value = BinaryExpression(">", self, other)
    return self._from_expr(comp_value)

def __and__(self, other):
    comp_value = BinaryExpression("&&", self, other)  # && 而非 and
    return self._from_expr(comp_value)

def __or__(self, other):
    comp_value = BinaryExpression("||", self, other)  # || 而非 or
    return self._from_expr(comp_value)

def __invert__(self):
    comp_value = UnaryExpression("!", self)  # ! 而非 not
    return self._from_expr(comp_value)
```

**BinaryExpression 的 `__repr__`**：

```python
# 文件: altair/expr/core.py, 行 246-251
class BinaryExpression(Expression):
    def __init__(self, op, lhs, rhs) -> None:
        super().__init__(op=op, lhs=lhs, rhs=rhs)
    
    def __repr__(self):
        return f"({_js_repr(self.lhs)} {self.op} {_js_repr(self.rhs)})"
```

**`_js_repr()` 确保 JavaScript 兼容性**：

```python
# 文件: altair/expr/core.py, 行 37-52
def _js_repr(val) -> str:
    if val is True:
        return "true"
    elif val is False:
        return "false"
    elif val is None:
        return "null"
    elif isinstance(val, OperatorMixin):
        return val._to_expr()  # 递归处理嵌套表达式
    # ... 其他类型
    else:
        return repr(val)
```

**完整示例**：

```python
from altair import datum

# 构建表达式
predicate = (datum.age > 18) & (datum.category == "A") | ~(datum.score < 60)

# 类型层次
# BinaryExpression(op="||")
# ├── lhs: BinaryExpression(op="&&")
# │   ├── lhs: BinaryExpression(op=">", lhs=GetAttrExpression("datum", "age"), rhs=18)
# │   └── rhs: BinaryExpression(op="===", lhs=GetAttrExpression("datum", "category"), rhs="A")
# └── rhs: UnaryExpression(op="!")
#     └── arg: BinaryExpression(op="<", lhs=GetAttrExpression("datum", "score"), rhs=60)

# _to_expr() 结果
predicate._to_expr()
# "((datum.age > 18) && (datum.category === 'A')) || (!(datum.score < 60))"
```

#### 路径 4：字典（直接使用）

```python
# 示例
chart.transform_filter({"test": "datum.x > 0"})
chart.transform_filter({"param": "my_selection"})
chart.transform_filter({"not": {"param": "brush"}})
```

**分发逻辑**：

```python
elif isinstance(predicate, dict):
    condition = predicate  # 直接返回，不做任何修改
```

**这意味着**：你可以直接传递符合 Vega-Lite 格式的字典，完全绕过所有解析逻辑。

### 3.4 谓词组合机制（and/or/not）

谓词可以通过 `&`, `|`, `~` 运算符组合。有**两条不同的组合路径**：

#### 路径 A：OperatorMixin 组合（表达式字符串）

```python
# 示例
from altair import datum
pred = (datum.x > 0) & (datum.y < 100)  # 返回 BinaryExpression
```

**最终输出**：

```json
{"filter": {"test": "((datum.x > 0) && (datum.y < 100))"}}
```

#### 路径 B：PredicateComposition 组合（嵌套对象）

```python
# 示例
from altair import FieldRangePredicate, FieldEqualPredicate

range_pred = FieldRangePredicate(field="year", range=[1950, 1960])
eq_pred = FieldEqualPredicate(field="sex", equal=1)

combined = range_pred & eq_pred  # 返回 PredicateComposition
```

**PredicateComposition 的运算符重载**：

```python
# 文件: altair/vegalite/v6/schema/core.py, 行 16317-16324
class PredicateComposition(VegaLiteSchema):
    def __invert__(self) -> PredicateComposition:
        return PredicateComposition({"not": self.to_dict()})
    
    def __and__(self, other: SchemaBase) -> PredicateComposition:
        return PredicateComposition({"and": [self.to_dict(), other.to_dict()]})
    
    def __or__(self, other: SchemaBase) -> PredicateComposition:
        return PredicateComposition({"or": [self.to_dict(), other.to_dict()]})
```

**最终输出**：

```json
{
  "filter": {
    "test": {
      "and": [
        {"range": {"year": [1950, 1960]}},
        {"equal": {"sex": 1}}
      ]
    }
  }
}
```

**关键差异总结**：

| 组合类型 | 输入示例 | 输出类型 | 最终 JSON |
|---------|----------|---------|-----------|
| **OperatorMixin** | `datum.x > 0 & datum.y < 100` | `BinaryExpression` | `"test": "((...) && (...))"` |
| **PredicateComposition** | `FieldRangePredicate(...) & ...` | `PredicateComposition` | `"test": {"and": [...]}` |

### 3.5 多谓词归约：`transform_filter` 的 `*args` 和 `**kwargs`

`transform_filter` 支持一种特殊的语法：**多个谓词通过位置参数自动用 `&` 连接**：

```python
# 示例
chart.transform_filter(
    datum.year > 1980,   # 位置参数 1
    datum.age != 90,     # 位置参数 2
    year=2000,           # 关键字参数约束
    sex=1
)
```

**入口点**：

```python
# 文件: altair/vegalite/v6/api.py, 行 3217-3229
if depr_filter := t.cast("Any", constraints.pop("filter", None)):
    # 处理废弃的 filter 关键字参数
    ...
else:
    # 解析谓词并组合
    cond = _parse_when(predicate, *more_predicates, empty=empty, **constraints)
    return self._add_transform(core.FilterTransform(filter=cond.get("test", cond)))
```

**`_parse_when()` 函数**：

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
        # 情况 1: 没有主谓词，但有 *args 或 **kwargs
        if more_predicates or constraints:
            composed = _parse_when_compose(more_predicates, constraints)
        else:
            raise TypeError("At least one predicate must be provided")
    
    elif more_predicates or constraints:
        # 情况 2: 有主谓词 + *args 或 **kwargs
        predicates = predicate, *more_predicates
        composed = _parse_when_compose(predicates, constraints)
    
    else:
        # 情况 3: 只有一个谓词，无需组合
        composed = predicate
    
    return _predicate_to_condition(composed, empty=empty)
```

**`_parse_when_compose()` - 核心组合逻辑**：

```python
# 文件: altair/vegalite/v6/api.py, 行 839-865
def _parse_when_compose(
    predicates: tuple[Any, ...],
    constraints: dict[str, _FieldEqualType],
    /,
) -> BinaryExpression:
    """Compose an `&` reduction predicate."""
    iters = []
    
    # 1. 验证位置参数谓词是否可组合
    if predicates:
        iters.append(_validate_composables(predicates))
    
    # 2. 将关键字参数转换为 datum.field == value 表达式
    if constraints:
        iters.append(_parse_when_constraints(constraints))
    
    # 3. 使用 functools.reduce 进行 & 归约
    r = functools.reduce(operator.and_, itertools.chain.from_iterable(iters))
    return t.cast("_expr_core.BinaryExpression", r)
```

**`_parse_when_constraints()` - 关键字参数转换**：

```python
# 文件: altair/vegalite/v6/api.py, 行 810-821
def _parse_when_constraints(
    constraints: dict[str, _FieldEqualType], /
) -> Iterator[_expr_core.GetAttrExpression]:
    """
    Convert `transform_filter(year=2000, sex=1)` 
    to `(datum.year == 2000) & (datum.sex == 1)`
    """
    for name, value in constraints.items():
        # 构建 GetAttrExpression 并调用 __eq__
        yield _expr_core.GetAttrExpression("datum", name) == value
```

**完整示例**：

```python
chart.transform_filter(
    datum.year > 1980,   # 位置参数 1
    datum.age != 90,     # 位置参数 2
    year=2000,           # 关键字参数 → datum.year == 2000
    sex=1                 # 关键字参数 → datum.sex == 1
)

# 组合过程：
# 1. 位置参数: [datum.year > 18, datum.age != 90]
# 2. 关键字参数: _parse_when_constraints({"year": 2000, "sex": 1})
#    → [datum.year == 2000, datum.sex == 1]
# 3. reduce(operator.and_, 所有谓词)
#    → ((datum.year > 18) & (datum.age != 90)) & (datum.year == 2000) & (datum.sex == 1)

# 最终输出：
# {"filter": {"test": "(((datum.year > 18) && (datum.age !== 90)) && (datum.year === 2000)) && (datum.sex === 1)"}}
```

### 3.6 FilterTransform 的最终构建

在 `transform_filter` 的最后一步，需要处理条件的格式：

```python
# 文件: altair/vegalite/v6/api.py, 行 3228-3229
cond = _parse_when(predicate, *more_predicates, empty=empty, **constraints)
return self._add_transform(core.FilterTransform(filter=cond.get("test", cond)))
```

**关键细节**：`cond.get("test", cond)`

这意味着：
- 如果 `cond` 是 `{"test": "..."}`，则提取 `"test"` 的值
- 否则直接使用 `cond`（如 `{"param": "brush"}` 或逻辑组合对象）

**为什么这样设计？**

查看 Vega-Lite 的 `FilterTransform` 定义：

```json
// FilterTransform 可以接受：
// 1. 字符串（测试表达式）
"filter": "datum.x > 0"

// 2. 参数引用对象
"filter": {"param": "selection"}

// 3. 逻辑组合对象
"filter": {"and": [...]}
```

**所以**：
- `{"test": "expr"}` → 需要提取为 `"expr"`（字符串形式）
- `{"param": "name"}` → 直接作为对象使用
- `{"and": [...]}` → 直接作为对象使用

---

## 4. 完整执行流程图

### 4.1 速记字符串解析流程

```
用户调用: chart.transform_aggregate(mean_acc="mean(Acceleration)")
         │
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│ transform_aggregate 方法                                              │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ 1. 遍历 **kwargs:                                                  │ │
│ │    key="mean_acc", val="mean(Acceleration)"                       │ │
│ │                                                                    │ │
│ │ 2. 调用 parse_shorthand(val):                                     │ │
│ │    ├── 构建正则表达式模式列表                                      │ │
│ │    │   - "{aggregate}({field})" (parse_aggregates=True)          │ │
│ │    │   - "{field}"                                                │ │
│ │    │   - 带类型码版本优先                                         │ │
│ │    │                                                              │ │
│ │    ├── 编译为正则:                                                │ │
│ │    │   r"\A(?P<aggregate>mean|sum|...)\((?P<field>.*)\)\Z"     │ │
│ │    │                                                              │ │
│ │    └── 匹配 "mean(Acceleration)":                                 │ │
│ │        → {"aggregate": "mean", "field": "Acceleration"}          │ │
│ │                                                                    │ │
│ │ 3. 重映射键名:                                                     │ │
│ │    dct = {                                                         │ │
│ │        "as": key,          # "mean_acc"                           │ │
│ │        "field": parsed.get("field"),  # "Acceleration"           │ │
│ │        "op": parsed.get("aggregate"),  # "mean" ← 重映射         │ │
│ │    }                                                               │ │
│ │                                                                    │ │
│ │ 4. 创建 AggregatedFieldDef:                                        │ │
│ │    core.AggregatedFieldDef(**dct)                                  │ │
│ └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│ 5. 创建 AggregateTransform 并调用 _add_transform                     │
└─────────────────────────────────────────────────────────────────────┘
         │
         ▼
    序列化输出:
    {
      "aggregate": [
        {"op": "mean", "field": "Acceleration", "as": "mean_acc"}
      ]
    }
```

### 4.2 谓词类型分发流程

```
用户调用: chart.transform_filter(datum.x > 0)
         │
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│ transform_filter 方法                                                 │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ 1. 调用 _parse_when(predicate, *more_predicates, **constraints) │ │
│ │                                                                    │ │
│ │    _parse_when 内部:                                              │ │
│ │    ├── 检查是否有多个谓词需要组合                                 │ │
│ │    └── 调用 _predicate_to_condition(composed, empty=empty)       │ │
│ └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│ _predicate_to_condition - 核心分发逻辑                               │
│                                                                      │
│ 类型判断分支（按优先级）:                                             │
│                                                                      │
│ ┌─ 1. isinstance(predicate, Parameter) ───────────────────────────┐│
│ │   │                                                                ││
│ │   ├─ 检查: predicate.param_type == "selection" 或无表达式       ││
│ │   │                                                                ││
│ │   ├─ 是 → {"param": name, "empty": ...}                         ││
│ │   │   输出: {"filter": {"param": "selection"}}                   ││
│ │   │                                                                ││
│ │   └─ 否 → {"test": predicate_expr}                               ││
│ │       输出: {"filter": {"test": "custom_expr"}}                  ││
│ └──────────────────────────────────────────────────────────────────┘│
│                                                                      │
│ ┌─ 2. _is_test_predicate(predicate) ──────────────────────────────┐│
│ │   (str, Expression, PredicateComposition)                         ││
│ │   │                                                                ││
│ │   └─ → {"test": predicate}                                        ││
│ │                                                                     ││
│ │   字符串示例:                                                       ││
│ │   输入: "datum.x > 0"                                             ││
│ │   输出: {"filter": {"test": "datum.x > 0"}}                      ││
│ │                                                                     ││
│ │   PredicateComposition 示例:                                      ││
│ │   输入: FieldRangePredicate(...) & ...                            ││
│ │   输出: {"filter": {"test": {"and": [...]}}}                      ││
│ └──────────────────────────────────────────────────────────────────┘│
│                                                                      │
│ ┌─ 3. isinstance(predicate, dict) ─────────────────────────────────┐│
│ │   │                                                                ││
│ │   └─ → 直接使用 predicate                                          ││
│ │                                                                     ││
│ │   示例:                                                             ││
│ │   输入: {"param": "my_sel", "empty": false}                       ││
│ │   输出: {"filter": {"param": "my_sel", "empty": false}}          ││
│ └──────────────────────────────────────────────────────────────────┘│
│                                                                      │
│ ┌─ 4. isinstance(predicate, OperatorMixin) ────────────────────────┐│
│ │   (datum.x > 0 等表达式对象)                                       ││
│ │   │                                                                ││
│ │   └─ → {"test": predicate._to_expr()}                             ││
│ │                                                                     ││
│ │   示例:                                                             ││
│ │   输入: datum.x > 0                                                ││
│ │   类型: GetAttrExpression.__gt__ 返回 BinaryExpression           ││
│ │         (继承自 OperatorMixin)                                     ││
│ │                                                                     ││
│ │   _to_expr() 过程:                                                 ││
│ │   ├── _to_expr() → return repr(self)                              ││
│ │   ├── BinaryExpression.__repr__() →                               ││
│ │   │   f"({_js_repr(lhs)} {op} {_js_repr(rhs)})"                 ││
│ │   ├── _js_repr(True) → "true"                                     ││
│ │   ├── _js_repr(GetAttrExpression) → 递归调用 _to_expr()          ││
│ │   └── 最终: "(datum.x > 0)"                                       ││
│ │                                                                     ││
│ │   输出: {"filter": {"test": "(datum.x > 0)"}}                     ││
│ └──────────────────────────────────────────────────────────────────┘│
│                                                                      │
│ ┌─ 5. 其他类型 ─────────────────────────────────────────────────────┐│
│ │   └─ → raise TypeError("Expected a predicate...")                 ││
│ └──────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 最后一步: cond.get("test", cond)                                     │
│                                                                      │
│ 目的: 处理 FilterTransform 的不同接受格式                            │
│                                                                      │
│ 情况 1: cond = {"test": "(datum.x > 0)"}                           │
│         → 提取: "(datum.x > 0)" （字符串）                          │
│         → FilterTransform(filter="(datum.x > 0)")                  │
│                                                                      │
│ 情况 2: cond = {"param": "brush", "empty": true}                   │
│         → 直接使用: {"param": "brush", "empty": true}（对象）      │
│         → FilterTransform(filter={"param": ...})                    │
│                                                                      │
│ 情况 3: cond = {"and": [...]} （PredicateComposition）              │
│         → 直接使用: {"and": [...]}（对象）                           │
│         → FilterTransform(filter={"and": [...]})                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 5. 代码位置汇总

| 功能 | 文件 | 行号 |
|------|------|------|
| `parse_shorthand()` 核心解析函数 | `altair/utils/core.py` | 526-710 |
| `SHORTHAND_UNITS` 正则片段定义 | `altair/utils/core.py` | 213-221 |
| `AGGREGATES` / `WINDOW_AGGREGATES` 列表 | `altair/utils/core.py` | 82-124 |
| `transform_aggregate` 方法 | `altair/vegalite/v6/api.py` | 2691-2767 |
| `transform_window` 方法 | `altair/vegalite/v6/api.py` | 3696-3795 |
| `transform_timeunit` 方法 | `altair/vegalite/v6/api.py` | 3613-3694 |
| `_predicate_to_condition()` 分发函数 | `altair/vegalite/v6/api.py` | 633-659 |
| `_parse_when()` 谓词解析入口 | `altair/vegalite/v6/api.py` | 868-889 |
| `_parse_when_compose()` 多谓词组合 | `altair/vegalite/v6/api.py` | 839-865 |
| `_parse_when_constraints()` 关键字参数转换 | `altair/vegalite/v6/api.py` | 810-821 |
| `OperatorMixin` 表达式混合类 | `altair/expr/core.py` | 93-140 |
| `BinaryExpression.__repr__()` | `altair/expr/core.py` | 246-251 |
| `_js_repr()` JavaScript 兼容转换 | `altair/expr/core.py` | 37-52 |
| `PredicateComposition` 逻辑组合类 | `altair/vegalite/v6/schema/core.py` | 16309-16324 |
| `transform_filter` 方法 | `altair/vegalite/v6/api.py` | 3123-3229 |

---

## 6. 核心设计要点总结

### 6.1 速记字符串解析设计要点

1. **动态正则模式**：根据解析选项动态构建匹配模式，优先级可控
   - 聚合/窗口/时间单元模式优先于纯字段名
   - 类型码版本（`:Q`, `:O` 等）优先于非类型码版本

2. **解析选项差异**：
   - `transform_aggregate`: `parse_aggregates=True`，键名从 `"aggregate"` 重映射为 `"op"`
   - `transform_window`: `parse_window_ops=True`，直接使用 `"op"` 键
   - `transform_timeunit`: `parse_timeunits=True`，自动移除 `type` 字段

3. **输出别名来源**：关键字参数名（如 `mean_acc=`）决定输出字段的 `"as"` 值

### 6.2 谓词类型分发设计要点

1. **多路径分发**：`_predicate_to_condition()` 按优先级检查 5 种类型
   - `Parameter`（最高优先级）→ `{"param": ...}`
   - `_TestPredicateType` → `{"test": ...}`
   - `dict` → 直接使用
   - `OperatorMixin` → `{"test": expr._to_expr()}`

2. **两种组合路径**：
   - **OperatorMixin 路径**：`datum.x > 0 & ...` → 字符串表达式
   - **PredicateComposition 路径**：`FieldRangePredicate(...) & ...` → 嵌套对象

3. **多谓词自动归约**：
   - 位置参数 `*more_predicates` 用 `&` 连接
   - 关键字参数 `**constraints` 转换为 `datum.field == value` 后再连接
   - 使用 `functools.reduce(operator.and_, ...)` 实现左结合归约

4. **最终格式适配**：`cond.get("test", cond)` 处理两种输出格式
   - 字符串表达式：提取 `"test"` 值
   - 对象格式：直接使用（参数引用、逻辑组合）

### 6.3 为什么同一入口能产生不同输出？

核心原因是 **Vega-Lite 的 FilterTransform 支持多种输入格式**，而 Altair 通过**类型分发**让用户可以用最自然的方式表达：

| 用户意图 | 自然表达方式 | 内部类型 | 最终输出 |
|---------|------------|---------|---------|
| 简单表达式过滤 | `"datum.x > 0"` | `str` | `{"filter": "datum.x > 0"}` |
| 类型安全的表达式 | `datum.x > 0` | `OperatorMixin` | `{"filter": "(datum.x > 0)"}` |
| 交互式选择 | `selection = alt.selection_interval()` | `Parameter` | `{"filter": {"param": "sel"}}` |
| 复杂谓词对象 | `FieldRangePredicate(...)` | `PredicateComposition` | `{"filter": {"range": ...}}` |
| 完全控制 | `{"test": "..."}` | `dict` | 直接使用 |

**这种设计的优势**：
- **用户友好**：可以用最直观的方式表达意图
- **类型安全**：OperatorMixin 路径提供 Python 级别的类型检查
- **灵活性**：字典路径允许完全绕过解析逻辑
- **向后兼容**：多种格式都支持
