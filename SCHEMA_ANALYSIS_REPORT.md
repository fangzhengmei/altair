# Altair Vega-Lite Schema 代码生成机制深度分析报告

## 1. 概述

Altair 通过一套完整的代码生成机制，将 Vega-Lite JSON Schema 自动转换为 Python 类。这个机制主要包含三个核心部分：

1. **运行时基类** (`altair/utils/schemapi.py`)：`SchemaBase` 类提供属性验证、序列化和反序列化功能
2. **代码生成器** (`tools/schemapi/`)：`SchemaInfo` 解析 schema，`SchemaGenerator` 生成 Python 代码
3. **主生成脚本** (`tools/generate_schema_wrapper.py`)：协调整个生成流程

---

## 2. SchemaBase 类的属性验证机制

### 2.1 核心属性

```python
class SchemaBase:
    _schema: ClassVar[dict[str, Any] | Any] = None
    _rootschema: ClassVar[dict[str, Any] | None] = None
    _class_is_valid_at_instantiation: ClassVar[bool] = True
```
- `_schema`：当前类对应的 JSON Schema（可能是 `$ref` 引用）
- `_rootschema`：根 Schema，用于解析 `$ref` 引用
- `_class_is_valid_at_instantiation`：控制是否在实例化时验证

### 2.2 初始化流程 (`__init__`)

**文件位置**: `altair/utils/schemapi.py:1077-1100`

```python
def __init__(self, *args: Any, **kwds: Any) -> None:
    if self._schema is None:
        raise ValueError(...)

    # 参数校验：要么关键字参数，要么位置参数
    if kwds:
        assert len(args) == 0
    else:
        assert len(args) in {0, 1}

    # 存储参数（使用 object.__setattr__ 避免触发 __setattr__）
    object.__setattr__(self, "_args", args)
    object.__setattr__(self, "_kwds", kwds)

    # DEBUG_MODE 下自动验证
    if DEBUG_MODE and self._class_is_valid_at_instantiation:
        self.to_dict(validate=True)
```

**关键设计点**：
- 参数存储在 `_args` 和 `_kwds` 中，而不是直接作为实例属性
- 使用 `object.__setattr__` 避免触发自定义的 `__setattr__`
- `DEBUG_MODE=True` 时在构造时自动调用 `to_dict(validate=True)`

### 2.3 属性访问机制

```python
def __getattr__(self, attr):
    # 从 _kwds 中获取属性
    if attr in self._kwds:
        return self._kwds[attr]
    # ...

def __setattr__(self, item, val) -> None:
    # 设置属性到 _kwds
    self._kwds[item] = val
```

**设计思想**：所有属性都通过 `_kwds` 字典间接访问，便于序列化和验证。

---

## 3. `to_dict` 序列化链详解

### 3.1 主流程

**文件位置**: `altair/utils/schemapi.py:1174-1225`

```python
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

    # 步骤1：确定数据源
    if self._args and not self._kwds:
        kwds = self._args[0]  # 单值模式（如 string、number）
    elif not self._args:
        kwds = self._kwds.copy()  # 对象模式
        # 过滤不需要的键
        exclude = {*ignore, "shorthand", "_cached_hash"}
        # 处理 shorthand 解析
        if parsed := context.pop("parsed_shorthand", None):
            kwds = _replace_parsed_shorthand(parsed, kwds)
        kwds = {k: v for k, v in kwds.items() if k not in exclude}
        # 字符串 mark 转换为 dict
        if (mark := kwds.get("mark")) and isinstance(mark, str):
            kwds["mark"] = {"type": mark}
    else:
        raise ValueError(...)

    # 步骤2：递归序列化
    result = _todict(kwds, context=context, **opts)

    # 步骤3：可选验证
    if validate:
        try:
            self.validate(result)
        except jsonschema.ValidationError as err:
            raise SchemaValidationError(self, err) from None
    return result
```

### 3.2 递归序列化函数 `_todict`

**文件位置**: `altair/utils/schemapi.py:526-559`

```python
def _todict(obj: Any, context: dict[str, Any] | None, np_opt: Any, pd_opt: Any) -> Any:
    # NumPy 类型处理
    if np_opt is not None:
        np = np_opt
        if isinstance(obj, np.ndarray):
            return [_todict(v, context, np_opt, pd_opt) for v in obj]
        elif isinstance(obj, np.number):
            return float(obj)
        elif isinstance(obj, np.datetime64):
            result = str(obj)
            if "T" not in result:
                result += "T00:00:00"
            return result

    # SchemaBase 子对象：递归调用 to_dict
    if isinstance(obj, SchemaBase):
        return obj.to_dict(validate=False, context=context)
    
    # 列表/元组：递归处理每个元素
    elif isinstance(obj, (list, tuple)):
        return [_todict(v, context, np_opt, pd_opt) for v in obj]
    
    # 字典：递归处理每个值，跳过 Undefined
    elif isinstance(obj, dict):
        return {
            k: _todict(v, context, np_opt, pd_opt)
            for k, v in obj.items()
            if v is not Undefined
        }
    
    # SchemaLike 协议对象
    elif isinstance(obj, SchemaLike):
        return obj.to_dict()
    
    # Pandas Timestamp
    elif pd_opt is not None and isinstance(obj, pd_opt.Timestamp):
        return pd_opt.Timestamp(obj).isoformat()
    
    # 其他可迭代对象
    elif _is_iterable(obj, exclude=(str, bytes)):
        return _todict(_from_array_like(obj), context, np_opt, pd_opt)
    
    # datetime.date/datetime
    elif isinstance(obj, dt.date):
        return _from_date_datetime(obj)
    
    # 其他类型直接返回
    else:
        return obj
```

### 3.3 序列化流程图

```
SchemaBase.to_dict(validate=True)
        │
        ▼
┌───────────────────┐
│ 1. 确定数据源    │
│    - _args[0]    │  单值模式（如 string、enum）
│    - _kwds.copy()│  对象模式
└───────────────────┘
        │
        ▼
┌───────────────────┐
│ 2. 预处理        │
│    - 过滤 Undefined
│    - 处理 shorthand
│    - mark 字符串转 dict
└───────────────────┘
        │
        ▼
┌───────────────────┐
│ 3. _todict 递归  │◄──────────────────┐
│    ├─ SchemaBase ─► to_dict(validate=False) │
│    ├─ list/tuple ─► 遍历递归           │
│    ├─ dict      ─► 遍历值，跳过 Undefined│
│    ├─ numpy 类型 ─► 转换为 Python 原生   │
│    ├─ pandas 类型─► 转换为 ISO 格式      │
│    └─ 其他类型   ─► 直接返回            │
└───────────────────┘
        │
        ▼
┌───────────────────┐
│ 4. 验证 (可选)   │
│    - validate_jsonschema
│    - SchemaValidationError 包装
└───────────────────┘
        │
        ▼
   返回 dict
```

---

## 4. Schema JSON 到 Python 类的代码生成过程

### 4.1 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                    tools/generate_schema_wrapper.py          │
│                      (主生成脚本 - vegalite_main)            │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌──────────────┐    ┌──────────────────┐    ┌──────────────────┐
│ 下载 Schema  │    │ 解析和生成代码    │    │ 生成辅助模块      │
│ vega-lite-   │    │                  │    │                  │
│ schema.json  │    │ - core.py        │    │ - _typing.py     │
└──────────────┘    │ - channels.py    │    │ - mixins.py      │
                    │ - __init__.py    │    │ - _config.py     │
                    └──────────────────┘    └──────────────────┘
```

### 4.2 核心生成流程

**文件位置**: `tools/generate_schema_wrapper.py:716-791`

```python
def generate_vegalite_schema_wrapper(fp: Path, /) -> ModuleDef[str]:
    basename = "VegaLiteSchema"
    rootschema = load_schema_with_shorthand_properties(fp)
    definitions: dict[str, SchemaGenerator] = {}
    graph: dict[str, list[str]] = {}  # 依赖关系图

    # 步骤1：为每个 definition 创建 SchemaGenerator
    for name in rootschema["definitions"]:
        defschema = {"$ref": "#/definitions/" + name}
        defschema_repr = {"$ref": "#/definitions/" + name}
        name = get_valid_identifier(name)
        
        # 检查是否有特殊覆盖（如 PredicateComposition）
        if overrides := CORE_OVERRIDES.get(name):
            tp = overrides["tp"]
            kwds = overrides["kwds"]
        else:
            tp = SchemaGenerator
            kwds = {}
        
        definitions[name] = tp(
            name,
            schema=defschema,
            schemarepr=defschema_repr,
            rootschema=rootschema,
            basename=basename,
            rootschemarepr=CodeSnippet(f"{basename}._rootschema"),
            **kwds,
        )

    # 步骤2：构建依赖关系图（用于拓扑排序）
    for name, schema in definitions.items():
        graph[name] = []
        for child_name in schema.subclasses():
            child_name = get_valid_identifier(child_name)
            graph[name].append(child_name)
            # 设置继承关系
            child: SchemaGenerator = definitions[child_name]
            if child.basename == basename:
                child.basename = [name]
            else:
                assert isinstance(child.basename, list)
                child.basename.append(name)

    # 步骤3：拓扑排序并生成代码
    contents = [
        HEADER,
        # ... 导入语句 ...
        LOAD_SCHEMA.format(schemafile=SCHEMA_FILE),
        BASE_SCHEMA.format(basename=basename),
        schema_class("Root", schema=rootschema, basename=basename, ...),
    ]

    # 按拓扑顺序生成每个类
    for name in toposort(graph):
        contents.append(definitions[name].schema_class())

    return ModuleDef("\n".join(contents), all_)
```

### 4.3 SchemaInfo 类：Schema 解析器

**文件位置**: `tools/schemapi/utils.py:360-973`

`SchemaInfo` 是核心的解析器，用于检查和分析 JSON Schema 的结构。

```python
class SchemaInfo:
    def __init__(
        self, schema: Mapping[str, Any], rootschema: Mapping[str, Any] | None = None
    ) -> None:
        object.__setattr__(self, "raw_schema", schema)
        object.__setattr__(self, "rootschema", rootschema)
        # 关键：构造时解析所有 $ref 引用
        object.__setattr__(self, "schema", resolve_references(schema, rootschema))
```

**核心属性**：
| 属性 | 说明 |
|------|------|
| `raw_schema` | 原始 schema（可能包含 `$ref`） |
| `rootschema` | 根 schema（用于解析引用） |
| `schema` | 解析后的 schema（`$ref` 已展开） |
| `title` | 从 `$ref` 提取的类名 |
| `properties` | SchemaProperties 包装器 |
| `anyOf` | Union 类型的子类型迭代器 |
| `required` | 必填属性列表 |
| `type` | JSON Schema 类型 |

**核心检测方法**：
| 方法 | 说明 |
|------|------|
| `is_reference()` | 是否是 `$ref` 引用 |
| `is_anyOf()` | 是否包含 `anyOf` |
| `is_union()` | 是否是 Union 类型（anyOf 且无 type） |
| `is_object()` | 是否是对象类型 |
| `is_array()` | 是否是数组类型 |
| `is_literal()` | 是否是 enum/const（转换为 Literal） |
| `is_flattenable()` | 是否可扁平化（用于 @overload） |
| `is_union_flattenable()` | 是否是可扁平化的 Union |

### 4.4 SchemaGenerator 类：代码生成器

**文件位置**: `tools/schemapi/codegen.py:169-456`

`SchemaGenerator` 根据 SchemaInfo 生成 Python 类代码。

```python
class SchemaGenerator:
    schema_class_template = textwrap.dedent(
        '''
    class {classname}({basename}):
        """{docstring}"""
        _schema = {schema!r}
        _rootschema = {rootschema!r}

        {init_code}
    '''
    )

    def schema_class(self) -> str:
        """Generate code for a schema class."""
        rootschema: dict = self.rootschema or self.schema
        schemarepr: object = self.schemarepr or self.schema
        # ...
        docstring = self.docstring(indent=4)
        init_code = self.init_code(indent=4)
        return self.schema_class_template.format(...)
```

**生成的代码示例**（来自 `altair/vegalite/v6/schema/core.py`）：

```python
class Axis(VegaLiteSchema):
    """Axis schema wrapper
    
    Parameters
    ----------
    bandPosition : float
        ...
    domain : bool
        ...
    """
    _schema = {'$ref': '#/definitions/Axis'}
    _rootschema = VegaLiteSchema._rootschema

    def __init__(
        self,
        bandPosition: Optional[float] = Undefined,
        domain: Optional[bool] = Undefined,
        # ... 其他参数
        **kwds
    ):
        super(Axis, self).__init__(
            bandPosition=bandPosition,
            domain=domain,
            # ...
            **kwds
        )
```

### 4.5 类型表示转换：`to_type_repr`

**文件位置**: `tools/schemapi/utils.py:446-540`

这个方法将 JSON Schema 类型转换为 Python 类型注解。

```python
def to_type_repr(
    self,
    *,
    as_str: bool = True,
    target: TargetType = "doc",  # "annotation" 或 "doc"
    use_concrete: bool = False,
    use_undefined: bool = False,
) -> str | list[str]:
    tps: set[str] = set()
    FOR_TYPE_HINTS: bool = target == "annotation"

    # 1. 有 title 的引用类型
    if self.title:
        if target == "annotation":
            tps.update(self.title_to_type_reprs(use_concrete=use_concrete))
        elif target == "doc":
            tps.add(rst_syntax_for_class(self.title))

    # 2. 空 schema → Any
    if self.is_empty():
        tps.add("Any")
    
    # 3. enum/const → Literal[...]
    elif self.is_literal():
        tp_str = spell_literal(self.literal)
        if FOR_TYPE_HINTS:
            tp_str = TypeAliasTracer.add_literal(self, tp_str, replace=True)
        tps.add(tp_str)
    
    # 4. anyOf → Union[...]
    elif self.is_anyOf():
        it_nest = (
            s.to_type_repr(target=target, as_str=False, use_concrete=use_concrete)
            for s in self.anyOf
        )
        tps.update(maybe_rewrap_literal(chain.from_iterable(it_nest)))
    
    # 5. 数组类型 → Sequence[...]
    elif self.is_array():
        tps.add(
            spell_nested_sequence(self, target=target, use_concrete=use_concrete)
        )
    
    # 6. 基本类型映射
    elif self.type in jsonschema_to_python_types:
        tps.add(jsonschema_to_python_types[self.type])

    # 最终处理
    return (
        finalize_type_reprs(tps, target=target, use_undefined=use_undefined)
        if as_str
        else sort_type_reprs(tps)
    )
```

**类型映射表**：

| JSON Schema 类型 | Python 类型 |
|------------------|-------------|
| `"string"` | `str` |
| `"number"` | `float` |
| `"integer"` | `int` |
| `"object"` | `Mapping[str, Any]` (Map) |
| `"boolean"` | `bool` |
| `"array"` | `list` / `Sequence` |
| `"null"` | `None` |
| `enum/const` | `Literal[...]` |
| `anyOf` | `Union[...]` |

---

## 5. Union Type 的引用展开逻辑

### 5.1 核心概念

在 JSON Schema 中，`anyOf` 关键字表示"满足任一子 schema"，对应 Python 的 `Union[...]` 类型。

**示例 Schema**：
```json
{
  "anyOf": [
    {"type": "string"},
    {"$ref": "#/definitions/Foo"},
    {"type": "null"}
  ]
}
```

**生成的 Python 类型**：
```python
Union[str, Foo, None]
```

### 5.2 Union 类型检测

**文件位置**: `tools/schemapi/utils.py:807-813`

```python
def is_union(self) -> bool:
    """
    Candidate for ``Union`` type alias.
    
    Not a real class.
    """
    return self.is_anyOf() and not self.type
```

**关键点**：只有当 schema 包含 `anyOf` 且没有 `type` 字段时，才被视为纯 Union 类型。

### 5.3 Union 扁平化检测

**文件位置**: `tools/schemapi/utils.py:855-881`

为了防止 `@overload` 方法爆炸，Altair 会检测 Union 是否可以"扁平化"。

```python
def is_flattenable(self) -> bool:
    """
    Represents a range of cases we want to annotate in ``@overload``(s).
    
    Base cases look like:
        Literal["left", "center", "right"]
        float
        Sequence[str]
    
    We also include compound cases, but only when **every** member meets these criteria:
        Literal["pad", "none", "fit"] | None
        float | Sequence[float]
    """
    return self.is_literal() or self.is_primitive() or self.is_union_flattenable()

def is_union_flattenable(self) -> bool:
    """
    Represents a fully flattenable ``Union``.
    Used to prevent ``@overload`` explosion in ``channels.py``
    
    Requires **every** member of the ``Union`` satisfies *at least* **one** the criteria.
    """
    if not self.is_union():
        return False
    else:
        fns = (
            SchemaInfo.is_literal,
            SchemaInfo.is_array,
            SchemaInfo.is_primitive,
            SchemaInfo.is_union_flattenable,
        )
        return all(any(fn(el) for fn in fns) for el in self.anyOf)
```

### 5.4 Grouped 分类器

**文件位置**: `tools/schemapi/utils.py:975-1006`

`Grouped` 类用于将 Union 成员分为两组：
- `truthy`：可扁平化的类型（合并为一个 @overload）
- `falsy`：不可扁平化的类型（需要分别展开）

```python
class Grouped(Generic[T]):
    def __init__(
        self, iterable: Iterable[T], /, predicate: Callable[[T], bool]
    ) -> None:
        truthy, falsy = deque[T](), deque[T]()
        for el in iterable:
            if predicate(el):
                truthy.append(el)
            else:
                falsy.append(el)
        self.truthy: deque[T] = truthy
        self.falsy: deque[T] = falsy
```

### 5.5 @overload 签名分发

**文件位置**: `tools/schemapi/codegen.py:423-447`

```python
def overload_dispatch(self, prop: str, info: SchemaInfo, /) -> Iterator[str]:
    """
    For a given property ``prop``, decide how to represent all valid signatures.
    
    Dispatching between 3 kinds of ``@overload``:
    1. Union 中可扁平化的基本类型 → 单个签名
    2. 复杂类型 → 递归展开为多个签名
    3. 非 Union 类型 → 单个签名
    """
    if info.is_anyOf():
        # 分组：可扁平化 vs 不可扁平化
        grouped = Grouped(info.anyOf, SchemaInfo.is_flattenable)
        
        # 特殊处理：如果只有一个复杂类型且有 properties，合并到 truthy
        if (expand := grouped.falsy) and len(expand) == 1 and expand[0].properties:
            grouped.truthy.append(expand[0])
        
        # 可扁平化类型 → 单个 @overload
        if flatten := grouped.truthy:
            yield from self.overload_signature(prop, flatten)
        
        # 不可扁平化类型 → 递归展开
        if expand := grouped.falsy:
            yield from self._overload_expand(prop, expand)
    else:
        yield from self.overload_signature(prop, info)
```

### 5.6 递归展开逻辑

**文件位置**: `tools/schemapi/codegen.py:409-421`

```python
def _overload_expand(
    self, prop: str, info: SchemaInfo | Iterable[SchemaInfo], /
) -> Iterator[str]:
    children: Iterable[SchemaInfo]
    if isinstance(info, SchemaInfo):
        # 如果是 Union 且不可扁平化，递归处理子成员
        children = info.anyOf if info.is_anyOf() else (info,)
    else:
        children = info
    
    for child in children:
        # 递归：如果子成员也是不可扁平化的 Union，继续展开
        if child.is_anyOf() and not child.is_union_flattenable():
            yield from self._overload_expand(prop, child)
        else:
            yield from self.overload_signature(prop, child)
```

### 5.7 Union 展开流程图

```
输入：anyOf = [A, B, C, D]
        │
        ▼
┌─────────────────────────┐
│ Grouped 分类           │
│ predicate=is_flattenable │
└─────────────────────────┘
        │
        ├─────────────────┐
        ▼                 ▼
   ┌─────────┐      ┌──────────┐
   │ truthy  │      │  falsy   │
   │ (可扁平化)│      │ (不可扁平化)│
   └────┬────┘      └────┬─────┘
        │                 │
        ▼                 ▼
┌─────────────┐    ┌──────────────────┐
│ 合并为单个  │    │ 递归展开 _overload │
│ @overload  │    │ _expand()         │
└─────────────┘    └──────────────────┘
                          │
                          ▼
              ┌─────────────────────────────┐
              │ 对每个子类型：               │
              │ - 是 anyOf 且不可扁平化？   │
              │   ├─ 是 → 继续递归          │
              │   └─ 否 → 生成 @overload    │
              └─────────────────────────────┘
```

---

## 6. 校验错误的路径追踪机制

### 6.1 整体验证流程

**文件位置**: `altair/utils/schemapi.py:124-159`

```python
def validate_jsonschema(
    spec,
    schema: dict[str, Any],
    rootschema: dict[str, Any] | None = None,
    *,
    raise_error: bool = True,
) -> jsonschema.exceptions.ValidationError | None:
    # 步骤1：使用 jsonschema 获取所有错误
    errors = _get_errors_from_spec(spec, schema, rootschema=rootschema)
    
    if errors:
        # 步骤2：获取叶子错误（最具体的错误）
        leaf_errors = _get_leaves_of_error_tree(errors)
        
        # 步骤3：按 json_path 分组
        grouped_errors = _group_errors_by_json_path(leaf_errors)
        
        # 步骤4：只保留最具体的路径
        grouped_errors = _subset_to_most_specific_json_paths(grouped_errors)
        
        # 步骤5：去重
        grouped_errors = _deduplicate_errors(grouped_errors)

        # 选择主要错误，附加所有错误信息
        main_error: Any = next(iter(grouped_errors.values()))[0]
        main_error._all_errors = grouped_errors
        
        if raise_error:
            raise main_error
        else:
            return main_error
    else:
        return None
```

### 6.2 叶子错误提取

**文件位置**: `altair/utils/schemapi.py:310-329`

jsonschema 的 `ValidationError` 形成一棵树，父错误由子错误导致。我们需要找到最具体的"叶子"错误。

```python
def _get_leaves_of_error_tree(
    errors: ValidationErrorList,
) -> ValidationErrorList:
    """
    For each error in `errors`, it traverses down the "error tree" 
    to find and return all "leaf" errors.
    
    These are errors which have no further errors that caused it 
    and so they are the most specific errors.
    """
    leaves: ValidationErrorList = []
    for err in errors:
        if err.context:
            # 有子错误 → 递归遍历
            leaves.extend(_get_leaves_of_error_tree(err.context))
        else:
            # 无上下文 → 叶子错误
            leaves.append(err)
    return leaves
```

**错误树示例**：
```
ValidationError (anyOf failed)  ← 父错误（不具体）
├── ValidationError (type: expected string)  ← 叶子错误
└── ValidationError (type: expected number)  ← 叶子错误
```

### 6.3 按 JSON Path 分组

**文件位置**: `altair/utils/schemapi.py:293-308`

```python
def _group_errors_by_json_path(
    errors: ValidationErrorList,
) -> GroupedValidationErrors:
    """
    Groups errors by the `json_path` attribute.
    
    Example paths:
        "$.encoding.x"
        "$.encoding.x.tooltip"
        "$.mark"
    """
    errors_by_json_path = defaultdict(list)
    for err in errors:
        err_key = getattr(err, "json_path", _json_path(err))
        errors_by_json_path[err_key].append(err)
    return dict(errors_by_json_path)
```

### 6.4 最具体路径选择

**文件位置**: `altair/utils/schemapi.py:333-349`

```python
def _subset_to_most_specific_json_paths(
    errors_by_json_path: GroupedValidationErrors,
) -> GroupedValidationErrors:
    """
    Removes key (json path), value (errors) pairs where the json path 
    is fully contained in another json path.
    
    Example:
        Input:  {"$.encoding.x": [...], "$.encoding.x.tooltip": [...]}
        Output: {"$.encoding.x.tooltip": [...]}
    
    Reasoning: More specific paths give more helpful error messages.
    """
    errors_by_json_path_specific: GroupedValidationErrors = {}
    for json_path, errors in errors_by_json_path.items():
        # 检查当前路径是否是其他路径的前缀
        if not _contained_at_start_of_one_of_other_values(
            json_path, list(errors_by_json_path.keys())
        ):
            errors_by_json_path_specific[json_path] = errors
    return errors_by_json_path_specific
```

### 6.5 SchemaValidationError 增强类

**文件位置**: `altair/utils/schemapi.py:626-873`

`SchemaValidationError` 包装原始的 `jsonschema.ValidationError`，提供更友好的错误消息。

```python
class SchemaValidationError(jsonschema.ValidationError):
    # JSON Schema 类型到 Python 类型的映射
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
        self.obj = obj  # 保存原始 SchemaBase 对象
        self._errors: GroupedValidationErrors = getattr(
            err, "_all_errors", {getattr(err, "json_path", _json_path(err)): [err]}
        )
        self._original_message = self.message
        self.message = self._get_message()  # 生成友好消息
```

### 6.6 错误消息生成

**核心方法**：
1. `_get_additional_properties_error_message()`：处理未知参数错误
2. `_get_default_error_message()`：处理类型/枚举错误
3. `_get_altair_class_for_error()`：根据错误路径查找对应的 Altair 类

**未知参数错误示例**：
```python
def _get_additional_properties_error_message(
    self, error: jsonschema.exceptions.ValidationError
) -> str:
    # 1. 找到对应的 Altair 类
    altair_cls = self._get_altair_class_for_error(error)
    
    # 2. 获取该类的所有参数名
    param_dict_keys = inspect.signature(altair_cls).parameters.keys()
    
    # 3. 格式化为表格
    param_names_table = self._format_params_as_table(param_dict_keys)
    
    # 4. 提取未知参数名
    parameter_name = error.message.split("('")[-1].split("'")[0]
    
    # 5. 组装消息
    message = f"""\
`{altair_cls.__name__}` has no parameter named '{parameter_name}'

Existing parameter names are:
{param_names_table}
See the help for `{altair_cls.__name__}` to read the full description..."""
    return message
```

### 6.7 类名路径追踪

**文件位置**: `altair/utils/schemapi.py:730-747`

```python
def _get_altair_class_for_error(
    self, error: jsonschema.exceptions.ValidationError
) -> type[SchemaBase]:
    """
    Try to get the lowest class possible in the chart hierarchy.
    
    Traverses the error's absolute_path backwards to find matching class names.
    
    Example:
        absolute_path = ["encoding", "x", "stack"]
        → Try "stack" → Stack (not found)
        → Try "x" → X (found!)
    """
    from altair import vegalite

    for prop_name in reversed(error.absolute_path):
        if isinstance(prop_name, str):
            # 首字母大写，尝试查找类
            candidate = prop_name[0].upper() + prop_name[1:]
            if tp := getattr(vegalite, candidate, None):
                # 可能是 channel 类型，尝试更具体的类
                return _maybe_channel(tp, self.instance)
    return type(self.obj)
```

### 6.8 完整错误处理流程图

```
用户代码触发验证（to_dict(validate=True)）
        │
        ▼
┌─────────────────────────────────┐
│ 1. jsonschema 验证              │
│    validator.iter_errors(spec)  │
│    返回 ValidationError 列表     │
└─────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────┐
│ 2. 提取叶子错误                 │
│    _get_leaves_of_error_tree() │
│    遍历错误树，找到最具体的错误   │
└─────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────┐
│ 3. 按 json_path 分组            │
│    _group_errors_by_json_path() │
│    同一位置的错误放在一起         │
└─────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────┐
│ 4. 选择最具体路径                │
│    _subset_to_most_specific_    │
│    json_paths()                 │
│    "$.encoding.x.tooltip" 优先于 │
│    "$.encoding.x"               │
└─────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────┐
│ 5. 去重错误                     │
│    _deduplicate_errors()        │
│    - enum 去重：只保留最长的枚举  │
│    - additionalProperties 去重  │
│    - 按消息去重                 │
└─────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────┐
│ 6. SchemaValidationError 包装   │
│    - 查找对应的 Altair 类        │
│    - 生成友好的错误消息          │
│    - 格式化参数列表为表格        │
└─────────────────────────────────┘
        │
        ▼
   抛出异常给用户
```

---

## 7. 关键数据结构和类型定义

### 7.1 Undefined 单例

**文件位置**: `altair/utils/schemapi.py:967-1027`

```python
class UndefinedType:
    """A singleton object for marking undefined parameters."""
    
    __instance = None
    
    def __new__(cls, *args, **kwargs) -> Self:
        if not isinstance(cls.__instance, cls):
            cls.__instance = object.__new__(cls, *args, **kwargs)
        return cls.__instance

Undefined = UndefinedType()

# 自定义 Optional 类型（区别于 typing.Optional）
Optional: TypeAlias = T | UndefinedType
```

**设计目的**：
- 区分"用户未提供参数"和"用户传入 None"
- 在 `to_dict` 序列化时，`Undefined` 会被跳过
- 与 `typing.Optional[T]` 不同，后者表示 `T | None`

### 7.2 TypeAliasTracer 类型别名追踪器

**文件位置**: `tools/schemapi/utils.py:90-245`

用于收集和管理生成的 `Literal` 类型别名，避免在每个方法中重复长枚举。

```python
class _TypeAliasTracer:
    """
    Recording all `enum` -> `Literal` translations.
    
    Rewrites as `TypeAlias` to be reused anywhere.
    """
    
    def __init__(self, fmt: str = "{}_T") -> None:
        self.fmt: str = fmt
        self._literals: dict[str, str] = {}  # {alias_name: literal_statement}
        self._literals_invert: dict[str, str] = {}  # {literal_statement: alias_name}
        self._aliases: dict[str, str] = {}

    def add_literal(self, info: SchemaInfo, tp: str, /, *, replace: bool = False) -> str:
        if info.title:
            alias = self.fmt.format(info.title)
            if alias not in self._literals:
                self._update_literals(alias, tp)
            if replace:
                tp = alias  # 替换为别名引用
        # ...
        return tp
```

**生成示例**：
```python
# 原始类型（很长）
Literal['year', 'quarter', 'month', 'week', 'day', ...]

# 使用 TypeAlias 后
TimeUnit_T: TypeAlias = Literal['year', 'quarter', 'month', 'week', 'day', ...]

# 方法签名中使用别名
def timeUnit(self, _: TimeUnit_T, /) -> X: ...
```

### 7.3 SchemaLike 协议

**文件位置**: `altair/utils/schemapi.py:914-944`

```python
@runtime_checkable
class SchemaLike(Generic[_JSON_VT_co], Protocol):
    """
    Represents ``altair`` classes which *may* not derive ``SchemaBase``.
    
    Minimum requirements:
    - _schema: A single item JSON Schema using the `type` keyword
    - to_dict(): Convert to dict
    """
    
    _schema: _TypeMap[_JSON_VT_co]
    
    def to_dict(self, *args, **kwds) -> Any: ...
```

**设计目的**：
- 允许非 `SchemaBase` 派生类参与序列化
- 例如 `ConditionLike` 条件包装器

---

## 8. 代码示例

### 8.1 自定义 SchemaBase 子类

```python
from altair.utils.schemapi import SchemaBase, Undefined

class MySchema(SchemaBase):
    _schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "count": {"type": "integer"},
            "items": {
                "type": "array",
                "items": {"type": "string"}
            }
        },
        "required": ["name"]
    }

# 使用
obj = MySchema(name="test", count=5, items=["a", "b"])
print(obj.to_dict())
# {'name': 'test', 'count': 5, 'items': ['a', 'b']}

# 验证失败会抛出 SchemaValidationError
from altair.utils.schemapi import SchemaValidationError
try:
    MySchema(name=123)  # name 应该是 string
except SchemaValidationError as e:
    print(e.message)
```

### 8.2 Union 类型处理示例

```python
from altair.utils.schemapi import SchemaBase

class UnionSchema(SchemaBase):
    _schema = {
        "anyOf": [
            {"type": "string"},
            {"type": "integer"},
            {"type": "null"}
        ]
    }

# 可以是 string
obj1 = UnionSchema("hello")
print(obj1.to_dict())  # 'hello'

# 可以是 integer
obj2 = UnionSchema(42)
print(obj2.to_dict())  # 42

# 可以是 None
obj3 = UnionSchema(None)
print(obj3.to_dict())  # None
```

---

## 9. 总结

### 9.1 核心机制回顾

| 机制 | 核心文件/类 | 关键功能 |
|------|------------|---------|
| **运行时验证** | `altair/utils/schemapi.py` | `SchemaBase` 属性存储、`to_dict` 序列化、`validate_jsonschema` 验证 |
| **代码生成** | `tools/schemapi/codegen.py` | `SchemaGenerator` 生成 Python 类代码 |
| **Schema 解析** | `tools/schemapi/utils.py` | `SchemaInfo` 解析 schema 结构、类型转换 |
| **Union 展开** | `tools/schemapi/codegen.py` | `overload_dispatch`、`_overload_expand` 递归展开 |
| **错误追踪** | `altair/utils/schemapi.py` | `_get_leaves_of_error_tree`、`SchemaValidationError` |

### 9.2 设计亮点

1. **延迟验证**：通过 `DEBUG_MODE` 控制是否在构造时验证，平衡开发体验和性能
2. **`_args`/`_kwds` 存储**：所有属性间接存储，便于序列化和验证
3. **拓扑排序生成**：基于依赖关系图确定类生成顺序
4. **智能 @overload**：Union 类型扁平化检测，避免方法签名爆炸
5. **错误树分析**：从 jsonschema 错误树中提取最具体、最有用的错误信息
6. **TypeAlias 优化**：长 `Literal` 类型提取为别名，提高代码可读性

### 9.3 关键流程速查表

**序列化流程**：
```
SchemaBase.__init__ → _args/_kwds 存储
    ↓
to_dict(validate=True)
    ↓
_todict() 递归处理
    ↓
validate_jsonschema() 验证
    ↓
SchemaValidationError 包装（如失败）
```

**代码生成流程**：
```
加载 vega-lite-schema.json
    ↓
遍历 definitions → SchemaGenerator
    ↓
构建依赖关系图
    ↓
拓扑排序
    ↓
生成 __init__、@overload、docstring
    ↓
写入 core.py, channels.py 等文件
```

**Union 展开流程**：
```
anyOf 子类型列表
    ↓
Grouped 分类（可扁平化/不可扁平化）
    ↓
可扁平化 → 合并为单个 @overload
不可扁平化 → _overload_expand 递归
    ↓
生成方法签名
```

**错误处理流程**：
```
jsonschema ValidationError 列表
    ↓
_get_leaves_of_error_tree 提取叶子
    ↓
_group_errors_by_json_path 按路径分组
    ↓
_subset_to_most_specific_json_paths 选择最具体路径
    ↓
SchemaValidationError 生成友好消息
```

---

## 10. 参考文件位置

| 文件 | 说明 |
|------|------|
| `altair/utils/schemapi.py` | 运行时核心：SchemaBase、验证、序列化、错误处理 |
| `tools/schemapi/codegen.py` | 代码生成：SchemaGenerator、@overload 生成 |
| `tools/schemapi/utils.py` | Schema 解析：SchemaInfo、类型转换、TypeAliasTracer |
| `tools/generate_schema_wrapper.py` | 主生成脚本：协调所有生成流程 |
| `altair/vegalite/v6/schema/core.py` | 生成的核心类示例 |
| `tests/utils/test_schemapi.py` | 测试文件：包含各种使用场景 |

---

## 11. Shorthand 编码字符串解析路径

### 11.1 概述

Shorthand 是 Altair 中最直观的语法糖，允许用户用简洁的字符串表示编码通道的配置。

**示例**:
```python
# 使用 shorthand
alt.Chart(data).encode(x="price:Q", y="count():O")

# 等价于完整形式
alt.Chart(data).encode(
    x=alt.X(field="price", type="quantitative"),
    y=alt.Y(aggregate="count", type="ordinal")
)
```

### 11.2 Shorthand 语法格式

**支持的语法模式** (`altair/utils/core.py:632-647`):

| 模式 | 示例 | 解析结果 |
|------|------|---------|
| 简单字段 | `"field_name"` | `{"field": "field_name"}` |
| 字段+类型 | `"field:Q"` | `{"field": "field", "type": "quantitative"}` |
| 聚合函数 | `"sum(field)"` | `{"aggregate": "sum", "field": "field"}` |
| 聚合+类型 | `"count():Q"` | `{"aggregate": "count", "type": "quantitative"}` |
| 时间单位 | `"year(date)"` | `{"timeUnit": "year", "field": "date", "type": "temporal"}` |
| 时间单位+类型 | `"month(date):O"` | `{"timeUnit": "month", "field": "date", "type": "ordinal"}` |

**类型编码映射**:
```python
TYPECODE_MAP = {
    "ordinal": "O",      # 定序
    "nominal": "N",      # 定类
    "quantitative": "Q", # 定量
    "temporal": "T",     # 时间
    "geojson": "G",      # GeoJSON
}
```

**支持的聚合函数** (`AGGREGATES`, `WINDOW_AGGREGATES`):
- 普通聚合: `sum`, `mean`, `count`, `min`, `max`, `median`, `variance` 等
- 窗口聚合: `row_number`, `rank`, `lag`, `lead`, `first_value` 等
- 时间单位: `year`, `quarter`, `month`, `week`, `day`, `hours`, `minutes` 等（含 UTC 变体）

### 11.3 正则表达式构建

**文件位置**: `altair/utils/core.py:632-652`

```python
def parse_shorthand(...):
    # 按优先级构建正则模式列表
    patterns = []
    
    # 1. 聚合函数（无字段，如 count()）
    if parse_aggregates:
        patterns.extend([r"{agg_count}\(\)"])
        patterns.extend([r"{aggregate}\({field}\)"])
    
    # 2. 窗口操作
    if parse_window_ops:
        patterns.extend([r"{op_count}\(\)"])
        patterns.extend([r"{window_op}\({field}\)"])
    
    # 3. 时间单位
    if parse_timeunits:
        patterns.extend([r"{timeUnit}\({field}\)"])
    
    # 4. 简单字段
    patterns.extend([r"{field}"])
    
    # 5. 可选的类型后缀（如 :Q, :N）
    if parse_types:
        patterns = list(itertools.chain(*((p + ":{type}", p) for p in patterns)))
    
    # 编译正则
    regexps = (
        re.compile(r"\A" + p.format(**SHORTHAND_UNITS) + r"\Z", re.DOTALL)
        for p in patterns
    )
```

**正则单元定义**:
```python
SHORTHAND_UNITS = {
    "field": "(?P<field>.*)",
    "type": "(?P<type>ordinal|nominal|quantitative|temporal|geojson|O|N|Q|T|G)",
    "agg_count": "(?P<aggregate>count)",
    "op_count": "(?P<op>count)",
    "aggregate": "(?P<aggregate>sum|mean|count|min|max|...)",
    "window_op": "(?P<op>row_number|rank|lag|...)",
    "timeUnit": "(?P<timeUnit>year|quarter|month|...)",
}
```

### 11.4 解析执行流程

**文件位置**: `altair/utils/core.py:654-710`

```python
def parse_shorthand(...):
    # 步骤1：匹配正则
    if isinstance(shorthand, dict):
        attrs = shorthand  # 已经是 dict，直接使用
    else:
        # 遍历所有正则，找到第一个匹配的
        attrs = next(
            exp.match(shorthand).groupdict()
            for exp in regexps
            if exp.match(shorthand) is not None
        )
    
    # 步骤2：类型代码转换（如 "Q" → "quantitative"）
    if "type" in attrs:
        attrs["type"] = INV_TYPECODE_MAP.get(attrs["type"], attrs["type"])
    
    # 步骤3：count() 默认类型为 quantitative
    if attrs == {"aggregate": "count"}:
        attrs["type"] = "quantitative"
    
    # 步骤4：时间单位默认类型为 temporal
    if "timeUnit" in attrs and "type" not in attrs:
        attrs["type"] = "temporal"
    
    # 步骤5：从数据推断类型（如果提供了 dataframe）
    if "type" not in attrs and is_data_type(data):
        unescaped_field = attrs["field"].replace("\\", "")
        data_nw = nw.from_native(data, eager_or_interchange_only=True)
        schema = data_nw.schema
        if unescaped_field in schema:
            column = data_nw[unescaped_field]
            # 根据数据类型推断
            attrs["type"] = infer_vegalite_type_for_narwhals(column)
            # 如果是有序分类，返回 (type, categories)
            if isinstance(attrs["type"], tuple):
                attrs["sort"] = attrs["type"][1]
                attrs["type"] = attrs["type"][0]
    
    # 步骤6：验证未转义的冒号
    if (
        "field" in attrs
        and ":" in attrs["field"]
        and attrs["field"][attrs["field"].rfind(":") - 1] != "\\"
    ):
        raise ValueError(
            f'"{attrs["field"].split(":")[-1]}" 不是有效的数据类型...'
            + '\n如果字段名包含冒号，使用反斜杠转义：如 "column\\:name"'
        )
    
    return attrs
```

### 11.5 从 Schema 注入 shorthand 属性

**关键设计**: Vega-Lite Schema 本身并没有 `shorthand` 这个属性。它是在代码生成阶段动态注入的。

**文件位置**: `tools/generate_schema_wrapper.py:605-630`

```python
def load_schema_with_shorthand_properties(fp: Path, /) -> dict[str, Any]:
    schema = load_schema(fp)
    encoding_def = "FacetedEncoding"
    
    # 定义 shorthand 的 schema
    shorthand = {
        "anyOf": [
            {"type": "string"},
            {"type": "array", "items": {"type": "string"}},
            {"$ref": "#/definitions/RepeatRef"},
        ],
        "description": "shorthand for field, aggregate, and type",
    }
    
    # 遍历所有 encoding 通道的属性
    for propschema in encoding.properties.values():
        # 找到 field/datum/value 的定义
        def_dict = get_field_datum_value_defs(propschema, schema)
        
        if field_ref := def_dict.get("field", None):
            # 解析引用的 schema
            defschema: dict[str, Any] = {"$ref": field_ref}
            defschema = copy.deepcopy(resolve_references(defschema, schema))
            
            # 动态注入 shorthand 属性！
            defschema["properties"]["shorthand"] = shorthand
            
            # 添加到 required（如果不存在）
            if "required" not in defschema:
                defschema["required"] = ["shorthand"]
            elif "shorthand" not in defschema["required"]:
                defschema["required"].append("shorthand")
            
            # 保存回 schema definitions
            schema["definitions"][field_ref.split("/")[-1]] = defschema
    
    return schema
```

**递归查找 field 定义** (`tools/generate_schema_wrapper.py:649-682`):
```python
def recursive_dict_update(schema: dict, root: dict, def_dict: dict) -> None:
    """递归查找包含 field/datum/value 键的 schema 定义"""
    if "$ref" in schema:
        # 解析引用
        next_schema = resolve_references(schema, root)
        if "properties" in next_schema:
            definition = schema["$ref"]
            properties = next_schema["properties"]
            # 检查是否包含目标键
            for k in def_dict:
                if k in properties:
                    def_dict[k] = definition  # 记录找到的定义路径
        else:
            # 继续递归解析
            recursive_dict_update(next_schema, root, def_dict)
    elif "anyOf" in schema:
        # 遍历 anyOf 的所有子 schema
        for sub_schema in schema["anyOf"]:
            recursive_dict_update(sub_schema, root, def_dict)
```

### 11.6 FieldChannelMixin 中的 shorthand 解析

**文件位置**: `altair/vegalite/v6/schema/channels.py:158-221` (由 `CHANNEL_MIXINS` 模板生成)

```python
class FieldChannelMixin:
    _encoding_name: str

    def to_dict(
        self,
        validate: bool = True,
        ignore: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict | list[dict]:
        context = context or {}
        ignore = ignore or []
        
        # 步骤1：获取 shorthand 和 field
        shorthand = self._get("shorthand")
        field = self._get("field")

        # 步骤2：不能同时指定 shorthand 和 field
        if shorthand is not Undefined and field is not Undefined:
            msg = f"{self.__class__.__name__} specifies both shorthand={shorthand} and field={field}."
            raise ValueError(msg)

        # 步骤3：处理列表形式的 shorthand
        if isinstance(shorthand, (tuple, list)):
            # 如 ["a:Q", "b:Q"] 用于 Detail/Order 通道
            kwds = self._kwds.copy()
            kwds.pop("shorthand")
            return [
                self.__class__(sh, **kwds).to_dict(...)
                for sh in shorthand
            ]

        # 步骤4：解析 shorthand 字符串
        if shorthand is Undefined:
            parsed = {}
        elif isinstance(shorthand, str):
            # 从 context 获取数据用于类型推断
            data: nw.DataFrame | Any = context.get("data", None)
            parsed = parse_shorthand(shorthand, data=data)
            
            # 检查类型是否需要
            type_required = "type" in self._kwds  # 检查 schema required
            type_in_shorthand = "type" in parsed
            type_defined_explicitly = self._get("type") is not Undefined
            
            if not type_required:
                # 次级字段（x2, y2 等）不需要类型
                parsed.pop("type", None)
            elif not (type_in_shorthand or type_defined_explicitly):
                # 类型缺失，尝试从数据推断
                if isinstance(data, nw.DataFrame):
                    msg = (
                        f'Unable to determine data type for the field "{shorthand}";'
                        " verify that the field name is not misspelled."
                    )
                else:
                    msg = (
                        f"{shorthand} encoding field is specified without a type; "
                        "the type cannot be automatically inferred because "
                        "the data is not specified as a pandas.DataFrame."
                    )
                raise ValueError(msg)
        else:
            # shorthand 不是字符串（如 RepeatRef），直接作为 field
            parsed = {"field": shorthand}
        
        # 步骤5：将解析结果放入 context，供 SchemaBase.to_dict 处理
        context["parsed_shorthand"] = parsed

        # 步骤6：调用父类 SchemaBase.to_dict
        return super().to_dict(validate=validate, ignore=ignore, context=context)
```

### 11.7 SchemaBase 中应用解析结果

**文件位置**: `altair/utils/schemapi.py:1415-1440`

```python
def _replace_parsed_shorthand(
    parsed_shorthand: dict[str, Any], kwds: dict[str, Any]
) -> dict[str, Any]:
    """
    将 parsed_shorthand 合并到 kwds 中。
    注意：用户显式传入的参数优先级更高。
    """
    for key, val in parsed_shorthand.items():
        if key not in kwds or kwds[key] is Undefined:
            # 只有当用户未显式指定时，才使用解析值
            kwds[key] = val
    return kwds
```

**在 `to_dict` 中的调用** (`altair/utils/schemapi.py:1189-1200`):
```python
def to_dict(...):
    # ...
    elif not self._args:
        kwds = self._kwds.copy()
        exclude = {*ignore, "shorthand", "_cached_hash"}
        
        # 关键：从 context 中取出解析结果
        if parsed := context.pop("parsed_shorthand", None):
            kwds = _replace_parsed_shorthand(parsed, kwds)
        
        # shorthand 会被过滤掉（在 exclude 中）
        kwds = {k: v for k, v in kwds.items() if k not in exclude}
    # ...
```

### 11.8 Shorthand 完整解析流程图

```
用户代码: alt.X("price:Q")
        │
        ▼
┌─────────────────────────────────────┐
│ 1. SchemaBase.__init__              │
│    存储: _kwds = {"shorthand": "price:Q"} │
└─────────────────────────────────────┘
        │
        ▼ 调用 to_dict() 时
┌─────────────────────────────────────┐
│ 2. FieldChannelMixin.to_dict()      │
│    - 检测到 shorthand 是字符串       │
│    - 从 context 获取 data            │
│    - 调用 parse_shorthand()          │
└─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│ 3. parse_shorthand() 核心解析       │
│    - 正则匹配: "price:Q"            │
│      → {"field": "price", "type": "Q"} │
│    - 类型代码转换: "Q" → "quantitative" │
│    - 从数据推断类型（如需要）         │
│    - 返回: {"field": "price", "type": "quantitative"} │
└─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│ 4. FieldChannelMixin.to_dict()      │
│    - 将解析结果放入 context:         │
│      context["parsed_shorthand"] = {...} │
│    - 调用 super().to_dict()         │
└─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│ 5. SchemaBase.to_dict()             │
│    - 从 context 取出 parsed_shorthand │
│    - 调用 _replace_parsed_shorthand() │
│      → 将 field/type 合并到 kwds    │
│    - 过滤掉 shorthand（exclude 中）  │
│    - 最终 kwds = {"field": "price", "type": "quantitative"} │
└─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│ 6. 递归序列化 (_todict)              │
│    - 验证并返回 dict                 │
└─────────────────────────────────────┘
```

### 11.9 类型推断机制

**文件位置**: `altair/utils/core.py:713-733`

```python
def infer_vegalite_type_for_narwhals(
    column: nw.Series,
) -> InferredVegaLiteType | tuple[InferredVegaLiteType, list]:
    
    dtype = column.dtype
    
    # 有序分类 → (ordinal, categories)
    if (
        nw.is_ordered_categorical(column)
        and not (categories := column.cat.get_categories()).is_empty()
    ):
        return "ordinal", categories.to_list()
    
    # 字符串/分类/布尔 → nominal
    if dtype == nw.String or dtype == nw.Categorical or dtype == nw.Boolean:
        return "nominal"
    
    # 数值 → quantitative
    elif dtype.is_numeric():
        return "quantitative"
    
    # 日期时间 → temporal
    elif dtype == nw.Datetime or dtype == nw.Date:
        return "temporal"
    
    else:
        raise ValueError(f"Unexpected DtypeKind: {dtype}")
```

---

## 12. 三个主文件的定位与协作关系

### 12.1 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    生成的模块结构 (altair/vegalite/v6/schema/) │
└─────────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────┐    ┌───────────────┐    ┌───────────────┐
│   core.py     │    │  channels.py  │    │   mixins.py   │
│  (核心数据模型)│    │ (编码通道类)  │    │  (Mixin 增强) │
└───────┬───────┘    └───────┬───────┘    └───────┬───────┘
        │                    │                    │
        │ 依赖               │ 依赖               │ 依赖
        ▼                    ▼                    ▼
┌─────────────────────────────────────────────────────────────┐
│              altair/utils/schemapi.py                        │
│  SchemaBase, Undefined, validate_jsonschema, with_property_setters │
└─────────────────────────────────────────────────────────────┘
```

### 12.2 core.py：核心数据模型

**定位**: Vega-Lite Schema 中所有 definition 的直接 Python 映射，是整个系统的基石。

**生成入口**: `tools/generate_schema_wrapper.py:716-791`

```python
def generate_vegalite_schema_wrapper(fp: Path, /) -> ModuleDef[str]:
    basename = "VegaLiteSchema"  # 根基类名
    rootschema = load_schema_with_shorthand_properties(fp)
    definitions: dict[str, SchemaGenerator] = {}
    graph: dict[str, list[str]] = {}

    # 步骤1：为每个 definition 创建 SchemaGenerator
    for name in rootschema["definitions"]:
        defschema = {"$ref": "#/definitions/" + name}
        name = get_valid_identifier(name)
        
        # 检查特殊覆盖
        if overrides := CORE_OVERRIDES.get(name):
            tp = overrides["tp"]    # 如 MethodSchemaGenerator
            kwds = overrides["kwds"]
        else:
            tp = SchemaGenerator    # 默认生成器
            kwds = {}
        
        definitions[name] = tp(
            name,
            schema=defschema,
            schemarepr=defschema_repr,
            rootschema=rootschema,
            basename=basename,      # 基类: VegaLiteSchema
            rootschemarepr=CodeSnippet(f"{basename}._rootschema"),
            **kwds,
        )

    # 步骤2：构建依赖关系图
    for name, schema in definitions.items():
        graph[name] = []
        for child_name in schema.subclasses():  # 遍历 anyOf 引用
            child_name = get_valid_identifier(child_name)
            graph[name].append(child_name)
            
            # 动态设置继承关系！
            child: SchemaGenerator = definitions[child_name]
            if child.basename == basename:
                # 原来是 VegaLiteSchema，改为更具体的父类
                child.basename = [name]
            else:
                # 追加到基类列表
                assert isinstance(child.basename, list)
                child.basename.append(name)

    # 步骤3：拓扑排序生成代码
    for name in toposort(graph):
        contents.append(definitions[name].schema_class())
```

**生成的类继承示例** (`core.py`):
```python
# 根基类
class VegaLiteSchema(SchemaBase):
    _rootschema = load_schema()
    
    @classmethod
    def _default_wrapper_classes(cls) -> Iterator[type[Any]]:
        return _subclasses(VegaLiteSchema)


# 具体类（继承自 VegaLiteSchema 或其他类）
class Axis(VegaLiteSchema):
    """Axis schema wrapper..."""
    _schema = {'$ref': '#/definitions/Axis'}
    _rootschema = VegaLiteSchema._rootschema

    def __init__(self, bandPosition: Optional[float] = Undefined, ..., **kwds):
        super(Axis, self).__init__(bandPosition=bandPosition, ..., **kwds)


# 有 anyOf 引用的类会动态设置继承关系
# 如：如果 schema 有 "anyOf": [{"$ref": "FieldDef"}]
# 则生成: class FieldDef(VegaLiteSchema): ...
# 而引用它的类会: class Xxx(FieldDef): ...
```

**核心功能**:
| 功能 | 说明 |
|------|------|
| `_schema` | 存储对应 JSON Schema 的 `$ref` 引用 |
| `_rootschema` | 存储根 Schema 引用（用于解析嵌套引用） |
| `__init__` | 将所有参数传递给 `SchemaBase.__init__` |
| 继承链 | 基于 `anyOf` 引用动态构建类继承关系 |

### 12.3 channels.py：编码通道类

**定位**: 专门用于 Encoding 通道的增强类，通过 Mixin 机制在 SchemaBase 基础上添加 shorthand 解析能力。

**与 core.py 的关键区别**:

| 特性 | core.py 类 | channels.py 类 |
|------|-----------|----------------|
| 基类 | `VegaLiteSchema` | `FieldChannelMixin + core.xxx` |
| `_class_is_valid_at_instantiation` | `True` | `False`（延迟验证） |
| `_encoding_name` | 无 | 有（如 `"x"`, `"y"`） |
| shorthand 支持 | 无 | 有（通过 Mixin） |
| `@with_property_setters` | 无 | 有（链式 API） |
| `@overload` 方法 | 无 | 有（类型安全的 property setter） |

**生成入口**: `tools/generate_schema_wrapper.py:823-921`

```python
def generate_vegalite_channel_wrappers(fp: Path, /) -> ModuleDef[list[str]]:
    schema = load_schema_with_shorthand_properties(fp)
    encoding_def = "FacetedEncoding"
    encoding = SchemaInfo(schema["definitions"][encoding_def], rootschema=schema)
    channel_infos: dict[str, ChannelInfo] = {}
    class_defs: list[Any] = []

    # 遍历 Encoding 的所有属性（x, y, color, size 等）
    for prop, propschema in encoding.properties.items():
        # 步骤1：找到 field/datum/value 的定义
        def_dict = get_field_datum_value_defs(propschema, schema)
        
        # 步骤2：检查是否支持数组（如 Detail 可接受列表）
        supports_arrays = any(
            schema_info.is_array() for schema_info in propschema.anyOf
        )
        
        # 步骤3：类名：x → X, color → Color
        classname: str = prop[0].upper() + prop[1:]
        
        channel_info = ChannelInfo(
            supports_arrays=supports_arrays,
            deep_description=propschema.deep_description,
            field_class_name=classname,
        )

        # 步骤4：为每种类型（field/datum/value）生成类
        for encoding_spec, definition in def_dict.items():
            # 从引用提取基类名
            basename = definition.rsplit("/", maxsplit=1)[-1]
            basename = get_valid_identifier(basename)
            
            defschema = {"$ref": definition}
            kwds: dict[str, Any] = {
                "basename": basename,        # 核心基类：如 core.FieldDef
                "schema": defschema,
                "rootschema": schema,
                "encodingname": prop,        # 如 "x", "color"
            }
            
            # 根据类型选择不同的生成器
            if encoding_spec == "field":
                gen = FieldSchemaGenerator(
                    classname, 
                    nodefault=[],        # 无必需参数
                    **kwds
                )
            elif encoding_spec == "datum":
                temp_name = f"{classname}Datum"
                channel_info.datum_class_name = temp_name
                gen = DatumSchemaGenerator(
                    temp_name,
                    nodefault=["datum"],   # datum 是必需参数
                    **kwds
                )
            elif encoding_spec == "value":
                temp_name = f"{classname}Value"
                channel_info.value_class_name = temp_name
                gen = ValueSchemaGenerator(
                    temp_name,
                    nodefault=["value"],   # value 是必需参数
                    **kwds
                )

            class_defs.append(gen.schema_class())

        channel_infos[prop] = channel_info

    # 步骤5：组装内容
    contents: list[str] = [
        HEADER,
        CHANNEL_MYPY_IGNORE_STATEMENTS,  # 忽略 overload 相关的 mypy 错误
        *imports,
        import_type_checking(...),
        f"\n__all__ = {all_}\n",
        CHANNEL_MIXINS,                    # 三个 Mixin 类定义
        *class_defs,                       # 通道类
        *generate_encoding_artifacts(...), # EncodeKwds 等
    ]
```

**三种通道生成器的模板**:

```python
# FieldChannel (主要类型：支持 shorthand)
class FieldSchemaGenerator(SchemaGenerator):
    schema_class_template = textwrap.dedent(
        '''
    @with_property_setters
    class {classname}(FieldChannelMixin, core.{basename}):
        """{docstring}"""
        _class_is_valid_at_instantiation = False  # 延迟验证
        _encoding_name = "{encodingname}"

        {method_code}  # @overload 方法

        {init_code}
    '''
    )
    haspropsetters = True  # 生成 @overload 方法


# ValueChannel (直接值：如 color="red")
class ValueSchemaGenerator(SchemaGenerator):
    schema_class_template = textwrap.dedent(
        '''
    @with_property_setters
    class {classname}(ValueChannelMixin, core.{basename}):
        """{docstring}"""
        _class_is_valid_at_instantiation = False
        _encoding_name = "{encodingname}"
        ...
    '''
    )


# DatumChannel (数据值：用于条件编码)
class DatumSchemaGenerator(SchemaGenerator):
    schema_class_template = textwrap.dedent(
        '''
    @with_property_setters
    class {classname}(DatumChannelMixin, core.{basename}):
        """{docstring}"""
        _class_is_valid_at_instantiation = False
        _encoding_name = "{encodingname}"
        ...
    '''
    )
```

**生成的通道类示例** (`channels.py`):

```python
# 由 CHANNEL_MIXINS 模板生成
class FieldChannelMixin:
    _encoding_name: str

    def to_dict(self, ...):
        # shorthand 解析逻辑
        context["parsed_shorthand"] = parsed
        return super().to_dict(...)


class ValueChannelMixin:
    _encoding_name: str
    
    def to_dict(self, ...):
        # condition 字段的 shorthand 解析
        condition = self._get("condition", Undefined)
        if condition is not Undefined:
            if "field" in condition and "type" not in condition:
                kwds = parse_shorthand(condition["field"], ...)
                copy = self.copy(deep=["condition"])
                copy["condition"].update(kwds)
        return super().to_dict(...)


class DatumChannelMixin:
    _encoding_name: str
    # 无特殊处理，直接委托给父类


# 实际通道类（多重继承）
@with_property_setters
class X(FieldChannelMixin, core.FieldOrDatumDefWithConditionFacetDef):
    """X schema wrapper..."""
    _class_is_valid_at_instantiation = False
    _encoding_name = "x"

    # @overload 方法（用于链式 API）
    @overload
    def field(self, _: str, /) -> X: ...
    @overload
    def field(self, *, aggregate: ... = Undefined, type: ... = Undefined) -> X: ...
    
    def __init__(
        self,
        shorthand: Optional[Union[str, Sequence[str], core.RepeatRef]] = Undefined,
        field: Optional[str] = Undefined,
        type: Optional[Union[str, core.StandardType]] = Undefined,
        aggregate: Optional[Union[str, core.Aggregate]] = Undefined,
        **kwds
    ):
        super(X, self).__init__(
            shorthand=shorthand,
            field=field,
            type=type,
            aggregate=aggregate,
            **kwds
        )


@with_property_setters
class ColorValue(ValueChannelMixin, core.FieldDefDatumDefValueDefStringValueDefColor):
    """ColorValue schema wrapper..."""
    _encoding_name = "color"
```

### 12.4 with_property_setters 装饰器

**文件位置**: `altair/utils/schemapi.py:1597-1680`

**作用**: 为通道类添加属性 setter 方法，支持链式 API。

```python
def with_property_setters(cls: type[TSchemaBase]) -> type[TSchemaBase]:
    """Decorator to add property setters to a Schema class."""
    schema = cls.resolve_references()
    
    # 遍历所有属性，为每个创建一个 setter 描述符
    for prop, propschema in schema.get("properties", {}).items():
        setattr(cls, prop, _PropertySetter(prop, propschema))
    
    return cls


class _PropertySetter(Generic[TSchemaBase]):
    """
    描述符类，实现属性 setter。
    支持两种调用方式：
    1. obj.field("price")  → 返回新对象（副本）
    2. obj.field = "price"   → 直接设置
    """
    
    def __init__(self, prop: str, schema: dict[str, Any]) -> None:
        self.prop = prop
        self.schema = schema

    def __get__(
        self, obj: TSchemaBase | None, objtype: type[TSchemaBase] | None = None
    ) -> Any:
        if obj is None:
            return self
        # 代理到 SchemaBase.__getattr__
        return getattr(obj, self.prop)

    def __set__(self, obj: TSchemaBase, value: Any) -> None:
        # 代理到 SchemaBase.__setattr__
        setattr(obj, self.prop, value)

    def __call__(
        self, obj: TSchemaBase, *args: Any, **kwargs: Any
    ) -> TSchemaBase:
        """
        实现链式 API：obj.field("price").type("quantitative")
        """
        if args:
            # 单参数形式：obj.field("price")
            copy = obj.copy(deep=False)
            copy[self.prop] = args[0]
            return copy
        elif kwargs:
            # 关键字参数形式：obj.field(aggregate="sum", field="price")
            # (用于嵌套属性，不太常见)
            copy = obj.copy(deep=False)
            copy[self.prop] = kwargs
            return copy
        else:
            return obj
```

### 12.5 mixins.py：方法增强 Mixin

**定位**: 提供高层 API 的 Mixin 类，添加到 `alt.Chart` 等类中，简化用户操作。

**生成入口**: `tools/generate_schema_wrapper.py:1231-1261`

```python
# generate the mark mixin
markdefs = {k: f"{k}Def" for k in ["Mark", "BoxPlot", "ErrorBar", "ErrorBand"]}
fp_mixins = schemapath / "mixins.py"

mark_mixin = generate_vegalite_mark_mixin(schemafile, markdefs)
config_mixin = generate_vegalite_config_mixin(schemafile)

content_mixins = [
    HEADER,
    "\n\n",
    "\n".join(mixins_imports),
    "\n\n",
    import_type_checking(...),
    "\n\n\n",
    mark_mixin,      # MarkMethodMixin
    "\n\n\n",
    config_mixin,    # ConfigMethodMixin
]
```

**MarkMethodMixin 生成**: `tools/generate_schema_wrapper.py:924-952`

```python
def generate_vegalite_mark_mixin(fp: Path, /, markdefs: dict[str, str]) -> str:
    schema = load_schema(fp)
    code: list[str] = []

    # 步骤1：生成虚拟的 _MarkDef 类（用于 @use_signature）
    it_dummy = (
        SchemaGenerator(
            classname=f"_{mark_def}",
            schema={"$ref": "#/definitions/" + mark_def},
            rootschema=schema,
            exclude_properties={"type"},  # 排除 type，因为用户不用指定
            annotate_kwds_flag=True,
        ).schema_class()
        for mark_def in markdefs.values()
    )

    # 步骤2：为每个 mark 类型生成方法
    for mark_enum, mark_def in markdefs.items():
        _def = schema["definitions"][mark_enum]
        marks: list[Any] = _def["enum"] if "enum" in _def else [_def["const"]]

        for mark in marks:
            # mark = "point", "bar", "line" 等
            mark_method = MARK_METHOD.format(
                decorator=f"_{mark_def}",    # _MarkDef
                mark=mark,
                mark_def=mark_def
            )
            code.append("\n    ".join(mark_method.splitlines()))

    # 步骤3：组装成类
    return "\n".join(chain(it_dummy, [MARK_MIXIN.format(methods="\n".join(code))]))
```

**MARK_METHOD 模板**:
```python
MARK_METHOD: Final = '''
@use_signature({decorator})
def mark_{mark}(self, **kwds: Any) -> Self:
    """Set the chart's mark to '{mark}' (see :class:`{mark_def}`)."""

    copy = self.copy(deep=False)
    if any(val is not Undefined for val in kwds.values()):
        copy.mark = core.{mark_def}(type="{mark}", **kwds)
    else:
        copy.mark = "{mark}"  # 简单字符串形式
    return copy
'''
```

**生成的 mixins.py 示例**:

```python
class _MarkDef:  # 虚拟类，仅用于签名复制
    """_MarkDef schema wrapper..."""
    _schema = {'$ref': '#/definitions/MarkDef'}
    ...

    def __init__(
        self,
        aria: Optional[bool] = Undefined,
        color: Optional[str] = Undefined,
        filled: Optional[bool] = Undefined,
        **kwds: Any
    ):
        super(_MarkDef, self).__init__(...)


class MarkMethodMixin:
    """A mixin class that defines mark methods"""

    @use_signature(_MarkDef)
    def mark_point(self, **kwds: Any) -> Self:
        """Set the chart's mark to 'point' (see :class:`MarkDef`)."""
        copy = self.copy(deep=False)
        if any(val is not Undefined for val in kwds.values()):
            copy.mark = core.MarkDef(type="point", **kwds)
        else:
            copy.mark = "point"
        return copy

    @use_signature(_MarkDef)
    def mark_bar(self, **kwds: Any) -> Self:
        """Set the chart's mark to 'bar'..."""
        ...

    # mark_line, mark_area, mark_rect 等...


class ConfigMethodMixin:
    """A mixin class that defines config methods"""

    @use_signature(core.Config)
    def configure(self, *args, **kwargs) -> Self:
        copy = self.copy(deep=False)
        copy.config = core.Config(*args, **kwargs)
        return copy

    @use_signature(core.AxisConfig)
    def configure_axis(self, *args, **kwargs) -> Self:
        copy = self.copy(deep=['config'])
        if copy.config is Undefined:
            copy.config = core.Config()
        copy.config["axis"] = core.AxisConfig(*args, **kwargs)
        return copy

    # configure_mark, configure_legend 等...
```

### 12.6 Mixin 协作关系详解

**核心机制**: Python 的 MRO（方法解析顺序）

```
类继承结构（以 X 通道为例）：

                    ┌─────────────────┐
                    │   SchemaBase    │  (属性存储、to_dict、验证)
                    └────────┬────────┘
                             │
                    ┌────────┴────────┐
                    │  VegaLiteSchema │  (_rootschema 管理)
                    └────────┬────────┘
                             │
                    ┌────────┴────────┐
                    │  core.FieldDef  │  (来自 core.py，schema 定义)
                    └────────┬────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
        ▼                    ▼                    ▼
┌───────────────┐    ┌───────────────┐    ┌───────────────┐
│FieldChannel   │    │ValueChannel   │    │DatumChannel   │
│    Mixin      │    │    Mixin      │    │    Mixin      │
│ (shorthand    │    │ (condition    │    │ (无特殊处理)   │
│  解析)        │    │  解析)        │    │               │
└───────────────┘    └───────────────┘    └───────────────┘
        │                    │                    │
        ▼                    ▼                    ▼
┌───────────────┐    ┌───────────────┐    ┌───────────────┐
│   X (通道类)  │    │  XValue       │    │  XDatum       │
│ (channels.py) │    │               │    │               │
└───────────────┘    └───────────────┘    └───────────────┘
```

**MRO 调用顺序**（以 `X.to_dict()` 为例）:

```
用户调用: x_obj.to_dict(validate=True)
           │
           ▼ 查找方法
    ┌──────────────────┐
    │  1. X.to_dict?   │ → 没有
    └────────┬─────────┘
             │
    ┌────────▼─────────┐
    │  2. FieldChannel │ → 找到了！
    │     Mixin        │    执行 shorthand 解析
    └────────┬─────────┘    放入 context["parsed_shorthand"]
             │              调用 super().to_dict()
             │
    ┌────────▼─────────┐
    │  3. core.FieldDef │ → 没有
    └────────┬─────────┘
             │
    ┌────────▼─────────┐
    │  4. VegaLiteSchema│ → 没有
    └────────┬─────────┘
             │
    ┌────────▼─────────┐
    │  5. SchemaBase   │ → 找到了！
    │                  │    从 context 取出 parsed_shorthand
    │                  │    应用到 kwds
    │                  │    执行 _todict 递归序列化
    │                  │    执行验证（如 validate=True）
    └──────────────────┘
```

**关键协作点**:

| 协作层 | 职责 | 调用时机 |
|--------|------|---------|
| `FieldChannelMixin.to_dict()` | 解析 shorthand，放入 context | 首先被调用 |
| `SchemaBase.to_dict()` | 从 context 取出解析结果，合并到 kwds | 通过 super() 调用 |
| `_replace_parsed_shorthand()` | 用户参数优先于解析结果 | SchemaBase 内部调用 |

### 12.7 三个文件的职责对比

| 维度 | core.py | channels.py | mixins.py |
|------|---------|-------------|-----------|
| **核心定位** | Schema 定义的直接映射 | 编码通道的用户友好封装 | 高层 API 方法增强 |
| **继承来源** | `SchemaBase` → `VegaLiteSchema` | `FieldChannelMixin + core.xxx` | 无基类（纯 Mixin） |
| **主要类** | `FieldDef`, `Axis`, `MarkDef`, `Config` 等 | `X`, `Y`, `Color`, `Size`, `XValue`, `ColorDatum` 等 | `MarkMethodMixin`, `ConfigMethodMixin` |
| **shorthand 支持** | 无 | 有（通过 Mixin） | 无 |
| `@overload` 方法 | 无 | 有（property setter） | 无 |
| **`_encoding_name`** | 无 | 有 | 无 |
| **延迟验证** | 否 | 是（`_class_is_valid_at_instantiation=False`） | 不适用 |
| **使用场景** | 内部数据结构、验证 | 用户直接使用（`.encode()`） | 混合到 `Chart` 等类中 |

---

## 13. `allOf` 与 `anyOf` 的不同处理路径

### 13.1 JSON Schema 语义区别

| 关键字 | 语义 | Python 对应 |
|--------|------|------------|
| `anyOf` | **满足任一**即可 | `Union[...]`（或类型） |
| `allOf` | **满足所有** | 继承/组合（与类型） |
| `oneOf` | **恰好满足一个** | 严格 Union（较少使用） |

**示例 Schema**:

```json
// anyOf: 可以是 string 或 number
{
  "anyOf": [
    {"type": "string"},
    {"type": "number"}
  ]
}

// allOf: 必须同时满足多个约束
{
  "allOf": [
    {"type": "object", "properties": {"x": {"type": "string"}}},
    {"type": "object", "properties": {"y": {"type": "number"}}}
  ]
}
// 等价于: {"type": "object", "properties": {"x": ..., "y": ...}}
```

### 13.2 `anyOf` 的处理路径

**核心处理**: 转换为 `Union[...]` 类型，用于生成 `@overload` 方法。

**检测方法** (`tools/schemapi/utils.py:765-766`):
```python
def is_anyOf(self) -> bool:
    return "anyOf" in self.schema
```

**类型表示转换** (`tools/schemapi/utils.py:491-496`):
```python
def to_type_repr(...):
    # ...
    elif self.is_anyOf():
        # 递归处理每个子类型
        it_nest = (
            s.to_type_repr(target=target, as_str=False, use_concrete=use_concrete)
            for s in self.anyOf
        )
        tps.update(maybe_rewrap_literal(chain.from_iterable(it_nest)))
    # ...
```

**子类型迭代器** (`tools/schemapi/utils.py:661-673`):
```python
@property
def anyOf(self) -> Iterator[SchemaInfo]:
    for s in self.schema.get("anyOf", []):
        yield self.child(s)  # 创建子 SchemaInfo，自动解析引用
```

**@overload 分发逻辑** (`tools/schemapi/codegen.py:423-447`):
```python
def overload_dispatch(self, prop: str, info: SchemaInfo, /) -> Iterator[str]:
    if info.is_anyOf():
        # 分组：可扁平化 vs 不可扁平化
        grouped = Grouped(info.anyOf, SchemaInfo.is_flattenable)
        
        # 特殊情况：单个复杂类型合并到可扁平化组
        if (expand := grouped.falsy) and len(expand) == 1 and expand[0].properties:
            grouped.truthy.append(expand[0])
        
        # 可扁平化 → 单个 @overload
        if flatten := grouped.truthy:
            yield from self.overload_signature(prop, flatten)
        
        # 不可扁平化 → 递归展开
        if expand := grouped.falsy:
            yield from self._overload_expand(prop, expand)
    else:
        yield from self.overload_signature(prop, info)
```

**递归展开** (`tools/schemapi/codegen.py:409-421`):
```python
def _overload_expand(
    self, prop: str, info: SchemaInfo | Iterable[SchemaInfo], /
) -> Iterator[str]:
    children: Iterable[SchemaInfo]
    if isinstance(info, SchemaInfo):
        # 如果是 anyOf 且不可扁平化，递归处理子类型
        children = info.anyOf if info.is_anyOf() else (info,)
    else:
        children = info
    
    for child in children:
        # 递归终止：子类型不再是不可扁平化的 anyOf
        if child.is_anyOf() and not child.is_union_flattenable():
            yield from self._overload_expand(prop, child)
        else:
            yield from self.overload_signature(prop, child)
```

**依赖关系图构建** (`tools/generate_schema_wrapper.py:743-753`):
```python
# 遍历 anyOf 中的引用，建立类继承关系
for name, schema in definitions.items():
    graph[name] = []
    for child_name in schema.subclasses():  # 遍历 anyOf 引用
        child_name = get_valid_identifier(child_name)
        graph[name].append(child_name)
        
        # 动态设置基类！
        child: SchemaGenerator = definitions[child_name]
        if child.basename == basename:
            child.basename = [name]
        else:
            child.basename.append(name)
```

**subclasses() 方法** (`tools/schemapi/codegen.py:240-252`):
```python
def subclasses(self) -> Iterator[str]:
    """
    返回 anyOf 中引用的类名。
    注意：这不是真正的 Python 子类，而是 schema 中的 Union 关系。
    """
    for child in SchemaInfo(self.schema, self.rootschema).anyOf:
        if child.is_reference():
            yield child.refname  # 如 "#/definitions/Foo" → "Foo"
```

### 13.3 `allOf` 的处理路径

**核心处理**: 在 Schema 解析阶段自动合并，代码生成阶段几乎不需要特殊处理。

**检测方法** (`tools/schemapi/utils.py:768-769`):
```python
def is_allOf(self) -> bool:
    return "allOf" in self.schema
```

**子类型迭代器** (`tools/schemapi/utils.py:676-678`):
```python
@property
def allOf(self) -> Iterator[SchemaInfo]:
    for s in self.schema.get("allOf", []):
        yield self.child(s)
```

**关键设计**: `allOf` 在 `SchemaInfo` 构造时就已经通过 `resolve_references` 合并了！

**引用解析** (`tools/schemapi/utils.py:247-355`):
```python
def resolve_references(
    schema: Mapping[str, Any],
    rootschema: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    解析 schema 中的 $ref 引用。
    关键：allOf 会被合并为单个 schema！
    """
    # ... 处理 $ref ...
    
    # 处理 allOf：合并所有子 schema 的属性
    if "allOf" in schema and rootschema is not None:
        out = {}
        for s in schema["allOf"]:
            # 递归解析每个 allOf 成员
            resolved = resolve_references(
                s, rootschema=rootschema, **kwargs
            )
            # 合并属性！
            for key, val in resolved.items():
                if key == "properties":
                    # properties 合并
                    if key in out:
                        out[key].update(val)
                    else:
                        out[key] = val
                elif key == "required":
                    # required 合并
                    if key in out:
                        out[key] = list(set(out[key] + val))
                    else:
                        out[key] = val
                else:
                    # 其他属性：如果已存在则跳过（不覆盖）
                    if key not in out:
                        out[key] = val
        return out
    
    # ... 处理 anyOf 等 ...
```

**代码生成中的 `allOf` 处理** (`tools/schemapi/codegen.py:147-157`):

```python
def get_args(info: SchemaInfo) -> ArgInfo:
    # ...
    else:
        nonkeyword = True
        additional = False
        if info.is_allOf():
            # 注意：这段代码实际上是"不可达"的！
            # 因为 resolve_references 已经把 allOf 合并了
            msg = f"Branch is reachable with:\n{info.raw_schema!r}"
            raise NotImplementedError(msg)
            
            # 概念上的处理（但实际不会执行）：
            arginfo: list[ArgInfo] = [get_args(child) for child in info.allOf]
            nonkeyword = all(args.nonkeyword for args in arginfo)
            required = {args.required for args in arginfo}
            kwds = {args.kwds for args in arginfo}
            kwds -= required
            invalid_kwds = {args.invalid_kwds for args in arginfo}
            additional = all(args.additional for args in arginfo)
```

### 13.4 两种处理路径对比

| 维度 | `anyOf` (Union) | `allOf` (组合/继承) |
|------|-----------------|---------------------|
| **处理阶段** | 代码生成（运行时仍保留） | Schema 解析（预合并） |
| **核心机制** | `Grouped` 分类 + `@overload` 分发 | `resolve_references` 合并属性 |
| **类型表示** | `Union[A, B, C]` | 单个对象类型（合并后） |
| **对继承的影响** | 建立类依赖关系图（拓扑排序用） | 无影响（已合并） |
| **属性合并** | 不合并（独立选项） | 合并 properties/required |
| **代码生成器方法** | `subclasses()`, `overload_dispatch()`, `_overload_expand()` | 无专门方法（解析阶段已处理） |
| **典型场景** | 编码通道的多种输入形式 | Schema 复用、属性组合 |

### 13.5 实际示例分析

**场景 1：编码通道的 `anyOf`**

```json
// Vega-Lite Schema 中 X 通道的定义
{
  "anyOf": [
    {"$ref": "#/definitions/FieldDef"},
    {"$ref": "#/definitions/ValueDef"},
    {"type": "null"}
  ]
}
```

**处理流程**:
```
1. SchemaInfo 解析 anyOf
   → 三个子类型：FieldDef (ref), ValueDef (ref), null

2. subclasses() 遍历引用
   → 产出 "FieldDef", "ValueDef"

3. 依赖图构建
   graph["X"] = ["FieldDef", "ValueDef"]
   
   → 如果 FieldDef 的 basename 是 VegaLiteSchema
   → 改为 [X] 或追加 X

4. @overload 生成
   → 检查每个子类型是否可扁平化
   → 可扁平化：合并为单个 @overload
   → 不可扁平化：递归展开
```

**场景 2：FieldDef 的 `allOf`**

```json
// Vega-Lite Schema 中 FieldDef 的定义
{
  "allOf": [
    {
      "type": "object",
      "properties": {
        "field": {"type": "string"},
        "type": {"type": "string", "enum": ["Q", "N", "O", "T"]}
      }
    },
    {
      "type": "object", 
      "properties": {
        "aggregate": {"type": "string"},
        "sort": {"anyOf": [{"type": "string"}, {"$ref": "#/definitions/SortDef"}]}
      }
    }
  ]
}
```

**处理流程**:
```
1. resolve_references 处理 allOf
   → 遍历两个子 schema
   → 合并 properties
   → 合并 required（如有）

2. 结果：单个合并的 schema
   {
     "type": "object",
     "properties": {
       "field": {"type": "string"},
       "type": {...},
       "aggregate": {"type": "string"},
       "sort": {...}
     }
   }

3. SchemaInfo 看到的是普通 object，不再有 allOf
   → is_allOf() 返回 False
   → 像普通对象一样处理
```

### 13.6 为什么 `allOf` 在解析阶段合并？

**设计原因**:

1. **语义等价性**
   ```json
   // 这两个 schema 是等价的
   {
     "allOf": [
       {"properties": {"a": {...}}},
       {"properties": {"b": {...}}}
     ]
   }
   
   // 等价于
   {
     "properties": {"a": {...}, "b": {...}}
   }
   ```

2. **简化后续处理**
   - 代码生成器不需要处理"多继承"的复杂情况
   - `to_dict()` 验证时，jsonschema 会处理 allOf 语义

3. **`anyOf` 不能合并的原因**
   - `anyOf` 是"或"的关系，不能合并为单个类型
   - 需要保留所有选项供 `@overload` 生成
   - 用户可能传入任意一种类型

### 13.7 注意事项：`is_allOf()` 何时返回 True？

**重要**: 由于 `resolve_references` 已经合并了 `allOf`，`SchemaInfo.is_allOf()` 几乎总是返回 `False`。

**唯一可能返回 True 的情况**:
- `rootschema` 为 `None`（无法解析引用）
- schema 中 `allOf` 的子 schema 有冲突，无法完全合并
- 直接访问 `raw_schema` 而非 `schema`

**代码示例**:
```python
info = SchemaInfo(schema_with_allOf, rootschema=root_schema)

info.is_allOf()          # False (schema 已合并)
"allOf" in info.schema    # False
"allOf" in info.raw_schema  # True (原始 schema)

# 手动遍历 allOf（仅用于特殊场景）
for child in SchemaInfo(info.raw_schema, rootschema=None).allOf:
    # ...
```

---

## 14. 补充总结

### 14.1 Shorthand 解析流程速查表

```
用户输入: "x" → alt.X("price:Q", data=df)
           │
           ▼
┌────────────────────────────────────┐
│ 1. SchemaBase.__init__             │
│    _kwds = {"shorthand": "price:Q"}│
└────────────────────────────────────┘
           │
           ▼ to_dict() 调用
┌────────────────────────────────────┐
│ 2. FieldChannelMixin.to_dict()     │
│    - 检测到 shorthand 是字符串      │
│    - 从 context 获取 data           │
│    - 调用 parse_shorthand()         │
└────────────────────────────────────┘
           │
           ▼
┌────────────────────────────────────┐
│ 3. parse_shorthand()               │
│    - 正则匹配: "price:Q"           │
│      → {"field": "price", "type": "Q"} │
│    - 类型转换: "Q" → "quantitative"  │
│    - 检查类型是否需要               │
│    - 如需要且缺失，从数据推断       │
└────────────────────────────────────┘
           │
           ▼
┌────────────────────────────────────┐
│ 4. context 传递                    │
│    context["parsed_shorthand"] =   │
│      {"field": "price", "type": "quantitative"} │
└────────────────────────────────────┘
           │
           ▼
┌────────────────────────────────────┐
│ 5. SchemaBase.to_dict()            │
│    - 从 context 取出解析结果        │
│    - _replace_parsed_shorthand()   │
│      → 合并到 kwds                  │
│    - 过滤 shorthand                 │
│    - 递归序列化 + 验证              │
└────────────────────────────────────┘
```

### 14.2 三文件协作关系表

| 文件 | 核心基类/Mixin | 关键特性 | 被谁依赖 |
|------|----------------|---------|---------|
| `core.py` | `VegaLiteSchema(SchemaBase)` | 所有 definition 的直接映射 | `channels.py`, `mixins.py` |
| `channels.py` | `FieldChannelMixin`, `ValueChannelMixin`, `DatumChannelMixin` | shorthand 解析、`@overload` setter、延迟验证 | 用户代码（`.encode()`） |
| `mixins.py` | `MarkMethodMixin`, `ConfigMethodMixin` | `mark_xxx()`, `configure_xxx()` 便利方法 | `api.py`（混合到 Chart 等） |

### 14.3 `anyOf` vs `allOf` 处理对比

| 特性 | `anyOf` | `allOf` |
|------|---------|---------|
| **处理时机** | 代码生成阶段 | Schema 解析阶段 |
| **核心函数** | `overload_dispatch()`, `_overload_expand()` | `resolve_references()` |
| **对继承的影响** | 建立依赖关系图 | 无（已合并） |
| **类型表示** | `Union[...]` | 单个类型 |
| **属性处理** | 各自独立 | 合并 properties/required |
| **`is_xxx()` 返回值** | 通常 True | 通常 False（已合并） |

### 14.4 关键设计亮点（补充）

1. **Shorthand 的"两阶段"解析**
   - 代码生成阶段：动态注入 `shorthand` 属性到 schema
   - 运行时阶段：通过 Mixin 的 `to_dict()` 解析，通过 context 传递结果
   - 优点：不修改原始 Vega-Lite Schema，保持兼容性

2. **Mixin + 多重继承的优雅协作**
   - `FieldChannelMixin` 处理 shorthand
   - `core.FieldDef` 提供 schema 定义
   - `SchemaBase` 提供基础功能
   - Python MRO 确保正确的调用顺序

3. **`allOf` 预合并的简化设计**
   - 在解析阶段就处理组合语义
   - 代码生成器只需处理普通对象类型
   - 避免了复杂的"多继承"代码生成逻辑

### 14.5 补充参考文件位置

| 文件 | 说明 |
|------|------|
| `altair/utils/core.py` | `parse_shorthand()`、类型推断、`infer_encoding_types()` |
| `altair/vegalite/v6/schema/channels.py` | 通道类定义、`FieldChannelMixin` 等 Mixin（生成的） |
| `altair/vegalite/v6/schema/mixins.py` | `MarkMethodMixin`、`ConfigMethodMixin`（生成的） |
| `tools/generate_schema_wrapper.py` | 三文件生成逻辑、`CHANNEL_MIXINS` 模板、`load_schema_with_shorthand_properties()` |
| `altair/utils/schemapi.py` | `with_property_setters`、`_PropertySetter`、`_replace_parsed_shorthand` |

---

## 15. `from_dict()` 反序列化路径分析

### 15.1 概述

`from_dict()` 是 `to_dict()` 的反向操作，用于将普通 Python 字典（或 JSON 反序列化的结果）重建为 `SchemaBase` 子类实例。

**典型使用场景**:
```python
# 从保存的 JSON 重建图表
import json
dct = json.load(open("chart.json"))
chart = alt.Chart.from_dict(dct)

# 或者直接使用
dct = {
    "mark": "point",
    "encoding": {
        "x": {"field": "price", "type": "quantitative"},
        "y": {"field": "count", "type": "quantitative"}
    }
}
chart = alt.Chart.from_dict(dct)

# 验证 round-trip
assert Chart.from_dict(obj.to_dict()).to_dict() == obj.to_dict()
```

### 15.2 入口：`SchemaBase.from_dict()`

**文件位置**: `altair/utils/schemapi.py:1285-1307`

```python
@classmethod
def from_dict(
    cls: type[TSchemaBase], dct: dict[str, Any], validate: bool = True
) -> TSchemaBase:
    """
    Construct class from a dictionary representation.
    
    Parameters
    ----------
    dct : dictionary
        The dict from which to construct the class
    validate : boolean
        If True (default), then validate the input against the schema.
    """
    # 步骤1：可选验证
    if validate:
        cls.validate(dct)  # 使用 jsonschema 验证 dict 格式
    
    # 步骤2：创建 _FromDict 转换器
    # _default_wrapper_classes() 返回所有 SchemaBase 的子类
    converter = _FromDict(cls._default_wrapper_classes())
    
    # 步骤3：执行实际的反序列化
    return converter.from_dict(dct, cls)
```

### 15.3 `_FromDict` 转换器类

**文件位置**: `altair/utils/schemapi.py:1469-1623`

**核心设计**：`_FromDict` 维护一个"schema 哈希 → 类列表"的映射表，用于快速查找与给定 schema 匹配的 Python 类。

```python
class _FromDict:
    """
    Class used to construct SchemaBase class hierarchies from a dict.
    
    The primary purpose of using this class is to be able to build a hash table
    that maps schemas to their wrapper classes.
    """
    
    # 计算哈希时排除的键（不影响类型匹配的元数据）
    _hash_exclude_keys = ("definitions", "title", "description", "$schema", "id")

    def __init__(self, wrapper_classes: Iterable[type[SchemaBase]], /) -> None:
        # Create a mapping of a schema hash to a list of matching classes
        # This lets us quickly determine the correct class to construct
        self.class_dict: dict[int, list[type[SchemaBase]]] = defaultdict(list)
        
        # 遍历所有 SchemaBase 子类，构建哈希映射
        for tp in wrapper_classes:
            if tp._schema is not None:
                # 计算 schema 的哈希值
                schema_hash = self.hash_schema(tp._schema)
                # 相同哈希的类放在同一个列表中（可能有多个类匹配同一 schema）
                self.class_dict[schema_hash].append(tp)
```

### 15.4 Schema 哈希计算

**文件位置**: `altair/utils/schemapi.py:1488-1521`

```python
@classmethod
def hash_schema(cls, schema: dict[str, Any], use_json: bool = True) -> int:
    """
    Compute a python hash for a nested dictionary which properly handles dicts, lists, sets, and tuples.
    
    At the top level, the function excludes from the hashed schema all keys
    listed in `exclude_keys` (definitions, title, description, $schema, id).
    """
    # 步骤1：排除不影响类型匹配的键
    if cls._hash_exclude_keys and isinstance(schema, dict):
        schema = {
            key: val
            for key, val in schema.items()
            if key not in cls._hash_exclude_keys
        }
    
    # 步骤2：两种哈希方式（默认使用 JSON 方式）
    if use_json:
        # JSON 序列化 + 字符串哈希
        s = json.dumps(schema, sort_keys=True)
        return hash(s)
    else:
        # 递归冻结为可哈希类型
        def _freeze(val):
            if isinstance(val, dict):
                return frozenset((k, _freeze(v)) for k, v in val.items())
            elif isinstance(val, set):
                return frozenset(map(_freeze, val))
            elif isinstance(val, (list, tuple)):
                return tuple(map(_freeze, val))
            else:
                return val
        
        return hash(_freeze(schema))
```

**哈希设计要点**:
- `_hash_exclude_keys` 排除 `definitions` 等元数据，因为它们不影响类型匹配
- `sort_keys=True` 确保字典键顺序不影响哈希
- 两种方式（JSON 方式通常更快）

### 15.5 `_FromDict.from_dict()` 核心逻辑

**文件位置**: `altair/utils/schemapi.py:1568-1623`

这是反序列化的核心递归函数。

```python
def from_dict(
    self,
    dct: dict[str, Any] | list[dict[str, Any]] | TSchemaBase,
    tp: type[TSchemaBase] | None = None,
    schema: dict[str, Any] | None = None,
    rootschema: dict[str, Any] | None = None,
    default_class: Any = _passthrough,
) -> TSchemaBase | SchemaBase:
    """Construct an object from a dict representation."""
    
    target_tp: Any        # 最终要实例化的类
    current_schema: dict[str, Any]  # 当前使用的 schema

    # ===== 阶段1：确定目标类型和 schema =====
    
    # 情况1：已经是 SchemaBase 实例，直接返回
    if isinstance(dct, SchemaBase):
        return dct
    
    # 情况2：明确指定了目标类型 tp
    elif tp is not None:
        current_schema = tp._schema
        # 使用 tp 的 _rootschema 作为根
        root_schema: dict[str, Any] = rootschema or tp._rootschema or current_schema
        target_tp = tp
    
    # 情况3：提供了 schema，通过哈希查找匹配的类
    elif schema is not None:
        # 如果有多个匹配，使用第一个（class_dict 是广度优先构建的，第一个是最通用的）
        current_schema = schema
        root_schema = rootschema or current_schema
        # 通过哈希查找匹配的类列表
        matches = self.class_dict[self.hash_schema(current_schema)]
        target_tp = matches[0] if matches else default_class
    
    else:
        msg = "Must provide either `tp` or `schema`, but not both."
        raise ValueError(msg)

    # ===== 阶段2：准备递归调用 =====
    
    # 创建部分应用函数（绑定 root_schema）
    from_dict = partial(self.from_dict, rootschema=root_schema)
    
    # 关键：解析当前 schema 中的 $ref 引用
    # 这是运行时 $ref 解析的核心入口！
    resolved = _resolve_references(current_schema, root_schema)

    # ===== 阶段3：处理 anyOf/oneOf（Union 类型） =====
    
    if "anyOf" in resolved or "oneOf" in resolved:
        # 收集所有可能的 schema
        schemas = resolved.get("anyOf", []) + resolved.get("oneOf", [])
        
        # 逐个尝试验证，找到第一个匹配的 schema
        for possible in schemas:
            try:
                # 使用 jsonschema 验证 dct 是否符合 possible schema
                validate_jsonschema(dct, possible, rootschema=root_schema)
            except jsonschema.ValidationError:
                # 不匹配，继续下一个
                continue
            else:
                # 找到匹配的！递归使用这个 schema
                return from_dict(dct, schema=possible, default_class=target_tp)

    # ===== 阶段4：根据数据类型递归处理 =====
    
    # 情况A：dict 类型 → 递归处理每个属性
    if _is_dict(dct):
        # 获取 schema 中定义的 properties
        props: dict[str, Any] = resolved.get("properties", {})
        
        # 遍历 dict 的每个键值对
        # 对于在 schema properties 中定义的键，递归调用 from_dict
        # 对于未定义的键（additionalProperties），保持原值
        kwds = {
            k: (from_dict(v, schema=props[k]) if k in props else v)
            for k, v in dct.items()
        }
        
        # 实例化目标类
        return target_tp(**kwds)
    
    # 情况B：list 类型 → 递归处理每个元素
    elif _is_list(dct):
        # 获取 items schema（数组元素的类型）
        item_schema: dict[str, Any] = resolved.get("items", {})
        
        # 对每个元素递归调用 from_dict
        return target_tp([from_dict(k, schema=item_schema) for k in dct])
    
    # 情况C：其他类型（基本类型）→ 直接传入
    else:
        # NOTE: Unsure what is valid here
        return target_tp(dct)
```

### 15.6 API 层的 `Chart.from_dict()`

**文件位置**: `altair/vegalite/v6/api.py:4114-4141`

`Chart` 类有一个特殊的 `from_dict()` 实现，用于智能识别图表类型。

```python
@classmethod
def from_dict(
    cls: type[_TSchemaBase], dct: dict[str, Any], validate: bool = True
) -> _TSchemaBase:
    """
    Construct a ``Chart`` from a dictionary representation.
    
    特殊行为：智能识别图表类型
    """
    _tp: Any
    
    # 步骤1：遍历所有 TopLevelMixin 子类尝试匹配
    # TopLevelMixin 子类包括：Chart, LayerChart, FacetChart, HConcatChart, VConcatChart, RepeatChart
    for tp in TopLevelMixin.__subclasses__():
        # 对于 Chart 类自身，使用 super()（即 SchemaBase.from_dict）
        _tp = super() if tp is Chart else tp
        try:
            # 尝试用当前类型反序列化
            return _tp.from_dict(dct, validate=validate)
        except jsonschema.ValidationError:
            # 验证失败，继续下一个类型
            pass

    # 步骤2：最后尝试用 core.Root（最通用的顶层 spec）
    return t.cast("_TSchemaBase", core.Root.from_dict(dct, validate))
```

**设计意图**:
- 用户保存的 JSON 可能是任意类型的图表（layered, faceted, concatenated 等）
- 通过遍历 `TopLevelMixin` 子类，自动识别并返回正确的类型
- 最后兜底使用 `core.Root`，它可以匹配任何 Vega-Lite spec

### 15.7 反序列化完整流程图

```
用户调用: Chart.from_dict(dct)
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│ 1. Chart.from_dict() (api.py)                                 │
│    - 遍历 TopLevelMixin 子类（Chart, LayerChart, 等）        │
│    - 逐个尝试 from_dict，找到第一个验证通过的                  │
│    - 最后兜底 core.Root                                       │
└──────────────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│ 2. SchemaBase.from_dict() (schemapi.py:1285-1307)          │
│    - 验证 dct 是否符合 cls._schema                            │
│    - 创建 _FromDict 转换器                                    │
│    - 调用 converter.from_dict(dct, cls)                       │
└──────────────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│ 3. _FromDict.from_dict() (schemapi.py:1568-1623)            │
│    │
│    ├──► 阶段1：确定目标类型
│    │     - tp 已知 → 使用 tp._schema 和 tp._rootschema
│    │     - schema 已知 → 哈希查找 class_dict
│    │
│    ├──► 阶段2：运行时 $ref 解析
│    │     resolved = _resolve_references(current_schema, root_schema)
│    │
│    ├──► 阶段3：处理 Union (anyOf/oneOf)
│    │     - 逐个尝试验证，找到匹配的 schema
│    │     - 递归调用 from_dict 使用匹配的 schema
│    │
│    └──► 阶段4：根据数据类型递归
│          ├──► dict: 遍历 properties，递归每个属性
│          ├──► list: 遍历 items，递归每个元素
│          └──► 其他: 直接 target_tp(dct)
└──────────────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│ 4. SchemaBase.__init__()                                      │
│    - 存储 _args 和 _kwds                                       │
│    - 如果 DEBUG_MODE，执行即时验证                             │
└──────────────────────────────────────────────────────────────┘
```

### 15.8 反序列化示例分析

**示例数据**:
```python
dct = {
    "mark": "point",
    "encoding": {
        "x": {"field": "price", "type": "quantitative"},
        "y": {"aggregate": "count", "type": "quantitative"}
    }
}
```

**执行流程**:

```
1. Chart.from_dict(dct)
   → 验证 dct 符合 Chart 的 schema
   → 创建 _FromDict，包含所有 SchemaBase 子类
   → 调用 converter.from_dict(dct, Chart)

2. converter.from_dict(dct, tp=Chart)
   → current_schema = Chart._schema
   → root_schema = Chart._rootschema (完整的 Vega-Lite schema)
   → resolved = _resolve_references(Chart._schema, root_schema)
        → 展开 $ref，得到完整的 spec 定义
   
   → 检查 resolved 有没有 anyOf/oneOf?
        → 如果是顶层 spec，可能有（如 UnitSpec | LayerSpec 等）
        → 逐个验证，找到匹配的
   
   → 处理 dict 类型
        → props = resolved.get("properties", {})
            → 包括 mark, encoding, data, transform 等
        → kwds = {
            "mark": dct["mark"],  # 字符串，不在 properties? 直接保留
            "encoding": from_dict(
                {"x": ..., "y": ...},
                schema=props["encoding"]  # FacetedEncoding 的 schema
            )
          }

3. 递归处理 encoding
   → encoding 的 schema 是 {"$ref": "#/definitions/FacetedEncoding"}
   → resolved = _resolve_references(encoding_schema, root_schema)
        → 展开得到 properties: x, y, color, size, 等
   
   → 处理 dict: {"x": ..., "y": ...}
        → props["x"] 的 schema 是 anyOf [...]
            → 检查 anyOf，验证 {"field": "price", "type": "quantitative"}
            → 找到匹配的 schema（如 FieldDef）
            → 递归 from_dict 使用 FieldDef schema
        → 同理处理 y

4. 递归处理 x: {"field": "price", "type": "quantitative"}
   → 匹配到 FieldDef schema
   → 处理 dict
        → props["field"] 是 string 类型 → 不递归，直接保留
        → props["type"] 是 enum 类型 → 直接保留
   → 返回 FieldDef(field="price", type="quantitative")

5. 最终实例化
   → Chart(
        mark="point",
        encoding=FacetedEncoding(
            x=FieldDef(field="price", type="quantitative"),
            y=FieldDef(aggregate="count", type="quantitative")
        )
     )
```

### 15.9 关键设计点

| 设计点 | 说明 |
|--------|------|
| **Schema 哈希映射** | 预计算所有类的 schema 哈希，O(1) 查找匹配类 |
| **`_rootschema` 运行时解析** | 反序列化时动态展开 `$ref`，不需要预展开所有 schema |
| **anyOf/oneOf 尝试验证** | 对于 Union 类型，逐个尝试 jsonschema 验证找到匹配 |
| **按 properties 递归** | 只有在 schema properties 中定义的键才递归反序列化 |
| **Chart 智能识别** | 遍历 `TopLevelMixin` 子类自动识别图表类型 |

---

## 16. 运行时 `$ref` 引用解析链

### 16.1 概述

`$ref` 是 JSON Schema 的引用机制，允许在 schema 中引用其他定义。Altair 在两个阶段处理 `$ref`：

| 阶段 | 处理方式 | 目的 |
|------|---------|------|
| **代码生成阶段** | 解析并合并（`allOf`），提取类型信息 | 生成正确的 Python 类型和类继承关系 |
| **运行时阶段** | 动态展开 `$ref` | 验证、反序列化、属性访问 |

### 16.2 `_rootschema` 类属性

**文件位置**: `altair/utils/schemapi.py:1073-1074`

```python
class SchemaBase:
    _schema: ClassVar[dict[str, Any] | Any] = None
    _rootschema: ClassVar[dict[str, Any] | None] = None
```

**设计意图**:
- `_schema`: 当前类对应的 schema（通常是 `$ref` 引用，如 `{'$ref': '#/definitions/FieldDef'}`）
- `_rootschema`: **完整的根 schema**，包含所有 `definitions`（用于解析 `$ref` 引用）

**生成的代码示例** (`core.py`):
```python
class VegaLiteSchema(SchemaBase):
    # _rootschema 存储完整的 Vega-Lite schema（包括所有 definitions）
    _rootschema = load_schema()
    
    @classmethod
    def _default_wrapper_classes(cls) -> Iterator[type[Any]]:
        return _subclasses(VegaLiteSchema)


class FieldDef(VegaLiteSchema):
    # _schema 只是一个 $ref 引用
    _schema = {'$ref': '#/definitions/FieldDef'}
    # _rootschema 继承自 VegaLiteSchema（完整 schema）
    _rootschema = VegaLiteSchema._rootschema
```

### 16.3 `_resolve_references()` 运行时解析

**文件位置**: `altair/utils/schemapi.py:562-581`

这是运行时 `$ref` 解析的核心函数。

```python
def _resolve_references(
    schema: dict[str, Any], rootschema: dict[str, Any] | None = None
) -> dict[str, Any]:
    """
    Resolve schema references until there is no $ref anymore in the top-level of the dictionary.
    
    注意：只解析顶层的 $ref，不递归解析嵌套的 $ref（除非展开后还有 $ref）。
    """
    
    # 两种实现方式：使用 referencing 库 或 jsonschema.RefResolver
    
    if _use_referencing_library():
        # 现代方式：使用 referencing 库
        registry = _get_referencing_registry(rootschema or schema)
        referencing_resolver = registry.resolver()
        
        # 循环解析直到没有 $ref
        while "$ref" in schema:
            # 构建完整 URI 并查找
            # _VEGA_LITE_ROOT_URI = "https://vega.github.io/schema/vega-lite/v6.json"
            schema = referencing_resolver.lookup(
                _VEGA_LITE_ROOT_URI + schema["$ref"]
            ).contents
    else:
        # 传统方式：使用 jsonschema.RefResolver
        resolver = jsonschema.RefResolver.from_schema(rootschema or schema)
        
        while "$ref" in schema:
            with resolver.resolving(schema["$ref"]) as resolved:
                schema = resolved
    
    return schema
```

**关键特性**:
1. **循环解析**：如果展开后的 schema 仍然有 `$ref`，继续解析
2. **顶层解析**：只处理顶层的 `$ref`，不自动递归解析嵌套属性中的 `$ref`
3. **两种实现**：优先使用 `referencing` 库（更现代），回退到 `jsonschema.RefResolver`

### 16.4 `SchemaBase.resolve_references()` 方法

**文件位置**: `altair/utils/schemapi.py:1350-1358`

提供便捷的类方法，使用类自身的 `_schema` 和 `_rootschema`。

```python
@classmethod
def resolve_references(cls, schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve references in the context of this object's schema or root schema."""
    # 使用传入的 schema，或类的 _schema
    schema_to_pass = schema or cls._schema
    
    # 使用类的 _rootschema 作为根
    return _resolve_references(
        schema=schema_to_pass,
        rootschema=(cls._rootschema or cls._schema or schema),
    )
```

### 16.5 运行时 `$ref` 解析的使用场景

`_resolve_references` 在以下运行时场景被调用：

#### 场景1：`from_dict()` 反序列化

**文件位置**: `altair/utils/schemapi.py:1599`

```python
def from_dict(...):
    # ...
    resolved = _resolve_references(current_schema, root_schema)
    # 使用 resolved schema 处理 anyOf、properties、items 等
    if "anyOf" in resolved or "oneOf" in resolved:
        schemas = resolved.get("anyOf", []) + resolved.get("oneOf", [])
        # ...
    props: dict[str, Any] = resolved.get("properties", {})
    # ...
```

**目的**：展开 `$ref` 后才能正确访问 `properties`、`anyOf` 等关键字。

#### 场景2：`validate()` 验证

**文件位置**: `altair/utils/schemapi.py:1338-1347`

```python
@classmethod
def validate(cls, instance: dict[str, Any], schema: dict[str, Any] | None = None) -> None:
    if schema is None:
        schema = cls._schema
    # 注意：这里没有显式调用 resolve_references
    # 而是直接传递给 validate_jsonschema，由 jsonschema 内部处理
    
    validate_jsonschema(instance, schema, rootschema=cls._rootschema or cls._schema)
```

**关键**：`validate_jsonschema` 本身会使用 `rootschema` 解析 `$ref`，不需要预展开。

#### 场景3：`validate_property()` 验证单个属性

**文件位置**: `altair/utils/schemapi.py:1360-1370`

```python
@classmethod
def validate_property(cls, name: str, value: Any, schema: dict[str, Any] | None = None) -> None:
    # 先解析引用以获取 properties
    props = cls.resolve_references(schema or cls._schema).get("properties", {})
    # 使用具体属性的 schema 验证
    validate_jsonschema(
        value, props.get(name, {}), rootschema=cls._rootschema or cls._schema
    )
```

**目的**：需要展开 `$ref` 才能访问 `properties` 字典。

#### 场景4：`with_property_setters` 装饰器

**文件位置**: `altair/utils/schemapi.py:1675-1680`

```python
def with_property_setters(cls: type[TSchemaBase]) -> type[TSchemaBase]:
    """Decorator to add property setters to a Schema class."""
    # 解析引用以获取完整的 properties 列表
    schema = cls.resolve_references()
    for prop, propschema in schema.get("properties", {}).items():
        # 为每个属性创建 _PropertySetter 描述符
        setattr(cls, prop, _PropertySetter(prop, propschema))
    return cls
```

**目的**：需要展开 `$ref` 才能知道有哪些属性需要创建 setter。

### 16.6 代码生成阶段 vs 运行时阶段对比

#### 代码生成阶段的 `$ref` 处理

**文件位置**: `tools/schemapi/utils.py:372-375`

```python
class SchemaInfo:
    def __init__(self, schema: Mapping[str, Any], rootschema: Mapping[str, Any] | None = None) -> None:
        object.__setattr__(self, "raw_schema", schema)
        object.__setattr__(self, "rootschema", rootschema)
        # 关键：在构造时就解析引用！
        object.__setattr__(self, "schema", resolve_references(schema, rootschema))
```

**代码生成阶段的特点**：

| 特点 | 说明 |
|------|------|
| **即时解析** | `SchemaInfo` 构造时就调用 `resolve_references` |
| **`raw_schema` 保留原始** | 通过 `self.raw_schema` 可以访问原始 `$ref` |
| **`allOf` 合并** | `resolve_references` 同时合并 `allOf` 的属性 |
| **`refname` 提取** | 从 `$ref` 提取类名用于继承关系 |

**代码生成中使用 `$ref` 的场景**：

```python
# 场景1：获取类名（用于继承关系）
@property
def refname(self) -> str:
    # 从 raw_schema 提取，不使用已解析的 schema
    return self.raw_schema.get("$ref", "#/").split("/")[-1]

def is_reference(self) -> bool:
    # 检查 raw_schema，不使用已解析的 schema
    return "$ref" in self.raw_schema

# 场景2：构建依赖关系图（subclasses()）
def subclasses(self) -> Iterator[str]:
    # 遍历 anyOf 中的引用
    for child in SchemaInfo(self.schema, self.rootschema).anyOf:
        if child.is_reference():
            yield child.refname
```

#### 两阶段对比总结

| 维度 | 代码生成阶段 | 运行时阶段 |
|------|-------------|-----------|
| **核心函数** | `tools.schemapi.schemapi._resolve_references` (导入为 `resolve_references`) | `altair.utils.schemapi._resolve_references` |
| **解析时机** | `SchemaInfo` 构造时即时解析 | 需要时动态解析（延迟） |
| **数据存储** | `self.schema` 存储解析后，`self.raw_schema` 保留原始 | 类属性 `_schema` 保留 `$ref`，`_rootschema` 存储完整定义 |
| **allOf 处理** | 合并到 `properties` | 交给 jsonschema 处理 |
| **主要用途** | 类型推断、类继承、`@overload` 生成 | 验证、反序列化、属性访问 |
| **引用展开** | 完整展开，用于代码生成 | 按需展开，通常只展开顶层 |

### 16.7 为什么需要两个阶段？

**设计原因分析**：

1. **代码生成阶段：需要完整信息**
   - 要生成正确的类型注解，必须知道 schema 的完整结构
   - 要建立类继承关系，必须知道 `anyOf` 中的引用指向哪个类
   - `allOf` 必须合并才能知道完整的 `properties` 列表

2. **运行时阶段：延迟解析更高效**
   - 不是所有 `$ref` 都需要展开
   - 保持 `_schema` 为 `$ref` 引用可以节省内存（不需要复制整个 schema）
   - `jsonschema` 内部会处理 `$ref`，不需要预展开

3. **关注点分离**
   - 代码生成：静态分析、类型推导、代码结构
   - 运行时：动态处理、验证、序列化/反序列化

### 16.8 `$ref` 解析完整流程图

```
┌─────────────────────────────────────────────────────────────────────┐
│                        代码生成阶段                                   │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 1. SchemaInfo.__init__(schema, rootschema)                          │
│    │                                                                  │
│    ├──► self.raw_schema = schema (保留原始 $ref)                    │
│    ├──► self.rootschema = rootschema                                │
│    └──► self.schema = resolve_references(schema, rootschema)       │
│         │                                                            │
│         ├──► 循环解析顶层 $ref                                      │
│         ├──► 合并 allOf 的 properties                               │
│         └──► 返回"展开后"的 schema                                  │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 2. 代码生成使用                                                       │
│    │                                                                  │
│    ├──► SchemaInfo.refname: 从 raw_schema 提取类名                  │
│    ├──► SchemaInfo.is_reference(): 检查 raw_schema 有无 $ref        │
│    ├──► subclasses(): 遍历 anyOf 中的引用建立依赖关系               │
│    └──► to_type_repr(): 使用展开的 schema 生成类型注解               │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 3. 生成的代码                                                         │
│    class VegaLiteSchema(SchemaBase):                                 │
│        _rootschema = load_schema()  # 完整 schema (含 definitions)  │
│                                                                    │
│    class FieldDef(VegaLiteSchema):                                   │
│        _schema = {'$ref': '#/definitions/FieldDef'}  # 保留 $ref   │
│        _rootschema = VegaLiteSchema._rootschema                      │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ 运行时
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        运行时阶段                                     │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 场景1: from_dict() 反序列化                                           │
│    resolved = _resolve_references(current_schema, root_schema)      │
│         │                                                             │
│         ├──► 使用 current_schema (可能是 $ref)                       │
│         ├──► 使用 root_schema (完整 schema) 解析                     │
│         └──► 返回展开后的 schema                                      │
│                                                                    │
│    → 用于: 检查 anyOf, 获取 properties, 递归反序列化                 │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 场景2: validate_property() 验证                                       │
│    props = cls.resolve_references().get("properties", {})           │
│    → 展开 $ref 以访问 properties 字典                                 │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 场景3: with_property_setters 装饰器                                   │
│    schema = cls.resolve_references()                                  │
│    → 展开 $ref 以获取完整的 properties 列表                           │
│    → 为每个属性创建 _PropertySetter                                    │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 场景4: validate() 验证（不需要预展开）                                 │
│    validate_jsonschema(instance, schema, rootschema=...)             │
│    → jsonschema 内部使用 rootschema 解析 $ref                        │
│    → 不需要显式调用 resolve_references                                 │
└─────────────────────────────────────────────────────────────────────┘
```

### 16.9 关键数据结构对比

| 结构 | 代码生成阶段 | 运行时阶段 |
|------|-------------|-----------|
| **`_schema`** | 不使用（使用 `SchemaInfo.schema`） | 类属性，保留 `$ref` 引用 |
| **`_rootschema`** | 作为参数传入 `SchemaInfo` | 类属性，存储完整 schema（含 definitions） |
| **`SchemaInfo.raw_schema`** | 保留原始 `$ref`，用于 `refname`、`is_reference()` | 不使用 |
| **`SchemaInfo.schema`** | 已展开，用于类型分析 | 不使用 |

---

## 17. 第二轮补充总结

### 17.1 `from_dict()` 反序列化速查表

```
输入: dct = {"mark": "point", "encoding": {"x": {...}, "y": {...}}}
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│ 1. 入口: SchemaBase.from_dict(dct, validate=True)            │
│    - 验证 dct 符合 cls._schema                                 │
│    - 创建 _FromDict 转换器                                     │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│ 2. _FromDict 初始化                                            │
│    - 遍历所有 SchemaBase 子类                                   │
│    - 计算每个类 _schema 的哈希值                                │
│    - 构建 class_dict: {hash: [Class1, Class2, ...]}          │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│ 3. _FromDict.from_dict() 核心逻辑                             │
│    │
│    ├──► 阶段1：确定目标类型
│    │     - tp 已知: 使用 tp._schema 和 tp._rootschema
│    │     - schema 已知: 哈希查找 class_dict
│    │
│    ├──► 阶段2：运行时 $ref 解析
│    │     resolved = _resolve_references(current_schema, root_schema)
│    │
│    ├──► 阶段3：处理 Union (anyOf/oneOf)
│    │     - 逐个尝试 jsonschema 验证
│    │     - 找到第一个匹配的 schema
│    │     - 递归调用 from_dict
│    │
│    └──► 阶段4：按类型递归
│          ├──► dict: 遍历 properties，递归每个值
│          ├──► list: 遍历 items，递归每个元素
│          └──► 其他: 直接 target_tp(dct)
└──────────────────────────────────────────────────────────────┘
```

### 17.2 两阶段 `$ref` 处理对比表

| 维度 | 代码生成阶段 | 运行时阶段 |
|------|-------------|-----------|
| **核心函数** | `tools.schemapi.schemapi._resolve_references` | `altair.utils.schemapi._resolve_references` |
| **触发时机** | `SchemaInfo` 构造时即时解析 | 需要时动态解析 |
| **解析方式** | 完整展开 + `allOf` 合并 | 按需展开顶层 |
| **原始引用保留** | `SchemaInfo.raw_schema` | 类属性 `_schema` |
| **完整定义存储** | `rootschema` 参数 | 类属性 `_rootschema` |
| **主要用途** | 类型推断、类继承、代码生成 | 验证、反序列化、属性访问 |
| **`allOf` 处理** | 预合并到 `properties` | 交给 jsonschema |
| **`anyOf` 处理** | 生成 `Union` 类型和 `@overload` | 运行时尝试验证匹配 |

### 17.3 `_rootschema` 使用场景汇总

| 场景 | 代码位置 | 用途 |
|------|---------|------|
| **`validate()`** | `schemapi.py:1347` | 传递给 `validate_jsonschema` 解析 `$ref` |
| **`resolve_references()`** | `schemapi.py:1357` | 作为根 schema 解析 `$ref` |
| **`validate_property()`** | `schemapi.py:1369` | 传递给 `validate_jsonschema` |
| **`from_dict()`** | `schemapi.py:1583,1590,1599` | 解析 `$ref`、验证匹配 |
| **`with_property_setters`** | `schemapi.py:1677` | 间接通过 `resolve_references()` |

### 17.4 关键设计亮点（第二轮补充）

1. **Schema 哈希映射的高效查找**
   - 预计算所有 `SchemaBase` 子类的 schema 哈希
   - `_hash_exclude_keys` 排除不影响类型匹配的元数据（`definitions`, `title`, `description` 等）
   - 反序列化时 O(1) 哈希查找匹配类

2. **`anyOf` 运行时尝试验证**
   - 代码生成阶段：生成 `Union` 类型和 `@overload`
   - 运行时阶段：无法静态确定具体类型，逐个 `validate_jsonschema` 尝试
   - 找到第一个匹配的 schema 后递归处理

3. **两阶段 `$ref` 处理的关注点分离**
   - **代码生成**：完整展开用于类型分析和代码生成
   - **运行时**：延迟解析，按需展开，节省内存
   - `raw_schema` vs `schema` 的区分让代码生成器既能访问原始引用，又能使用展开后的结构

4. **按 properties 递归的精确控制**
   - 只有在 schema `properties` 中定义的键才递归反序列化
   - 未定义的键（`additionalProperties`）保持原样
   - 避免过度处理，保持与 schema 定义一致

### 17.5 第二轮补充参考文件位置

| 文件 | 说明 |
|------|------|
| `altair/utils/schemapi.py:1285-1307` | `SchemaBase.from_dict()` 入口 |
| `altair/utils/schemapi.py:1469-1623` | `_FromDict` 转换器类完整实现 |
| `altair/utils/schemapi.py:562-581` | `_resolve_references()` 运行时解析 |
| `altair/utils/schemapi.py:1350-1358` | `SchemaBase.resolve_references()` 方法 |
| `altair/vegalite/v6/api.py:4114-4141` | `Chart.from_dict()` 智能类型识别 |
| `tools/schemapi/utils.py:372-375` | `SchemaInfo` 构造时即时解析 `$ref` |
| `tools/schemapi/utils.py:701-706` | `refname`、`ref` 从 `raw_schema` 提取 |

---

## 18. `@use_signature` 装饰器机制

### 18.1 概述

`@use_signature` 和 `@use_signature_func` 是两个运行时装饰器，用于将一个类或函数的参数签名复制到另一个函数/方法上。这是一种"间接签名注入"机制，用于解决以下问题：

1. **代码生成与手动编写的方法需要同步签名**
2. **IDE 自动补全和类型检查支持**
3. **文档自动继承与更新**

**典型使用场景**:
- `mixins.py` 中的 `mark_point()`、`configure_axis()` 等方法
- `api.py` 中的 `binding_checkbox()`、`binding_select()` 等便利函数
- 子类化 `SchemaBase` 派生类时继承签名

### 18.2 为什么需要这种间接机制？

**直接写死签名的问题**：

```python
# 假设 MarkDef 有以下参数
class MarkDef:
    def __init__(
        self,
        color: Optional[str] = Undefined,
        filled: Optional[bool] = Undefined,
        opacity: Optional[float] = Undefined,
        # ... 还有几十个参数
    ): ...

# 问题1：如果直接写死，代码重复且难以维护
class MarkMethodMixin:
    def mark_point(self, color=None, filled=None, opacity=None, ...):  # 重复几十次！
        copy = self.copy(deep=False)
        copy.mark = core.MarkDef(type="point", color=color, filled=filled, ...)
        return copy

# 问题2：MarkDef 签名变化时，mark_point 等方法需要同步更新
# 问题3：类型注解和文档需要手动维护
```

**使用 `@use_signature` 的优势**：

```python
# 代码简洁，签名自动从 MarkDef 继承
class MarkMethodMixin:
    @use_signature(_MarkDef)
    def mark_point(self, **kwds: Any) -> Self:
        """Set the chart's mark to 'point' (see :class:`MarkDef`)."""
        copy = self.copy(deep=False)
        if any(val is not Undefined for val in kwds.values()):
            copy.mark = core.MarkDef(type="point", **kwds)
        else:
            copy.mark = "point"
        return copy

# 优势：
# 1. 代码简洁，使用 **kwds 传递参数
# 2. 签名自动从 MarkDef 继承，MarkDef 变化时自动同步
# 3. 文档自动继承（通过 __wrapped__ 属性）
# 4. IDE 自动补全和类型检查正常工作
```

### 18.3 `@use_signature` 与 `@use_signature_func` 的实现

**文件位置**: `altair/utils/core.py:759-792`

#### 类型定义与协议

```python
# 方法签名复制器协议
class _MethodSignatureCopier(Protocol[P]):
    def __call__(self, cb: WrapsMethod[T, R], /) -> WrappedMethod[T, P, R]: ...

# 函数签名复制器协议
class _FunctionSignatureCopier(Protocol[P]):
    def __call__(self, cb: Callable[..., R], /) -> Callable[P, R]: ...
```

#### `use_signature` 装饰器（用于方法）

```python
def use_signature(tp: Callable[P, Any], /) -> _MethodSignatureCopier[P]:
    """
    Use the signature and doc of ``tp`` for the decorated method ``cb``.
    
    Returns
    -------
    A decorator that copies the doc and static typing signature from ``tp`` to ``cb``.
    """
    
    def decorate(cb: WrapsMethod[T, R], /) -> WrappedMethod[T, P, R]:
        # 核心：调用 _wrap_and_copy_doc 复制文档和设置 __wrapped__
        _wrap_and_copy_doc(tp, cb)
        return cb
    
    return decorate
```

#### `use_signature_func` 装饰器（用于普通函数）

```python
def use_signature_func(tp: Callable[P, Any], /) -> _FunctionSignatureCopier[P]:
    """
    Use the signature and doc of ``tp`` for the decorated function ``cb``.
    
    Returns
    -------
    A decorator that copies the doc and static typing signature from ``tp`` to ``cb``.
    """
    
    def decorate(fn: Callable[..., R], /) -> Callable[P, R]:
        _wrap_and_copy_doc(tp, fn)
        return fn
    
    return decorate
```

#### `_wrap_and_copy_doc` 核心辅助函数

**文件位置**: `altair/utils/core.py:736-752`

```python
def _wrap_and_copy_doc(tp: Callable[..., Any], cb: Callable[..., Any]) -> None:
    """
    复制文档字符串，设置 __wrapped__ 属性。
    
    Notes
    -----
    - Reference to ``tp`` is stored in ``cb.__wrapped__``.
    - The doc for ``cb`` will have a ``.rst`` link added, referring  to ``tp``.
    """
    
    # 步骤1：设置 __wrapped__ 属性
    # 对于类，使用 __init__；对于函数，使用自身
    cb.__wrapped__ = getattr(tp, "__init__", tp)
    
    # 步骤2：复制和处理文档字符串
    if doc_in := tp.__doc__:
        # cb 原有文档的第一行（或默认引用提示）
        line_1 = f"{cb.__doc__ or f'Refer to :class:`{tp.__name__}`'}\n"
        
        # 合并文档：
        # - 第一行使用 cb 原有的（或默认）
        # - 后续行使用 tp.__doc__ 的内容
        cb.__doc__ = "".join((line_1, *doc_in.splitlines(keepends=True)[1:]))
    else:
        msg = f"Found no doc for {tp!r}"
        raise AttributeError(msg)
```

### 18.4 类型系统的魔法：`ParamSpec` 和 `WrapsMethod`

**关键类型定义**（`altair/utils/core.py`）：

```python
# ParamSpec 用于捕获目标类型的签名
P = ParamSpec("P")
T = TypeVar("T")  # self 类型
R = TypeVar("R")  # 返回值类型

# WrapsMethod：装饰前的方法类型（签名是通用的）
WrapsMethod = Callable[Concatenate[T, ...], R]

# WrappedMethod：装饰后的方法类型（签名是 P，即 tp 的签名）
WrappedMethod = Callable[Concatenate[T, P], R]
```

**类型系统如何工作**：

```python
# 假设 MarkDef.__init__ 的签名是：
# def __init__(
#     self,
#     color: Optional[str] = Undefined,
#     filled: Optional[bool] = Undefined,
#     **kwargs: Any
# ) -> None: ...

# @use_signature(MarkDef) 时：
# - P = (color: Optional[str] = Undefined, filled: Optional[bool] = Undefined, **kwargs: Any)
# - T = Self (方法的 self 类型)
# - R = Self (返回值类型)

# 装饰前：
def mark_point(self, **kwds: Any) -> Self: ...
# 类型：Callable[Concatenate[Self, ...], Self]

# 装饰后：
@use_signature(MarkDef)
def mark_point(self, **kwds: Any) -> Self: ...
# 类型：Callable[Concatenate[Self, P], Self]
# 其中 P = MarkDef.__init__ 的参数签名

# 结果：IDE 看到的签名是：
# def mark_point(
#     self,
#     color: Optional[str] = Undefined,
#     filled: Optional[bool] = Undefined,
#     **kwargs: Any
# ) -> Self: ...
```

### 18.5 代码生成阶段的配合：虚拟 `_MarkDef` 类

在 `mixins.py` 中，`@use_signature` 需要一个"签名来源"类。但 `core.MarkDef` 是在 `core.py` 中定义的，如果直接使用它，会导致类型检查器看到两个不同的类。

**解决方案**：在 `mixins.py` 中生成一个虚拟的 `_MarkDef` 类，与 `core.MarkDef` 有相同的签名。

**生成逻辑** (`tools/generate_schema_wrapper.py:2066-2093`)：

```python
def generate_vegalite_mark_mixin(fp: Path, /, markdefs: dict[str, str]) -> str:
    schema = load_schema(fp)
    code: list[str] = []

    # 步骤1：生成虚拟的 _MarkDef 类（用于 @use_signature）
    # 排除 'type' 属性，因为 mark_xxx() 方法会自动设置 type
    it_dummy = (
        SchemaGenerator(
            classname=f"_{mark_def}",  # 如 "_MarkDef"
            schema={"$ref": "#/definitions/" + mark_def},
            rootschema=schema,
            exclude_properties={"type"},  # 关键：排除 type 参数
            annotate_kwds_flag=True,
        ).schema_class()
        for mark_def in markdefs.values()
    )

    # 步骤2：为每个 mark 类型生成方法
    for mark_enum, mark_def in markdefs.items():
        _def = schema["definitions"][mark_enum]
        marks: list[Any] = _def["enum"] if "enum" in _def else [_def["const"]]

        for mark in marks:
            # mark = "point", "bar", "line" 等
            mark_method = MARK_METHOD.format(
                decorator=f"_{mark_def}",    # 如 "_MarkDef"
                mark=mark,
                mark_def=mark_def
            )
            code.append("\n    ".join(mark_method.splitlines()))
```

**生成的虚拟类** (`mixins.py`)：

```python
# 虚拟类，与 core.MarkDef 签名相同（但排除了 'type'）
class _MarkDef:
    """_MarkDef schema wrapper..."""
    _schema = {'$ref': '#/definitions/MarkDef'}
    
    def __init__(
        self,
        aria: Optional[bool] = Undefined,
        color: Optional[str] = Undefined,
        filled: Optional[bool] = Undefined,
        # ... 其他参数，但没有 'type'（因为被 exclude_properties 排除）
        **kwds: Any
    ):
        super(_MarkDef, self).__init__(...)
```

**生成的方法** (`mixins.py`)：

```python
class MarkMethodMixin:
    """A mixin class that defines mark methods"""

    @use_signature(_MarkDef)
    def mark_point(self, **kwds: Any) -> Self:
        """Set the chart's mark to 'point' (see :class:`MarkDef`)."""
        copy = self.copy(deep=False)
        if any(val is not Undefined for val in kwds.values()):
            # 注意：自动设置 type="point"
            copy.mark = core.MarkDef(type="point", **kwds)
        else:
            copy.mark = "point"
        return copy

    @use_signature(_MarkDef)
    def mark_bar(self, **kwds: Any) -> Self:
        """Set the chart's mark to 'bar'..."""
        copy = self.copy(deep=False)
        if any(val is not Undefined for val in kwds.values()):
            copy.mark = core.MarkDef(type="bar", **kwds)
        else:
            copy.mark = "bar"
        return copy
    
    # mark_line, mark_area, etc.
```

### 18.6 `api.py` 中的使用场景

#### 场景1：子类化 `SchemaBase` 派生类

**文件位置**: `altair/vegalite/v6/api.py:312-322`

```python
class LookupData(core.LookupData):
    @utils.use_signature(core.LookupData)
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
    
    # 自定义的 to_dict 方法（处理 data 转换）
    def to_dict(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Convert the chart to a dictionary suitable for JSON export."""
        copy = self.copy(deep=False)
        copy.data = _prepare_data(copy.data, kwargs.get("context"))
        return super(LookupData, copy).to_dict(*args, **kwargs)
```

**设计意图**：
- `LookupData` 需要在 `to_dict()` 中做特殊处理（`_prepare_data`）
- 使用 `@use_signature` 确保 `__init__` 的签名与 `core.LookupData` 完全一致

#### 场景2：便利函数 `binding_*`

**文件位置**: `altair/vegalite/v6/api.py:1907-1933`

```python
@utils.use_signature_func(core.BindCheckbox)
def binding_checkbox(**kwargs: Any) -> BindCheckbox:
    """A checkbox binding."""
    return core.BindCheckbox(input="checkbox", **kwargs)


@utils.use_signature_func(core.BindRadioSelect)
def binding_radio(**kwargs: Any) -> BindRadioSelect:
    """A radio button binding."""
    return core.BindRadioSelect(input="radio", **kwargs)


@utils.use_signature_func(core.BindRadioSelect)
def binding_select(**kwargs: Any) -> BindRadioSelect:
    """A select binding."""
    return core.BindRadioSelect(input="select", **kwargs)


@utils.use_signature_func(core.BindRange)
def binding_range(**kwargs: Any) -> BindRange:
    """A range binding."""
    return core.BindRange(input="range", **kwargs)
```

**设计意图**：
- `binding_checkbox()` 是 `core.BindCheckbox(input="checkbox", ...)` 的简写
- 使用 `@use_signature_func` 复制 `BindCheckbox.__init__` 的签名
- 用户不需要记住 `input="checkbox"` 这个固定参数

### 18.7 `configure_*` 方法的设计

**文件位置**: `mixins.py` 中的 `ConfigMethodMixin`

```python
class ConfigMethodMixin:
    """A mixin class that defines config methods"""

    @use_signature(core.Config)
    def configure(self, *args, **kwargs) -> Self:
        copy = self.copy(deep=False)
        copy.config = core.Config(*args, **kwargs)
        return copy

    @use_signature(core.AxisConfig)
    def configure_axis(self, *args, **kwargs) -> Self:
        copy = self.copy(deep=['config'])
        if copy.config is Undefined:
            copy.config = core.Config()
        # 关键：直接设置 config["axis"]
        copy.config["axis"] = core.AxisConfig(*args, **kwargs)
        return copy

    @use_signature(core.MarkConfig)
    def configure_mark(self, *args, **kwargs) -> Self:
        copy = self.copy(deep=['config'])
        if copy.config is Undefined:
            copy.config = core.Config()
        copy.config["mark"] = core.MarkConfig(*args, **kwargs)
        return copy

    # configure_legend, configure_title, configure_view, etc.
```

**设计要点**：
- `configure()` 直接设置整个 `config` 对象
- `configure_axis()`、`configure_mark()` 等方法设置 `config` 的特定属性
- 使用 `copy(deep=['config'])` 确保 `config` 对象被深拷贝
- 使用 `config["axis"]` 而非 `config.axis` 避免触发 Undefined 检查

### 18.8 为什么不直接生成带签名的方法？

**可能的疑问**：既然代码生成器可以生成 `core.py`、`channels.py`，为什么不直接在 `mixins.py` 中生成带完整签名的方法？

**回答**：这是一个**关注点分离**的设计决策：

| 方案 | 优点 | 缺点 |
|------|------|------|
| **直接生成签名** | 运行时无需装饰器 | 代码生成器更复杂，需要处理模板变量、方法体等 |
| **`@use_signature` + 运行时装饰** | 代码生成器只需处理模板字符串，方法体手写 | 运行时需要装饰器，依赖 `ParamSpec` 类型系统 |

**Altair 选择后者的原因**：

1. **方法体逻辑复杂**：`mark_point()`、`configure_axis()` 等方法的逻辑相对复杂，包含条件判断、`copy(deep=...)` 等操作。用模板字符串生成这些逻辑会非常繁琐且难以维护。

2. **签名来自现有类**：`MarkDef`、`AxisConfig` 等类已经在 `core.py` 中生成好了，签名信息已经存在。通过 `@use_signature` 可以**复用**这些信息，避免重复。

3. **类型检查器支持**：现代 Python 类型检查器（mypy、pyright）都支持 `ParamSpec` 和 `@use_signature` 这种模式。IDE 自动补全和类型检查可以正常工作。

4. **文档自动同步**：通过 `__wrapped__` 属性，文档字符串可以自动继承和更新，避免手动维护的不一致问题。

### 18.9 `@use_signature` 完整流程图

```
┌─────────────────────────────────────────────────────────────────────┐
│ 阶段1：代码生成（生成 mixins.py）                                      │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 1. 生成虚拟类 _MarkDef                                                │
│    - 基于 core.MarkDef 的 schema                                      │
│    - 使用 exclude_properties={"type"} 排除 type 参数                   │
│    - 生成与 core.MarkDef 相同的签名（但无 type）                       │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 2. 生成 mark_* 方法                                                   │
│    - 使用模板 MARK_METHOD                                              │
│    - @use_signature(_MarkDef)                                          │
│    - 方法体：**kwds 传递 + type="point" 自动设置                       │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ 运行时（导入 mixins.py）
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 阶段2：运行时装饰（@use_signature 执行）                              │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 3. use_signature(_MarkDef) 返回 decorate 函数                         │
│    - P = ParamSpec 捕获 _MarkDef.__init__ 的签名                       │
│    - 返回 Callable[Concatenate[T, P], R] 类型                         │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 4. decorate(mark_point) 调用                                          │
│    - 设置 mark_point.__wrapped__ = _MarkDef.__init__                  │
│    - 复制和处理文档字符串                                               │
│    - 返回 mark_point（签名现在是 P）                                   │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ 用户调用
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 阶段3：用户使用                                                        │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 5. 用户调用 chart.mark_point(color="red", filled=True)                │
│    - IDE 看到完整签名：color, filled, aria, stroke, 等                │
│    - 自动补全、类型检查正常工作                                         │
│    - 方法体执行：**kwds = {"color": "red", "filled": True}            │
│    - core.MarkDef(type="point", **kwds)                               │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 19. `CORE_OVERRIDES` 覆盖体系

### 19.1 概述

`CORE_OVERRIDES` 是一个覆盖机制，允许对特定的 schema definition 使用不同的代码生成器。默认情况下，所有 definition 都使用 `SchemaGenerator` 生成代码，但某些特殊的 schema 模式需要自定义生成器。

**核心定义** (`tools/generate_schema_wrapper.py:482-491`)：

```python
SchGen = TypeVar("SchGen", bound=SchemaGenerator)

class OverridesItem(TypedDict, Generic[SchGen]):
    tp: type[SchGen]      # 要使用的生成器类
    kwds: dict[str, Any]   # 传递给生成器的额外参数


CORE_OVERRIDES: dict[str, OverridesItem[SchemaGenerator]] = {
    "PredicateComposition": OverridesItem(
        tp=MethodSchemaGenerator, 
        kwds={"method_code": DUNDER_PREDICATE_COMPOSITION}
    )
}
```

### 19.2 默认生成器：`SchemaGenerator`

**文件位置**: `tools/generate_schema_wrapper.py:451-460`

```python
class SchemaGenerator(codegen.SchemaGenerator):
    schema_class_template = textwrap.dedent(
        '''
    class {classname}({basename}):
        """{docstring}"""
        _schema = {schema!r}

        {init_code}
    '''
    )
```

**生成的代码结构**：

```python
class FieldDef(VegaLiteSchema):
    """FieldDef schema wrapper..."""
    _schema = {'$ref': '#/definitions/FieldDef'}
    
    def __init__(self, field: Optional[str] = Undefined, type=...):
        super(FieldDef, self).__init__(field=field, type=type, ...)
```

**包含内容**：
- 类定义
- docstring
- `_schema` 类属性
- `__init__` 方法

### 19.3 扩展生成器：`MethodSchemaGenerator`

**文件位置**: `tools/generate_schema_wrapper.py:463-476`

```python
class MethodSchemaGenerator(SchemaGenerator):
    """Base template w/ an extra slot `{method_code}` after `{init_code}`."""

    schema_class_template = textwrap.dedent(
        '''
    class {classname}({basename}):
        """{docstring}"""
        _schema = {schema!r}

        {init_code}

        {method_code}    # 额外的方法代码插槽
    '''
    )
```

**与默认生成器的区别**：

| 特性 | `SchemaGenerator` | `MethodSchemaGenerator` |
|------|------------------|------------------------|
| 模板插槽 | `{init_code}` 后直接结束 | 额外的 `{method_code}` 插槽 |
| 生成代码 | 只有 `__init__` | `__init__` + 自定义方法 |
| 适用场景 | 大多数 definition | 需要额外方法的特殊类 |

