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

