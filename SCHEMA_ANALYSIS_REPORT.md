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

