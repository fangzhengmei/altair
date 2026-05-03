# Altair 数据转换器分析报告

## 1. 概述

本报告深入分析 Altair 中数据转换器（Data Transformer）的注册机制、调用链路，以及面对超出大小限制的数据集时，各种下发策略（内嵌/文件/URL/服务端）如何在多个模块中选择和协作。

## 2. 数据转换器注册机制

### 2.1 核心架构

数据转换器的注册系统基于插件注册表模式，主要由以下类构成：

#### 2.1.1 PluginRegistry 基类

**位置**: `altair/utils/plugin_registry.py:83-280`

`PluginRegistry` 是一个通用的插件注册表基类，提供以下核心功能：

- **双重注册机制**:
  1. 显式注册：通过 `.register(name, value)` 方法
  2. Entry Point 自动加载：通过 Python 的 `importlib.metadata.entry_points` 机制

- **状态管理**:
  - `_active`: 当前激活的插件
  - `_active_name`: 当前激活的插件名称
  - `_plugins`: 已注册的插件字典
  - `_options`: 激活时传递的选项
  - `_global_settings`: 全局设置

- **关键方法**:

| 方法 | 位置 | 功能 |
|------|------|------|
| `register(name, value)` | L147-173 | 显式注册插件 |
| `enable(name, **options)` | L226-250 | 激活指定插件，支持上下文管理器 |
| `get()` | L262-276 | 获取当前激活的插件（已绑定 options） |
| `names()` | L175-181 | 列出所有已注册的插件名称 |

#### 2.1.2 DataTransformerRegistry 子类

**位置**: `altair/utils/data.py:94-103`

```python
class DataTransformerRegistry(PluginRegistry[DataTransformerType, R]):
    _global_settings = {"consolidate_datasets": True}

    @property
    def consolidate_datasets(self) -> bool:
        return self._global_settings["consolidate_datasets"]

    @consolidate_datasets.setter
    def consolidate_datasets(self, value: bool) -> None:
        self._global_settings["consolidate_datasets"] = value
```

扩展了 `PluginRegistry`，增加了：
- `consolidate_datasets` 属性：控制是否启用数据集合并（默认 `True`）
- 类型参数化：`DataTransformerType` 定义了转换器的函数签名

### 2.2 注册的转换器

**位置**: `altair/vegalite/v6/data.py:20-29`

```python
ENTRY_POINT_GROUP: Final = "altair.vegalite.v6.data_transformer"

data_transformers = DataTransformerRegistry(entry_point_group=ENTRY_POINT_GROUP)
data_transformers.register("default", default_data_transformer)
data_transformers.register("json", to_json)
data_transformers.register("csv", to_csv)
data_transformers.register("vegafusion", vegafusion_data_transformer)
data_transformers.enable("default")
```

| 转换器名称 | 实现函数 | 功能描述 |
|-----------|---------|---------|
| `default` | `default_data_transformer` | 内嵌数据，带行数限制检查 |
| `json` | `to_json` | 导出为 JSON 文件，返回 URL 引用 |
| `csv` | `to_csv` | 导出为 CSV 文件，返回 URL 引用 |
| `vegafusion` | `vegafusion_data_transformer` | 服务端预计算，返回特殊 URL 引用 |

### 2.3 转换器类型签名

**位置**: `altair/utils/data.py:88-91`

```python
P = ParamSpec("P")
R = TypeVar("R", VegaLiteDataDict, Any)
DataTransformerType = Callable[Concatenate[DataType, P], R]
```

转换器必须是一个可调用对象，接受：
- 第一个参数：`DataType`（支持 `dict`、`DataFrame`、`__geo_interface__` 对象等）
- 后续参数：任意可选参数
- 返回值：`VegaLiteDataDict` 或兼容格式

## 3. 数据转换器调用链路

### 3.1 完整调用流程图

```
用户代码
    │
    ▼
Chart(df).encode(...).mark_point()
    │
    ▼
显示/保存/导出时触发
    │
    ├─► chart.to_dict()          ──────────────────────────┐
    ├─► chart.to_json()         ──────────────────────────┤
    ├─► chart.to_html()         ──────────────────────────┤
    ├─► chart.save()            ──────────────────────────┤
    └─► Jupyter 显示 _repr_mimebundle_()  ───────────────┘
                                      │
                                      ▼
                        TopLevelMixin.to_dict()
                        (altair/vegalite/v6/api.py:2044)
                                      │
                                      ▼
                        ┌─────────────────────────────────┐
                        │ 1. 准备 context 字典            │
                        │    - datasets: {}               │
                        │    - top_level: True            │
                        │    - pre_transform: True        │
                        └─────────────────────────────────┘
                                      │
                                      ▼
                        获取 chart.data 属性
                                      │
                                      ▼
                        _prepare_data(data, context)
                        (altair/vegalite/v6/api.py:266)
                                      │
                                      ▼
                        ┌─────────────────────────────────┐
                        │ 2. 数据类型检查                  │
                        │    if not dict and _is_data_type │
                        └─────────────────────────────────┘
                                      │
                                      ▼
                        data_transformers.get()
                        (获取当前激活的转换器)
                                      │
                                      ▼
                        func(nw.to_native(data, pass_through=True))
                                      │
                                      ▼
                        ┌─────────────────────────────────┐
                        │ 3. 执行具体转换策略              │
                        │    - default: limit_rows + to_values │
                        │    - json: 写入文件 + 返回 URL       │
                        │    - vegafusion: 存储 + 特殊 URL     │
                        └─────────────────────────────────┘
                                      │
                                      ▼
                        ┌─────────────────────────────────┐
                        │ 4. 数据集合并（可选）            │
                        │    if consolidate_datasets:     │
                        │        _consolidate_data()      │
                        └─────────────────────────────────┘
                                      │
                                      ▼
                        ┌─────────────────────────────────┐
                        │ 5. VegaFusion 预转换（可选）    │
                        │    if pre_transform and using_vegafusion: │
                        │        _compile_with_vegafusion() │
                        └─────────────────────────────────┘
                                      │
                                      ▼
                        返回最终的 Vega-Lite/Vega spec
```

### 3.2 关键调用点详解

#### 3.2.1 _prepare_data 函数

**位置**: `altair/vegalite/v6/api.py:266-302`

```python
def _prepare_data(
    data: ChartDataType, context: dict[str, Any] | None = None
) -> ChartDataType | NamedData | InlineData | UrlData | Any:
    if data is Undefined:
        return data

    # 关键点1: 对非字典类型的数据调用转换器
    elif not isinstance(data, dict) and _is_data_type(data):
        if func := data_transformers.get():
            data = func(nw.to_native(data, pass_through=True))

    # 关键点2: 字符串转换为 UrlData
    elif isinstance(data, str):
        data = core.UrlData(data)

    # 关键点3: 数据集合并
    if context is not None and data_transformers.consolidate_datasets:
        data = _consolidate_data(data, context)

    return data
```

**核心逻辑**:
1. 只对非字典类型的数据应用数据转换器（字典可能已经是转换后的格式）
2. 使用 `narwhals` 库统一处理各种 DataFrame 类型
3. 字符串类型直接转换为 `UrlData`（用户手动指定的 URL）

#### 3.2.2 TopLevelMixin.to_dict 方法

**位置**: `altair/vegalite/v6/api.py:2044-2157`

这是整个调用链的核心方法，控制了从 Chart 对象到 Vega 规范字典的完整转换过程。

**关键代码片段**:

```python
def to_dict(
    self,
    validate: bool = True,
    *,
    format: Literal["vega-lite", "vega"] = "vega-lite",
    ignore: list[str] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Context 包含以下关键标志：
    # - 'data': 用于列类型推断的数据引用
    # - 'top_level': 是否为顶层调用（影响 $schema 键）
    # - 'datasets': 命名数据集字典
    # - 'pre_transform': 是否执行 VegaFusion 预转换
    
    context = context.copy() if context else {}
    context.setdefault("datasets", {})
    
    # 准备数据
    copy = _top_schema_base(self).copy(deep=False)
    original_data = getattr(copy, "data", Undefined)
    if not utils.is_undefined(original_data):
        try:
            data = nw.from_native(original_data, eager_or_interchange_only=True)
        except TypeError:
            data = original_data
        copy.data = _prepare_data(data, context)  # <-- 调用数据转换器
        context["data"] = data

    # 执行基础转换
    vegalite_spec = _top_schema_base(super(TopLevelMixin, copy)).to_dict(
        validate=validate, ignore=ignore, 
        context=dict(context, pre_transform=False)
    )

    # 处理数据集合并结果
    if is_top_level and context["datasets"]:
        vegalite_spec.setdefault("datasets", {}).update(context["datasets"])

    # VegaFusion 预转换
    if context.get("pre_transform", True) and _using_vegafusion():
        if format == "vega-lite":
            raise ValueError(
                'When "vegafusion" is enabled, must use format="vega"'
            )
        else:
            return _compile_with_vegafusion(vegalite_spec)
    elif format == "vega":
        # 使用 vl-convert 编译
        plugin = vegalite_compilers.get()
        return plugin(vegalite_spec)
    else:
        return vegalite_spec
```

### 3.3 数据合并机制

**位置**: `altair/vegalite/v6/api.py:235-263`

```python
def _consolidate_data(
    data: ChartDataType | UrlData, context: dict[str, Any]
) -> ChartDataType | NamedData | InlineData | UrlData:
    values: Any = Undefined
    kwds: dict = {}

    # 识别内联数据
    if isinstance(data, core.InlineData):
        if utils.is_undefined(data.name) and not utils.is_undefined(data.values):
            values = data.values  # 或 data.to_dict()["values"]
            kwds = {"format": data.format}

    elif isinstance(data, dict) and ("name" not in data) and ("values" in data):
        values = data["values"]
        kwds = {k: v for k, v in data.items() if k != "values"}

    # 移动到顶层 datasets
    if not utils.is_undefined(values):
        name = _dataset_name(values)  # 基于内容的哈希命名
        data = core.NamedData(name=name, **kwds)
        context.setdefault("datasets", {})[name] = values

    return data
```

**设计意图**:
- 避免同一数据在多层图表（如 layer, hconcat）中重复出现
- 使用 SHA256 哈希值作为数据集名称，确保相同内容共享同一份数据
- 减少最终 spec 的体积

## 4. 下发策略详解

### 4.1 策略概览

| 策略 | 转换器名称 | 触发方式 | 数据位置 | 行数限制 | 适用场景 |
|------|-----------|---------|---------|---------|---------|
| 内嵌 | `default` | 默认启用 | 直接嵌入 spec | 5000 行 | 小数据集 |
| JSON 文件 | `json` | `enable('json')` | 本地文件系统 | 无硬限制 | 中等数据集 |
| CSV 文件 | `csv` | `enable('csv')` | 本地文件系统 | 无硬限制 | 中等数据集 |
| 服务端 | `vegafusion` | `enable('vegafusion')` | Python 内存中 | 100,000 行 | 大数据集 |
| 数据服务器 | `data_server` | 外部插件 | 本地 HTTP 服务器 | 无硬限制 | 云环境 |

### 4.2 内嵌策略（Default）

**实现位置**: `altair/vegalite/data.py:24-44`

```python
def default_data_transformer(
    data: DataType | None = None, max_rows: int = 5000
) -> Callable[[DataType], ToValuesReturnType] | ToValuesReturnType:
    if data is None:
        def pipe(data: DataType, /) -> ToValuesReturnType:
            data = limit_rows(data, max_rows=max_rows)
            return to_values(data)
        return pipe
    else:
        return to_values(limit_rows(data, max_rows=max_rows))
```

**工作流程**:

```
输入: DataFrame/dict
    │
    ▼
limit_rows(data, max_rows=5000)
    │
    ├── 检查数据类型
    ├── 计算行数 n = len(values)
    └── if n > max_rows: raise MaxRowsError
    │
    ▼
to_values(data)
    │
    ├── GeoInterface: sanitize_geo_interface()
    ├── Pandas DataFrame: sanitize_pandas_dataframe() -> to_dict(orient='records')
    ├── Dict with 'values': 直接返回
    └── Narwhals DataFrame: sanitize_narwhals_dataframe() -> rows(named=True)
    │
    ▼
输出: {"values": [...]}
```

**核心特点**:
1. **柯里化设计**: 支持无参调用返回管道函数，或直接传入数据
2. **行数限制**: 默认 5000 行，通过 `MaxRowsError` 强制检查
3. **多类型支持**: 统一处理 Pandas、GeoPandas、普通字典等

**limit_rows 实现** (`altair/utils/data.py:135-165`):

```python
def limit_rows(
    data: DataType | None = None, max_rows: int | None = 5000
) -> partial | DataType:
    if data is None:
        return partial(limit_rows, max_rows=max_rows)
    
    # 根据数据类型提取 values
    if isinstance(data, SupportsGeoInterface):
        # GeoJSON: features 或整个对象
        if data.__geo_interface__["type"] == "FeatureCollection":
            values = data.__geo_interface__["features"]
        else:
            values = data.__geo_interface__
    elif isinstance(data, dict):
        values = data.get("values", data)  # 无 values 时返回原数据（跳过检查）
    else:
        data = to_eager_narwhals_dataframe(data)
        values = data
    
    n = len(values)
    if max_rows is not None and n > max_rows:
        raise MaxRowsError.from_limit_rows(n, max_rows)
    
    return data
```

### 4.3 JSON 文件策略

**实现位置**: `altair/utils/data.py:226-259`

```python
def to_json(
    data: DataType | None = None,
    prefix: str = "altair-data",
    extension: str = "json",
    filename: str = "{prefix}-{hash}.{extension}",
    urlpath: str = "",
) -> partial | _ToFormatReturnUrlDict:
    kwds = _to_text_kwds(prefix, extension, filename, urlpath)
    if data is None:
        return partial(to_json, **kwds)
    else:
        data_str = _data_to_json_string(data)
        return _to_text(data_str, **kwds, format=_FormatDict(type="json"))
```

**核心函数 _to_text** (`altair/utils/data.py:298-310`):

```python
def _to_text(
    data: str,
    prefix: str,
    extension: str,
    filename: str,
    urlpath: str,
    format: _FormatDict,
) -> _ToFormatReturnUrlDict:
    data_hash = _compute_data_hash(data)  # SHA256 前 32 位
    filename = filename.format(prefix=prefix, hash=data_hash, extension=extension)
    Path(filename).write_text(data, encoding="utf-8")
    url = str(Path(urlpath, filename))
    return _ToFormatReturnUrlDict({"url": url, "format": format})
```

**工作流程**:

```
输入: DataFrame/dict
    │
    ▼
_data_to_json_string(data)
    │
    ├── GeoInterface: json.dumps(sanitize_geo_interface(...))
    ├── Pandas: df.to_json(orient='records', double_precision=15)
    ├── Dict with 'values': json.dumps(data['values'], sort_keys=True)
    └── Narwhals: json.dumps(df.rows(named=True))
    │
    ▼
_compute_data_hash(json_str)
    │
    └── hashlib.sha256(data.encode()).hexdigest()[:32]
    │
    ▼
写入文件: {prefix}-{hash}.json
    │
    ▼
输出: {"url": "altair-data-xxx.json", "format": {"type": "json"}}
```

**设计特点**:
1. **内容寻址**: 相同内容生成相同文件名，避免重复写入
2. **高精度**: Pandas 数据使用 `double_precision=15` 确保精度
3. **可定制**: 支持自定义 `prefix`、`filename` 模板、`urlpath`

**配置示例**:
```python
# 基础使用
alt.data_transformers.enable('json')

# 自定义存储目录
def json_dir(data, data_dir='altairdata'):
    os.makedirs(data_dir, exist_ok=True)
    return pipe(data, alt.to_json(filename=f'{data_dir}/{{prefix}}-{{hash}}.{{extension}}'))

alt.data_transformers.register('json_dir', json_dir)
alt.data_transformers.enable('json_dir', data_dir='mydata')
```

### 4.4 CSV 文件策略

**实现位置**: `altair/utils/data.py:262-295`

```python
def to_csv(
    data: dict | pd.DataFrame | DataFrameLike | None = None,
    prefix: str = "altair-data",
    extension: str = "csv",
    filename: str = "{prefix}-{hash}.{extension}",
    urlpath: str = "",
) -> partial | _ToFormatReturnUrlDict:
    kwds = _to_text_kwds(prefix, extension, filename, urlpath)
    if data is None:
        return partial(to_csv, **kwds)
    else:
        data_str = _data_to_csv_string(data)
        return _to_text(data_str, **kwds, format=_FormatDict(type="csv"))
```

**与 JSON 的主要差异**:

1. **不支持 GeoInterface**:
   ```python
   if isinstance(data, SupportsGeoInterface):
       msg = (
           f"to_csv does not yet work with data that "
           f"is of type {type(SupportsGeoInterface).__name__!r}.\n"
           f"See https://github.com/vega/altair/issues/3441"
       )
       raise NotImplementedError(msg)
   ```

2. **Dict 转换依赖 Pandas**:
   ```python
   elif isinstance(data, dict):
       if "values" not in data:
           raise KeyError("values expected in data dict, but not present")
       # 需要 Pandas 来转换 dict 到 CSV
       try:
           import pandas as pd
       except ImportError as exc:
           raise ImportError("pandas is required to convert a dict to a CSV string") from exc
       return pd.DataFrame.from_dict(data["values"]).to_csv(index=False)
   ```

**数据类型注意事项**:
- CSV 格式不保留数据类型信息（所有值都是字符串）
- Vega-Lite 在解析时会进行类型推断，但可能不准确
- 建议仅用于纯数值/字符串数据，避免复杂类型

### 4.5 VegaFusion 服务端策略

**实现位置**: `altair/utils/_vegafusion_data.py:72-105`

这是最复杂也最强大的策略，专门用于处理大数据集。

#### 4.5.1 核心转换器

```python
def vegafusion_data_transformer(
    data: DataType | None = None, max_rows: int = 100000
) -> Callable[..., Any] | _VegaFusionReturnType:
    if data is None:
        return vegafusion_data_transformer
    
    # 检查是否支持此数据类型
    if is_supported_by_vf(data) and not isinstance(data, SupportsGeoInterface):
        table_name = f"table_{uuid.uuid4()}".replace("-", "_")
        extracted_inline_tables[table_name] = data  # 存储到弱引用字典
        return {"url": VEGAFUSION_PREFIX + table_name}  # 特殊 URL 格式
    else:
        # GeoInterface 或不支持的类型，回退到 default
        return default_data_transformer(data)
```

#### 4.5.2 关键数据结构

**特殊 URL 前缀**:
```python
VEGAFUSION_PREFIX: Final = "vegafusion+dataset://"
```

**临时存储**:
```python
extracted_inline_tables: MutableMapping[str, DataFrameLike] = WeakValueDictionary()
```

- 使用 `WeakValueDictionary` 而非普通 `dict`
- 允许 Python 垃圾回收器在没有其他引用时释放 DataFrame
- 避免内存泄漏

**版本兼容的类型检查**:
```python
if VEGAFUSION_VERSION and Version("2.0.0a0") <= VEGAFUSION_VERSION:
    def is_supported_by_vf(data: Any) -> TypeIs[DataFrameLike]:
        # VegaFusion v2+ 支持 narwhals 兼容的 DataFrame
        return isinstance(data, DataFrameLike) or is_into_dataframe(data)
else:
    def is_supported_by_vf(data: Any) -> TypeIs[DataFrameLike]:
        # 旧版本只支持 DataFrameLike
        return isinstance(data, DataFrameLike)
```

#### 4.5.3 预编译流程

当 `pre_transform=True` 且使用 VegaFusion 时，会触发完整的服务端预计算：

**位置**: `altair/utils/_vegafusion_data.py:235-283`

```python
def compile_with_vegafusion(vegalite_spec: dict[str, Any]) -> dict[str, Any]:
    vf = import_vegafusion()
    
    # 步骤1: 编译 Vega-Lite 到 Vega
    compiler = vegalite_compilers.get()
    vega_spec = compiler(vegalite_spec)
    
    # 步骤2: 提取内联表（从 WeakValueDictionary 中取出）
    inline_tables = get_inline_tables(vega_spec)
    # 其中会调用: extracted_inline_tables.pop(k) 取出并删除
    
    # 步骤3: 获取行限制配置
    row_limit = data_transformers.options.get("max_rows", None)
    
    # 步骤4: 执行预转换（核心！）
    transformed_vega_spec, warnings = vf.runtime.pre_transform_spec(
        vega_spec,
        vf.get_local_tz(),
        inline_datasets=inline_tables,  # 传入原始 DataFrame
        row_limit=row_limit,
    )
    
    # 步骤5: 检查行限制警告
    handle_row_limit_exceeded(row_limit, warnings)
    
    return transformed_vega_spec
```

#### 4.5.4 完整工作流程图

```
┌─────────────────────────────────────────────────────────────────┐
│                    VegaFusion 数据转换器流程                      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Phase 1: to_dict() 中的数据准备                                   │
│                                                                  │
│ 1. 用户调用 chart.to_dict(format="vega")                        │
│ 2. _prepare_data() 调用 vegafusion_data_transformer(data)       │
│ 3. 生成 UUID 表名: table_123e4567_89ab...                      │
│ 4. 存储到 WeakValueDictionary:                                    │
│    extracted_inline_tables["table_xxx"] = df                    │
│ 5. 返回特殊 URL:                                                  │
│    {"url": "vegafusion+dataset://table_xxx"}                    │
│ 6. 此 URL 被嵌入到 Vega-Lite spec 中                             │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Phase 2: 检测到 VegaFusion 激活，触发预转换                        │
│                                                                  │
│ 在 TopLevelMixin.to_dict() 中:                                   │
│                                                                  │
│ if context.get("pre_transform", True) and _using_vegafusion(): │
│     if format == "vega-lite":                                   │
│         raise ValueError(...)  # 必须使用 format="vega"         │
│     else:                                                        │
│         return _compile_with_vegafusion(vegalite_spec)          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Phase 3: compile_with_vegafusion() 核心流程                       │
│                                                                  │
│ 3.1 编译 Vega-Lite → Vega                                        │
│     ├── 使用 vegalite_compilers.get()                           │
│     └── 调用 vl-convert-python 库                                │
│                                                                  │
│ 3.2 提取内联表                                                    │
│     ├── 遍历 vega_spec 中的所有 data 节点                        │
│     ├── 查找 URL 以 "vegafusion+dataset://" 开头的节点           │
│     ├── 从 extracted_inline_tables 中取出 DataFrame              │
│     └── 注意: 使用 pop() 取出后即从字典中删除                      │
│                                                                  │
│ 3.3 执行 vf.runtime.pre_transform_spec()                         │
│     ├── 输入: vega_spec + inline_datasets (原始 DataFrame)       │
│     ├── 处理:                                                     │
│     │   ├── 用 Rust 实现的 Vega 表达式引擎                        │
│     │   ├── 执行所有数据转换 (aggregate, filter, bin, 等)        │
│     │   ├── 优化: 列剪枝、下推计算                                │
│     └── 输出: transformed_vega_spec                              │
│                                                                  │
│ 3.4 关键差异点:                                                   │
│     - 输入 spec 中的 data: {"url": "vegafusion+dataset://..."}  │
│     - 输出 spec 中的 data: {"values": [...]} (已聚合的数据)      │
│     - 数据量大幅减少！                                             │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Phase 4: 返回结果                                                  │
│                                                                  │
│ 返回的 Vega spec 特点:                                            │
│ - 所有数据转换已在 Python/Rust 中完成                             │
│ - data.values 包含聚合后的最终结果                                 │
│ - 无需前端再执行计算                                               │
│ - 体积通常比原始数据小几个数量级                                    │
└─────────────────────────────────────────────────────────────────┘
```

#### 4.5.5 行限制处理

**位置**: `altair/utils/_vegafusion_data.py:286-296`

```python
def handle_row_limit_exceeded(row_limit: int | None, warnings: list):
    for warning in warnings:
        if warning.get("type") == "RowLimitExceeded":
            msg = (
                "The number of dataset rows after filtering and aggregation exceeds\n"
                f"the current limit of {row_limit}. Try adding an aggregation to reduce\n"
                "the size of the dataset that must be loaded into the browser. Or, disable\n"
                "the limit by calling alt.data_transformers.disable_max_rows(). Note that\n"
                "disabling this limit may cause the browser to freeze or crash."
            )
            raise MaxRowsError(msg)
```

**与 default 策略的区别**:
- `default`: 限制**原始数据**行数（默认 5000）
- `vegafusion`: 限制**转换后**的数据行数（默认 100,000）
- VegaFusion 的限制应用在聚合、过滤等操作之后，更有实际意义

#### 4.5.6 交互式场景支持

**位置**: `altair/utils/_transformed_data.py:78-148`

`transformed_data()` 函数提供了动态获取转换后数据的能力：

```python
def transformed_data(chart, row_limit=None, exclude=None):
    vf = import_vegafusion()
    
    # 确保有 mark
    if isinstance(chart, Chart) and chart.mark == Undefined:
        chart = chart.mark_point()
    
    # 命名所有视图
    chart_names = name_views(chart, 0, exclude=exclude)
    
    # 使用 vegafusion 转换器编译
    with data_transformers.enable("vegafusion"):
        vega_spec = chart.to_dict(format="vega", context={"pre_transform": False})
        inline_datasets = get_inline_tables(vega_spec)
    
    # 构建映射关系
    facet_mapping = get_facet_mapping(vega_spec)
    dataset_mapping = get_datasets_for_view_names(vega_spec, chart_names, facet_mapping)
    
    # 提取转换后的数据
    datasets, _ = vf.runtime.pre_transform_datasets(
        vega_spec,
        dataset_names,
        row_limit=row_limit,
        inline_datasets=inline_datasets,
    )
    
    return datasets[0] if isinstance(chart, (Chart, FacetChart)) else datasets
```

这支持了 `JupyterChart` 和 `"jupyter"` renderer 的交互式场景，数据转换可以在 Python 中动态执行以响应选择事件。

## 5. 模块协作关系

### 5.1 模块架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                         用户接口层 (User API)                         │
│                                                                       │
│  altair/__init__.py                                                   │
│  ├── 导出 data_transformers 全局实例                                  │
│  ├── 导出 Chart, LayerChart, HConcatChart 等                         │
│  └── 导出 limit_rows, sample, to_json, to_csv 等工具函数              │
└─────────────────────────────────────────────────────────────────────┘
                                    │
            ┌───────────────────────┼───────────────────────┐
            ▼                       ▼                       ▼
┌───────────────────┐   ┌───────────────────┐   ┌───────────────────┐
│  API 层           │   │  数据处理层       │   │  VegaFusion 扩展   │
│                   │   │                   │   │                   │
│ altair/vegalite/  │   │ altair/utils/    │   │ altair/utils/     │
│ v6/api.py         │   │ data.py           │   │ _vegafusion_data.py│
│                   │   │                   │   │                   │
│ - Chart 类        │   │ - 核心转换函数    │   │ - 服务端转换器      │
│ - to_dict()       │◄──┤ - to_values()     │◄──┤ - 预编译逻辑        │
│ - _prepare_data() │   │ - to_json()       │   │ - 内联表管理        │
│ - _consolidate_   │   │ - to_csv()        │   │ - WeakValueDict    │
│   data()          │   │ - limit_rows()    │   │                   │
└───────────────────┘   │ - sample()        │   └───────────────────┘
            │           │                   │
            │           │ - DataTransformer │
            │           │   Registry        │
            │           └───────────────────┘
            │                       │
            ▼                       ▼
┌───────────────────┐   ┌───────────────────┐
│  Vega-Lite 数据层  │   │  插件注册基类     │
│                   │   │                   │
│ altair/vegalite/  │   │ altair/utils/    │
│ data.py           │   │ plugin_registry.py│
│                   │   │                   │
│ - default_data_   │   │ - PluginRegistry  │
│   transformer     │   │   (通用基类)      │
│ - DataTransformer │   │ - PluginEnabler   │
│   Registry (子类) │   │   (上下文管理器)   │
└───────────────────┘   └───────────────────┘
```

### 5.2 关键模块职责

| 模块 | 主要职责 | 关键类/函数 |
|------|---------|------------|
| `altair/utils/plugin_registry.py` | 通用插件注册机制 | `PluginRegistry`, `PluginEnabler` |
| `altair/utils/data.py` | 核心数据转换函数 + 注册表基类 | `DataTransformerRegistry`, `to_values`, `to_json`, `to_csv`, `limit_rows` |
| `altair/vegalite/data.py` | Vega-Lite 特定的数据转换器 | `default_data_transformer`, `DataTransformerRegistry` 子类 |
| `altair/vegalite/v6/data.py` | v6 版本转换器注册 | `data_transformers` 实例注册与激活 |
| `altair/vegalite/v6/api.py` | Chart API 与调用链路 | `TopLevelMixin.to_dict()`, `_prepare_data()`, `_consolidate_data()` |
| `altair/utils/_vegafusion_data.py` | VegaFusion 集成 | `vegafusion_data_transformer`, `compile_with_vegafusion()` |
| `altair/utils/save.py` | 保存时的特殊处理 | `save()` 函数中的转换器管理 |

### 5.3 保存操作中的模块协作

**位置**: `altair/utils/save.py:271-284`

保存操作对数据转换器有特殊处理逻辑：

```python
def save(chart, fp, ...):
    def perform_save() -> None:
        spec = chart.to_dict(context={"pre_transform": False})
        # ... 保存逻辑
    
    if using_vegafusion():
        # VegaFusion 启用时:
        # - 使用 disable_max_rows() 避免限制
        # - 转换会在 to_dict 中正常执行
        with data_transformers.disable_max_rows():
            perform_save()
    else:
        # 非 VegaFusion 时:
        # - 强制切换到 'default' 转换器
        # - 原因: vl-convert 无法访问本地 JSON/CSV 文件
        # - 也禁用 max_rows 检查
        with data_transformers.enable("default"), data_transformers.disable_max_rows():
            perform_save()
```

**设计考量**:
1. **VegaFusion 模式**: 数据已经过预转换，结果数据量通常较小
2. **非 VegaFusion 模式**: 
   - `json`/`csv` 转换器会写入本地文件
   - `vl-convert`（用于 PNG/SVG/PDF 导出）运行在独立进程中
   - 独立进程无法访问 Python 脚本写入的临时文件路径
   - 因此必须强制使用 `default` 转换器内联数据

### 5.4 上下文标志协作

在 `to_dict()` 的 `context` 参数中，多个模块通过标志位协作：

| 标志 | 设置者 | 消费者 | 作用 |
|------|--------|--------|------|
| `context["data"]` | `TopLevelMixin.to_dict()` | `FacetMapping.to_dict()` | 引用原始数据用于列类型推断 |
| `context["top_level"]` | 默认为 `True` | `TopLevelMixin.to_dict()` | 控制是否添加 `$schema` 键 |
| `context["datasets"]` | 默认为 `{}` | `_consolidate_data()` | 存储合并后的命名数据集 |
| `context["pre_transform"]` | 递归调用时设为 `False` | `TopLevelMixin.to_dict()` 末尾 | 控制是否执行 VegaFusion 预转换 |

**`pre_transform` 标志的关键作用**:

```python
# 第一次调用 (用户触发)
chart.to_dict()  
# context = {"datasets": {}, "top_level": True, "pre_transform": True}

# 递归调用 (内部子对象)
super().to_dict(..., context=dict(context, pre_transform=False))
# context = {"datasets": {...}, "top_level": False, "pre_transform": False}

# 只有顶层调用会执行:
if context.get("pre_transform", True) and _using_vegafusion():
    return _compile_with_vegafusion(vegalite_spec)
```

这确保了：
1. 嵌套的 `to_dict()` 调用不会重复触发预转换
2. 只有最终的顶层 spec 会被完整处理

## 6. 策略选择决策树

```
                    ┌─────────────────────────────────┐
                    │  数据集大小是否超过 5000 行？      │
                    └─────────────────────────────────┘
                                │
            ┌───────────────────┼───────────────────┐
            │否                  │是                  │
            ▼                    ▼                    ▼
┌─────────────────────┐  ┌─────────────────────────────────┐
│ 使用 default 策略    │  │  能否在 Python 中预聚合数据？     │
│ (内嵌)              │  └─────────────────────────────────┘
└─────────────────────┘              │
                        ┌────────────┼────────────┐
                        │是           │否           │
                        ▼             ▼             ▼
            ┌─────────────────┐  ┌─────────────────────────┐
            │ pandas 预聚合   │  │ 是否安装了 vegafusion？  │
            │ (推荐做法)      │  └─────────────────────────┘
            └─────────────────┘              │
                                ┌────────────┼────────────┐
                                │是           │否           │
                                ▼             ▼             ▼
                    ┌─────────────────┐  ┌─────────────────────────┐
                    │ 启用 vegafusion │  │ 是否需要保存为静态文件？  │
                    │ (服务端预计算)  │  └─────────────────────────┘
                    └─────────────────┘              │
                                        ┌────────────┼────────────┐
                                        │是           │否           │
                                        ▼             ▼             ▼
                            ┌─────────────────┐  ┌─────────────────────────┐
                            │ 切换到 default  │  │ 环境是否支持本地文件？    │
                            │ (save() 会自动) │  └─────────────────────────┘
                            └─────────────────┘              │
                                                ┌────────────┼────────────┐
                                                │是           │否           │
                                                ▼             ▼             ▼
                                    ┌─────────────────┐  ┌─────────────────────────┐
                                    │ 启用 json/csv   │  │ 安装 altair_data_server  │
                                    │ (文件存储)      │  │ (HTTP 服务)              │
                                    └─────────────────┘  └─────────────────────────┘
```

## 7. 关键设计模式与最佳实践

### 7.1 设计模式

| 模式 | 应用位置 | 实现方式 |
|------|---------|---------|
| **Registry 模式** | 插件注册 | `PluginRegistry` 管理转换器生命周期 |
| **Strategy 模式** | 下发策略 | 不同转换器实现相同接口，可互换 |
| **Context Manager** | 临时启用 | `PluginEnabler` 支持 `with enable('x'):` |
| **Currying** | 参数配置 | `limit_rows(max_rows=10000)` 返回部分应用的函数 |
| **Weak Reference** | 内存管理 | `WeakValueDictionary` 存储 VegaFusion 内联表 |
| **Template Method** | 转换流程 | `default_data_transformer` 定义 `limit_rows → to_values` 管道 |

### 7.2 扩展点

用户可以通过以下方式自定义数据转换器：

```python
import altair as alt
from toolz.curried import pipe

# 方式1: 注册新转换器
def my_s3_transformer(data, bucket='my-bucket'):
    # 实现: 上传到 S3，返回 URL
    url = upload_to_s3(data, bucket)
    return {"url": url, "format": {"type": "json"}}

alt.data_transformers.register('s3', my_s3_transformer)
alt.data_transformers.enable('s3', bucket='my-data-bucket')

# 方式2: 组合现有转换器
from altair import limit_rows, to_json

def custom_transformer(data):
    return pipe(
        data,
        limit_rows(max_rows=20000),  # 自定义限制
        to_json(prefix='my-data')     # 自定义前缀
    )

alt.data_transformers.register('custom', custom_transformer)
alt.data_transformers.enable('custom')
```

### 7.3 常见问题与解决方案

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| `MaxRowsError` | 数据集超过 5000 行 | 1. 启用 VegaFusion<br>2. 使用 `json`/`csv` 转换器<br>3. pandas 预聚合<br>4. `disable_max_rows()` (不推荐) |
| 保存为 HTML 后图表不显示 | `json` 转换器写入的文件路径问题 | 保存时自动切换到 `default` 转换器（内置处理） |
| VegaFusion 时报 `format="vega"` 错误 | VegaFusion 需要编译到 Vega 格式 | 使用 `chart.to_dict(format="vega")` 或 `chart.to_json(format="vega")` |
| 交互式选择在大数据集上慢 | 选择需要全量数据在前端 | 使用 `JupyterChart` 或 `"jupyter"` renderer + VegaFusion |
| GeoPandas 数据与 VegaFusion | VegaFusion 不直接支持 GeoInterface | VegaFusion 自动回退到 `default` 转换器 |

## 8. 总结

Altair 的数据转换器系统是一个设计优雅、高度可扩展的架构：

1. **分层设计**: 从通用插件注册表到具体转换函数，每层职责清晰
2. **多策略支持**: 内嵌、文件、服务端三种核心策略，覆盖不同数据规模
3. **智能协作**: 模块间通过上下文标志和注册表状态无缝协作
4. **内存敏感**: VegaFusion 使用 `WeakValueDictionary` 避免内存泄漏
5. **用户友好**: 合理的默认值（5000 行限制）+ 清晰的错误信息

这个系统的核心价值在于：**将数据表示的复杂性从用户代码中抽象出来**，用户可以专注于可视化逻辑，而数据的传输方式通过简单的 `enable()` 调用即可切换。
