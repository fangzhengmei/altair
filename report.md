# Altair 数据适配层与渲染后端分发层深度分析

## 一、数据适配层：数据类型自动推断机制

### 1.1 核心数据类型定义

Altair 通过类型别名和协议定义了一套清晰的数据类型体系，位于 `altair/utils/data.py:57-59`：

```python
DataType: TypeAlias = (
    dict[Any, Any] | IntoDataFrame | SupportsGeoInterface | DataFrameLike
)
```

支持的数据类型包括：
- **dict**: 包含 `values` 键的字典（直接内联数据）
- **IntoDataFrame**: 通过 Narwhals 库支持的 DataFrame 类型（pandas, Polars, PyArrow 等）
- **SupportsGeoInterface**: 实现 `__geo_interface__` 协议的地理数据对象
- **DataFrameLike**: 实现 DataFrame Interchange Protocol 的对象

### 1.1.1 完整数据类型体系：ChartDataType

实际上，`Chart` 类接受的数据类型比 `DataType` 更广泛，定义在 `altair/vegalite/v6/api.py:206`：

```python
ChartDataType: TypeAlias = Optional[DataType | core.Data | str | core.Generator]
```

**完整的可接受数据类型：**

| 类型分类 | 具体类型 | 说明 |
|---------|---------|------|
| `DataType` | dict, DataFrame, SupportsGeoInterface, DataFrameLike | 走数据转换器的标准路径 |
| `core.Data` | `InlineData`, `UrlData`, `NamedData` | 原生 Vega-Lite 数据对象，直接使用 |
| `str` | URL 字符串 | 被转换为 `UrlData` |
| `core.Generator` | `GraticuleGenerator`, `SphereGenerator`, `SequenceGenerator` | **生成器数据源，绕过数据转换器** |

### 1.2 生成器数据源：绕过转换器的特殊路径

#### 1.2.1 三种生成器类型

Altair 内置三种"生成器数据源"，它们直接使用 Vega-Lite 内置的数据生成逻辑，完全绕过数据转换器：

| 生成器类型 | 工厂函数 | 作用 | Vega-Lite spec 输出 |
|-----------|---------|------|-------------------|
| `GraticuleGenerator` | `alt.graticule()` | 生成经纬网格地理数据 | `{"graticule": true}` 或 `{"graticule": {...}}` |
| `SphereGenerator` | `alt.sphere()` | 生成整个地球球面的 GeoJSON | `{"sphere": true}` |
| `SequenceGenerator` | `alt.sequence(start, stop, step)` | 生成数值序列 | `{"sequence": {"start": ..., "stop": ...}}` |

**工厂函数实现**（位于 `altair/vegalite/v6/api.py:5462-5487`）：

```python
@utils.use_signature_func(core.SequenceParams)
def sequence(
    start: Optional[float],
    stop: Optional[float | None] = None,
    step: Optional[float] = Undefined,
    as_: Optional[str] = Undefined,
    **kwds: Any,
) -> SequenceGenerator:
    """Sequence generator."""
    if stop is None:
        start, stop = 0, start
    params = core.SequenceParams(start=start, stop=stop, step=step, **{"as": as_})
    return core.SequenceGenerator(sequence=params, **kwds)


@utils.use_signature_func(core.GraticuleParams)
def graticule(**kwds: Any) -> GraticuleGenerator:
    """Graticule generator."""
    graticule: Any = core.GraticuleParams(**kwds) if kwds else True
    return core.GraticuleGenerator(graticule=graticule)


def sphere() -> SphereGenerator:
    """Sphere generator."""
    return core.SphereGenerator(sphere=True)
```

#### 1.2.2 继承关系：为什么它们绕过转换器

生成器类型的继承链（位于 `altair/vegalite/v6/schema/core.py`）：

```
VegaLiteSchema (基类)
    │
    ├── Generator
    │       ├── GraticuleGenerator
    │       ├── SphereGenerator
    │       └── SequenceGenerator
    │
    └── Data
            ├── InlineData
            ├── UrlData
            └── NamedData
```

**关键代码分析 - _prepare_data 函数**（`altair/vegalite/v6/api.py:266-302`）：

```python
def _prepare_data(
    data: ChartDataType, context: dict[str, Any] | None = None
) -> ChartDataType | NamedData | InlineData | UrlData | Any:
    if data is Undefined:
        return data

    # 关键条件：非 dict 且是 DataType → 调用数据转换器
    elif not isinstance(data, dict) and _is_data_type(data):
        if func := data_transformers.get():
            data = func(nw.to_native(data, pass_through=True))

    # 字符串 → UrlData
    elif isinstance(data, str):
        data = core.UrlData(data)

    # 内联数据合并到顶层 datasets（可选）
    if context is not None and data_transformers.consolidate_datasets:
        data = _consolidate_data(data, context)

    if not isinstance(data, (dict, core.Data)):
        warnings.warn(f"data of type {type(data)} not recognized", stacklevel=1)

    return data
```

**生成器绕过转换器的原因：**

| 条件判断 | 生成器类型的结果 | 说明 |
|---------|-----------------|------|
| `isinstance(data, dict)` | `False` | 生成器是 `Generator` 实例，不是 dict |
| `_is_data_type(data)` | `False` | 生成器不是 DataFrame、没有 `__geo_interface__` |
| `not isinstance(data, dict) and _is_data_type(data)` | `False` | 关键条件不满足！ |

**`is_data_type` 函数定义**（`altair/utils/data.py:69-73`）：

```python
def is_data_type(obj: Any) -> TypeIs[DataType]:
    return isinstance(obj, (dict, SupportsGeoInterface)) or isinstance(
        nw.from_native(obj, eager_or_interchange_only=True, pass_through=True),
        nw.DataFrame,
    )
```

生成器类型不满足任何一个条件：
1. 不是 `dict`
2. 没有实现 `SupportsGeoInterface` 协议
3. 无法被 `narwhals.from_native()` 转换为 DataFrame

#### 1.2.3 完整处理流程对比

**普通 DataFrame 数据的处理流程：**

```
用户: alt.Chart(df)
         │
         ▼
    Chart.data = df
         │
         ▼
    to_dict() 调用 _prepare_data(df, context)
         │
         ▼
    条件判断: not isinstance(df, dict) and is_data_type(df)
         │
         ├── isinstance(df, dict)? → False
         └── is_data_type(df)? → True (DataFrame)
         │
         ▼
    条件满足！调用数据转换器:
    data_transformers.get() → default_data_transformer
         │
         ▼
    default_data_transformer(df):
         ├── limit_rows(df, max_rows=5000)  # 行数检查
         └── to_values(df)                   # 转为 {"values": [...]}
         │
         ▼
    输出: {"values": [...]}
         │
         ▼
    合并到 spec["data"]
```

**生成器数据源的处理流程：**

```
用户: alt.Chart(alt.graticule())
         │
         ▼
    Chart.data = GraticuleGenerator(graticule=True)
         │
         ▼
    to_dict() 调用 _prepare_data(data, context)
         │
         ▼
    条件判断: not isinstance(data, dict) and is_data_type(data)
         │
         ├── isinstance(data, dict)? → False
         └── is_data_type(data)? → False (生成器不是 DataType)
         │
         ▼
    条件不满足！跳过数据转换器！
         │
         ▼
    下一个条件: isinstance(data, str)? → False
         │
         ▼
    consolidate_datasets 检查:
    isinstance(data, (InlineData, dict with values))? → False
         │
         ├── 生成器是 Generator，不是 InlineData
         └── 生成器没有 "values" 键
         │
         ▼
    直接返回原始的 GraticuleGenerator 实例
         │
         ▼
    最终: GraticuleGenerator.to_dict() 被调用
         │
         ▼
    输出: {"graticule": true}
         │
         ▼
    合并到 spec["data"]
```

#### 1.2.4 最终序列化：SchemaBase.to_dict()

生成器类型最终通过 `SchemaBase.to_dict()` 方法序列化为 Vega-Lite spec：

**GraticuleGenerator 类定义**（`altair/vegalite/v6/schema/core.py:8146-8170`）：

```python
class GraticuleGenerator(Generator):
    """
    GraticuleGenerator schema wrapper.
    
    Parameters
    ----------
    graticule : dict, Literal[True], :class:`GraticuleParams`
        Generate graticule GeoJSON data for geographic reference lines.
    name : str
        Provide a placeholder name and bind data at runtime.
    """
    _schema = {"$ref": "#/definitions/GraticuleGenerator"}
    
    def __init__(
        self,
        graticule: Any = Undefined,
        name: Optional[str] = Undefined,
        **kwds,
    ):
        super().__init__(graticule=graticule, name=name, **kwds)
```

**使用示例：**

```python
import altair as alt
from vega_datasets import data

# 1. 使用 graticule 生成器
graticule = alt.Chart(alt.graticule()).mark_geoshape(stroke="gray")

# 2. 使用 sphere 生成器
sphere = alt.Chart(alt.sphere()).mark_geoshape(fill="lightblue")

# 3. 使用 sequence 生成器
seq = alt.Chart(alt.sequence(0, 10, 0.5)).mark_point().encode(
    x="data:Q",
    y="data:Q"
)

# 查看生成的 spec
print(graticule.to_dict())
# 输出:
# {
#   "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
#   "data": {"graticule": true},  # 不是 {"values": [...]}
#   "mark": {"type": "geoshape", "stroke": "gray"}
# }
```

#### 1.2.5 生成器数据源的设计意图

| 设计考虑 | 说明 |
|---------|------|
| **性能优化** | 网格和球面数据是固定模式，Vega-Lite 可以在浏览器端高效生成，无需在 Python 端预计算 |
| **数据体积** | `{"graticule": true}` 只有十几字节，而实际的 GeoJSON 网格数据可能有数千行 |
| **延迟计算** | 真正的数据生成延迟到 Vega-Lite 运行时，用户可以在 spec 中调整网格参数 |
| **地图专用** | Graticule 和 Sphere 是地理可视化的专用辅助数据，无需通用数据转换器处理 |

### 1.3 数据类型推断机制

数据类型的自动推断由 `is_data_type` 函数实现 (`altair/utils/data.py:69-73`)：

```python
def is_data_type(obj: Any) -> TypeIs[DataType]:
    return isinstance(obj, (dict, SupportsGeoInterface)) or isinstance(
        nw.from_native(obj, eager_or_interchange_only=True, pass_through=True),
        nw.DataFrame,
    )
```

**推断逻辑：**
1. 首先检查是否为 `dict` 或实现了 `SupportsGeoInterface` 协议
2. 否则通过 `narwhals.from_native()` 尝试转换为 DataFrame
3. Narwhals 库提供了对多种 DataFrame 库的统一抽象层

### 1.3 数据转换器注册表

数据适配层的核心是 `DataTransformerRegistry`，它继承自 `PluginRegistry`，位于 `altair/utils/data.py:94-103`：

```python
class DataTransformerRegistry(PluginRegistry[DataTransformerType, R]):
    _global_settings = {"consolidate_datasets": True}
```

**已注册的转换器**（位于 `altair/vegalite/v6/data.py:20-29`）：

| 转换器名称 | 函数 | 作用 | 输出格式 |
|-----------|------|------|---------|
| `default` | `default_data_transformer` | 内联数据（默认） | `{"values": [...]}` |
| `json` | `to_json` | 导出为外部 JSON 文件 | `{"url": "...", "format": {"type": "json"}}` |
| `csv` | `to_csv` | 导出为外部 CSV 文件 | `{"url": "...", "format": {"type": "csv"}}` |
| `vegafusion` | `vegafusion_data_transformer` | 大数据服务端处理 | 特殊处理 |

### 1.4 数据转换链路详解

#### 1.4.1 默认转换器链路

默认数据转换器定义在 `altair/vegalite/data.py:32-44`：

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

**转换链路：**
1. **limit_rows**: 检查数据行数，超过 5000 行抛出 `MaxRowsError`
2. **to_values**: 将各种数据类型转换为 `{"values": [...]}` 格式

#### 1.4.2 to_values 详细转换逻辑

`to_values` 函数 (`altair/utils/data.py:317-338`) 处理不同输入类型：

```python
def to_values(data: DataType) -> ToValuesReturnType:
    check_data_type(data)
    data_native = nw.to_native(data, pass_through=True)
    
    if isinstance(data_native, SupportsGeoInterface):
        return {"values": _from_geo_interface(data_native)}
    elif is_pandas_dataframe(data_native):
        data_native = sanitize_pandas_dataframe(data_native)
        return {"values": data_native.to_dict(orient="records")}
    elif isinstance(data_native, dict):
        if "values" not in data_native:
            raise KeyError("values expected in data dict, but not present.")
        return data_native
    elif isinstance(data, nw.DataFrame):
        data = sanitize_narwhals_dataframe(data)
        return {"values": data.rows(named=True)}
    else:
        raise ValueError(f"Unrecognized data type: {type(data)}")
```

**转换分支：**

| 输入类型 | 处理方式 | 关键函数 |
|---------|---------|---------|
| SupportsGeoInterface | 提取 `__geo_interface__` 并清理 | `_from_geo_interface`, `sanitize_geo_interface` |
| Pandas DataFrame | 清理特殊类型 → 转为 records 列表 | `sanitize_pandas_dataframe`, `to_dict(orient="records")` |
| Dict (含 values) | 直接返回 | 无 |
| Narwhals DataFrame | 清理 → 转为命名行列表 | `sanitize_narwhals_dataframe`, `rows(named=True)` |

#### 1.4.3 to_json / to_csv 外部文件转换

这两个转换器将数据写入外部文件，并返回 URL 引用格式：

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

**关键特性：**
- 使用内容哈希作为文件名的一部分，避免重复写入
- 返回格式：`{"url": "altair-data-<hash>.json", "format": {"type": "json"}}`
- Vega-Lite 会自动从 URL 加载数据

### 1.5 数据清理（Sanitization）机制

#### 1.5.1 Pandas DataFrame 清理

`sanitize_pandas_dataframe` (`altair/utils/core.py:337-466`) 处理各种特殊类型：

**清理内容：**
1. **列名处理**：RangeIndex 转为字符串，非字符串列名抛出错误
2. **多级索引**：不支持，抛出 ValueError
3. **分类类型**：转为 object，NaN 转为 None
4. **字符串类型**：转为 object，NaN 转为 None
5. **布尔类型**：np.bool_ 转为 Python bool
6. **Nullable 类型**（Int8, Int16, Float32 等）：转为 object，NA 转为 None
7. **日期时间类型**：转为 ISO 格式字符串（含时区信息）
8. **时间差类型**：不支持，抛出 ValueError
9. **数值类型**：转为 Python 对象，inf 转为 None
10. **对象类型**：numpy 数组转为列表

#### 1.5.2 Narwhals DataFrame 清理

`sanitize_narwhals_dataframe` (`altair/utils/core.py:469-504`) 处理通用 DataFrame：

**清理内容：**
1. **日期类型**：转为 ISO 格式字符串
2. **日期时间类型**：转为 ISO 格式字符串，保留时区信息
3. **时间差类型**：不支持，抛出 ValueError

### 1.6 临界规则总结

| 规则类型 | 规则内容 | 实现位置 |
|---------|---------|---------|
| 行数限制 | 默认 5000 行，超过抛出 `MaxRowsError` | `limit_rows`, `altair/utils/data.py:131-165` |
| 数据类型 | 必须是 dict、DataFrame 或实现 `__geo_interface__` | `is_data_type`, `altair/utils/data.py:69-73` |
| 列名限制 | 必须是字符串，多级索引不支持 | `sanitize_pandas_dataframe` |
| 时间差类型 | 不支持 Timedelta，需转为数值或时间戳 | `sanitize_pandas_dataframe` |
| GeoJSON 处理 | 自动合并 properties 与 geometry | `sanitize_geo_interface` |

---

## 二、渲染后端分发层：注册与切换机制

### 2.1 渲染器注册表架构

渲染后端的核心是 `RendererRegistry`，继承自 `PluginRegistry`，位于 `altair/utils/display.py:37-116`：

```python
class RendererRegistry(PluginRegistry[RendererType, MimeBundleType]):
    entrypoint_err_messages = {
        "notebook": textwrap.dedent("""
            To use the 'notebook' renderer, you must install the vega package...
        """),
    }

    def set_embed_options(self, defaultStyle=None, renderer=None, width=None, ...):
        # 设置 vega-embed 选项
        ...
```

**核心特性：**
- 继承自 `PluginRegistry`，支持插件化注册
- 通过 setuptools entry points 支持自动发现外部渲染器
- 入口点组：`altair.vegalite.v6.renderer`
- 提供 `set_embed_options` 方法配置 vega-embed 渲染参数

### 2.2 已注册的渲染器

渲染器注册位于 `altair/vegalite/v6/display.py:150-166`：

```python
renderers = RendererRegistry(entry_point_group=ENTRY_POINT_GROUP)

renderers.register("default", html_renderer)
renderers.register("html", html_renderer)
renderers.register("colab", html_renderer)
renderers.register("kaggle", html_renderer)
renderers.register("zeppelin", html_renderer)
renderers.register("mimetype", mimetype_renderer)
renderers.register("jupyterlab", mimetype_renderer)
renderers.register("nteract", mimetype_renderer)
renderers.register("json", json_renderer)
renderers.register("png", png_renderer)
renderers.register("svg", svg_renderer)
renderers.register("jupyter", jupyter_renderer)
renderers.register("browser", browser_renderer)
renderers.register("olli", olli_renderer)
renderers.enable("default")
```

### 2.3 渲染器详解

#### 2.3.1 渲染器分类

| 渲染器名称 | 核心函数 | 输出格式 | 适用场景 |
|-----------|---------|---------|---------|
| `default`/`html` | `HTMLRenderer` | `{"text/html": "..."}` | 通用 HTML 渲染 |
| `colab`/`kaggle`/`zeppelin` | `HTMLRenderer` | `{"text/html": "..."}` | 特定 Notebook 环境 |
| `mimetype`/`jupyterlab`/`nteract` | `mimetype_renderer` | `{"application/vnd.vegalite.v6+json": spec}` | 支持 Vega-Lite MIME 类型的前端 |
| `json` | `json_renderer` | `{"application/json": spec}` | 调试/查看原始 spec |
| `png` | `png_renderer` | `{"image/png": bytes}` | 静态图片导出 |
| `svg` | `svg_renderer` | `{"image/svg+xml": str}` | 矢量图导出 |
| `jupyter` | `jupyter_renderer` | Jupyter Widget | 交互式 JupyterChart |
| `browser` | `browser_renderer` | 打开浏览器窗口 | 独立浏览器查看 |
| `olli` | `HTMLRenderer(template="olli")` | 可访问性增强 HTML | 无障碍访问 |

#### 2.3.2 MIME 类型渲染器

`mimetype_renderer` 定义在 `altair/vegalite/v6/display.py:56-57`：

```python
def mimetype_renderer(spec: dict, **metadata) -> DefaultRendererReturnType:
    return default_renderer_base(spec, VEGALITE_MIME_TYPE, DEFAULT_DISPLAY, **metadata)
```

`default_renderer_base` 位于 `altair/utils/display.py:170-198`：

```python
def default_renderer_base(
    spec: dict[str, Any], mime_type: str, str_repr: str, **options
) -> DefaultRendererReturnType:
    bundle: dict[str, str | dict] = {}
    metadata: dict[str, dict[str, Any]] = {}

    if using_vegafusion():
        spec = compile_with_vegafusion(spec)
        if mime_type == VEGALITE_MIME_TYPE:
            mime_type = VEGA_MIME_TYPE

    bundle[mime_type] = spec
    bundle["text/plain"] = str_repr
    if options:
        metadata[mime_type] = options
    return bundle, metadata
```

**输出格式：**
```python
({
    "application/vnd.vegalite.v6+json": {...},  # Vega-Lite spec
    "text/plain": "<VegaLite 6 object>..."      # 纯文本表示
}, {
    "application/vnd.vegalite.v6+json": {...}   # 元数据（如 embed_options）
})
```

#### 2.3.3 HTML 渲染器

`HTMLRenderer` 类位于 `altair/utils/display.py:214-228`：

```python
class HTMLRenderer:
    def __init__(self, output_div: str = "altair-viz-{}", **kwargs) -> None:
        self._output_div = output_div
        self.kwargs = kwargs

    @property
    def output_div(self) -> str:
        return self._output_div.format(uuid.uuid4().hex)

    def __call__(self, spec: dict[str, Any], **metadata) -> dict[str, str]:
        kwargs = self.kwargs.copy()
        kwargs.update(**metadata, output_div=self.output_div)
        return spec_to_mimebundle(spec, format="html", **kwargs)
```

**关键特性：**
- 每次调用生成唯一的 `output_div` ID（使用 UUID）
- 委托给 `spec_to_mimebundle` 生成完整 HTML

#### 2.3.4 PNG/SVG/PDF 渲染器

这些渲染器通过 `vl-convert` 库进行转换，以 `png_renderer` 为例 (`altair/vegalite/v6/display.py:64-75`)：

```python
def png_renderer(spec: dict, **metadata) -> dict[str, bytes]:
    return spec_to_mimebundle(
        spec,
        format="png",
        mode="vega-lite",
        vega_version=VEGA_VERSION,
        vegaembed_version=VEGAEMBED_VERSION,
        vegalite_version=VEGALITE_VERSION,
        **metadata,
    )
```

`spec_to_mimebundle` 中的 PNG 转换逻辑 (`altair/utils/mimebundle.py:232-258`)：

```python
elif format == "png":
    scale = kwargs.get("scale_factor", 1)
    default_ppi = 72
    ppi = kwargs.get("ppi", default_ppi)
    if mode == "vega":
        png = vlc.vega_to_png(spec, scale=scale, ppi=ppi, ...)
    else:
        png = vlc.vegalite_to_png(spec, vl_version=vl_version, scale=scale, ppi=ppi, ...)
    factor = ppi / default_ppi
    w, h = _pngxy(png)
    return {"image/png": png}, {
        "image/png": {"width": w / factor, "height": h / factor}
    }
```

### 2.4 渲染器切换机制

#### 2.4.1 基础切换

使用 `renderers.enable()` 方法切换渲染器：

```python
import altair as alt

# 切换到 mimetype 渲染器
alt.renderers.enable("mimetype")

# 切换到 html 渲染器
alt.renderers.enable("html")
```

#### 2.4.2 上下文管理器（临时切换）

`enable()` 方法返回 `PluginEnabler` 上下文管理器，可用于临时切换：

```python
# 临时使用 png 渲染器
with alt.renderers.enable("png"):
    chart.display()  # 输出为 PNG

# 回到之前的渲染器
chart.display()  # 输出为默认格式
```

`PluginEnabler` 实现位于 `altair/utils/plugin_registry.py:52-80`：

```python
class PluginEnabler(Generic[PluginT, R]):
    def __init__(
        self, registry: PluginRegistry[PluginT, R], name: str, **options: Any
    ) -> None:
        self.registry = registry
        self.name = name
        self.options = options
        self.original_state = registry._get_state()  # 保存原始状态
        self.registry._enable(name, **options)      # 启用新插件

    def __enter__(self) -> PluginEnabler[PluginT, R]:
        return self

    def __exit__(self, typ: type, value: Exception, traceback: TracebackType) -> None:
        self.registry._set_state(self.original_state)  # 恢复原始状态
```

#### 2.4.3 状态管理

`PluginRegistry` 的状态管理方法：

```python
def _get_state(self) -> dict[str, Any]:
    return {
        "_active": self._active,
        "_active_name": self._active_name,
        "_plugins": self._plugins.copy(),
        "_options": self._options.copy(),
        "_global_settings": self._global_settings.copy(),
    }

def _set_state(self, state: dict[str, Any]) -> None:
    for key, val in state.items():
        setattr(self, key, val)
```

### 2.5 MIME Bundle 分发机制

`spec_to_mimebundle` 函数 (`altair/utils/mimebundle.py:65-168`) 是渲染后端的核心分发点：

```python
def spec_to_mimebundle(
    spec: dict[str, Any],
    format: MimeBundleFormat,
    mode: Literal["vega-lite"] | None = None,
    ...
) -> dict[str, Any] | tuple[dict[str, Any], dict[str, Any]]:
    # VegaFusion 预处理
    if using_vegafusion():
        spec = compile_with_vegafusion(spec)
        internal_mode = "vega"
    
    # 根据 format 分发
    if format in {"png", "svg", "pdf", "vega"}:
        return _spec_to_mimebundle_with_engine(spec, format, internal_mode, ...)
    elif format == "html":
        html = spec_to_html(spec, mode=internal_mode, ...)
        return {"text/html": html}
    elif format == "vega-lite":
        return {f"application/vnd.vegalite.v{vegalite_version[0]}+json": spec}
    elif format == "json":
        return {"application/json": spec}
    else:
        raise ValueError(...)
```

**分发逻辑：**

| format 参数 | 处理方式 | 输出 MIME 类型 |
|-------------|---------|--------------|
| `png`/`svg`/`pdf`/`vega` | 调用 `_spec_to_mimebundle_with_engine`（vl-convert） | `image/png`, `image/svg+xml`, `application/pdf`, `application/vnd.vega.v6+json` |
| `html` | 调用 `spec_to_html` 生成完整 HTML | `text/html` |
| `vega-lite` | 直接返回 spec | `application/vnd.vegalite.v6+json` |
| `json` | 直接返回 spec | `application/json` |

### 2.6 HTML 模板系统

`spec_to_html` 函数 (`altair/utils/html.py:307-411`) 使用 Jinja2 模板生成 HTML：

**可用模板：**

| 模板名称 | 模板变量 | 用途 |
|---------|---------|------|
| `standard` | `HTML_TEMPLATE` | 标准 HTML 页面（CDN 加载 JS） |
| `universal` | `HTML_TEMPLATE_UNIVERSAL` | 通用模板（动态加载 JS，兼容各种环境） |
| `inline` | `INLINE_HTML_TEMPLATE` | 内联所有 JS（离线可用） |
| `olli` | `HTML_TEMPLATE_OLLI` | 可访问性增强（Olli 库） |

**模板关键特性：**
- **standard**: 直接使用 `<script>` 标签从 CDN 加载 Vega、Vega-Lite、Vega-Embed
- **universal**: 动态检查并加载所需 JS 库，支持 AMD/RequireJS
- **inline**: 使用 vl-convert 获取完整的 JS bundle 并内联到 HTML
- **olli**: 集成 Olli 可访问性库，提供屏幕阅读器支持

---

## 三、两层协作：完整图表输出流程

### 3.1 架构概览

```
┌─────────────────────────────────────────────────────────────────┐
│                         用户代码层                                │
│  Chart(data).mark_point().encode(x="foo", y="bar")             │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      数据适配层 (Data Layer)                     │
│  ┌──────────────┐    ┌──────────────────┐    ┌──────────────┐  │
│  │ is_data_type │───▶│ _prepare_data    │───▶│ data_        │  │
│  │ (类型判断)    │    │ (数据准备)       │    │ transformers │  │
│  └──────────────┘    └──────────────────┘    │ (转换器注册表) │  │
│                                                └──────────────┘  │
│  输出：Vega-Lite spec dict (data 部分已处理)                      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    渲染后端分发层 (Render Layer)                  │
│  ┌──────────────┐    ┌──────────────────┐    ┌──────────────┐  │
│  │ _repr_       │───▶│ renderers.get()  │───▶│ spec_to_     │  │
│  │ mimebundle_  │    │ (获取渲染器)      │    │ mimebundle   │  │
│  │ (Jupyter入口) │    └──────────────────┘    │ (格式分发)    │  │
│  └──────────────┘                            └──────────────┘  │
│  输出：MIME bundle (适用于当前渲染目标的格式)                     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                         渲染目标层                                │
│  JupyterLab / Colab / 静态 HTML / PNG 图片 / 浏览器窗口        │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 详细协作流程

#### 3.2.1 流程总览

一次完整的图表输出经历以下阶段：

1. **图表构建**：用户创建 `Chart` 对象并配置编码
2. **触发输出**：Jupyter 调用 `_repr_mimebundle_` 或用户调用 `save()`/`to_html()`
3. **数据转换**：`to_dict()` → `_prepare_data()` → 数据转换器处理
4. **规范生成**：生成完整的 Vega-Lite spec 字典
5. **渲染分发**：`renderers.get()` → 渲染器处理 → `spec_to_mimebundle`
6. **输出交付**：返回 MIME bundle 供前端渲染

#### 3.2.2 阶段一：图表构建与触发

用户代码示例：
```python
import altair as alt
from vega_datasets import data

cars = data.cars()  # pandas DataFrame

chart = alt.Chart(cars).mark_point().encode(
    x="Horsepower:Q",
    y="Miles_per_Gallon:Q",
    color="Origin:N"
)

chart  # Jupyter 自动显示，触发 _repr_mimebundle_
```

#### 3.2.3 阶段二：Jupyter 显示入口

`_repr_mimebundle_` 方法位于 `altair/vegalite/v6/api.py:3799-3810`：

```python
def _repr_mimebundle_(self, *args: Any, **kwds: Any) -> MimeBundleType | None:
    try:
        dct = self.to_dict(context={"pre_transform": False})
    except Exception:
        utils.display_traceback(in_ipython=True)
        return {}
    else:
        if renderer := renderers.get():
            return renderer(dct)
```

**关键步骤：**
1. 调用 `to_dict()` 生成 Vega-Lite spec
2. 从 `renderers.get()` 获取当前激活的渲染器
3. 将 spec 传递给渲染器，返回 MIME bundle

#### 3.2.4 阶段三：数据转换（数据适配层核心）

`to_dict` 方法位于 `altair/vegalite/v6/api.py:2044-2157`：

```python
def to_dict(
    self,
    validate: bool = True,
    *,
    format: Literal["vega-lite", "vega"] = "vega-lite",
    ignore: list[str] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # 上下文初始化
    context = context.copy() if context else {}
    context.setdefault("datasets", {})
    is_top_level = context.get("top_level", True)

    # 数据准备核心
    copy = _top_schema_base(self).copy(deep=False)
    original_data = getattr(copy, "data", Undefined)
    
    if not utils.is_undefined(original_data):
        try:
            data = nw.from_native(original_data, eager_or_interchange_only=True)
        except TypeError:
            data = original_data
        copy.data = _prepare_data(data, context)  # 关键调用
        context["data"] = data

    # 生成 spec
    vegalite_spec: Any = _top_schema_base(super(TopLevelMixin, copy)).to_dict(...)

    # 顶层处理
    if is_top_level:
        if "$schema" not in vegalite_spec:
            vegalite_spec["$schema"] = SCHEMA_URL
        
        # 应用主题
        if func := theme.get():
            vegalite_spec = utils.update_nested(func(), vegalite_spec, copy=True)
        
        # 合并数据集
        if context["datasets"]:
            vegalite_spec.setdefault("datasets", {}).update(context["datasets"])

    # VegaFusion 或 Vega 格式转换
    if context.get("pre_transform", True) and _using_vegafusion():
        if format == "vega-lite":
            raise ValueError(...)
        else:
            return _compile_with_vegafusion(vegalite_spec)
    elif format == "vega":
        plugin = vegalite_compilers.get()
        return plugin(vegalite_spec)
    else:
        return vegalite_spec
```

#### 3.2.5 阶段四：_prepare_data 详解

`_prepare_data` 函数 (`altair/vegalite/v6/api.py:266-302`) 是数据适配层的核心：

```python
def _prepare_data(
    data: ChartDataType, context: dict[str, Any] | None = None
) -> ChartDataType | NamedData | InlineData | UrlData | Any:
    if data is Undefined:
        return data

    # 核心：DataFrame 或 GeoInterface → 调用数据转换器
    elif not isinstance(data, dict) and _is_data_type(data):
        if func := data_transformers.get():
            data = func(nw.to_native(data, pass_through=True))

    # 字符串 → UrlData
    elif isinstance(data, str):
        data = core.UrlData(data)

    # 内联数据合并到顶层 datasets（可选）
    if context is not None and data_transformers.consolidate_datasets:
        data = _consolidate_data(data, context)

    if not isinstance(data, (dict, core.Data)):
        warnings.warn(f"data of type {type(data)} not recognized", stacklevel=1)

    return data
```

**数据流：**

```
输入数据
    │
    ▼
┌─────────────────────────────────────────┐
│ 1. is_data_type(data) 判断类型          │
│    - dict? → 跳过转换                    │
│    - DataFrame / GeoInterface? → 继续   │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│ 2. data_transformers.get() 获取转换器   │
│    - default → limit_rows → to_values   │
│    - json → 写入文件 → 返回 URL         │
│    - csv → 写入文件 → 返回 URL          │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│ 3. consolidate_datasets（可选）         │
│    - 内联数据 → 移动到顶层 datasets      │
│    - 使用 NamedData 引用                 │
└─────────────────────────────────────────┘
    │
    ▼
  输出：处理后的数据格式
    - {"values": [...]}          (内联)
    - {"url": "data.json", ...}  (外部引用)
    - NamedData(name="data-xxx") (合并引用)
```

#### 3.2.6 阶段五：数据合并（_consolidate_data）

`_consolidate_data` 函数 (`altair/vegalite/v6/api.py:235-263`) 实现数据去重和共享：

```python
def _consolidate_data(
    data: ChartDataType | UrlData, context: dict[str, Any]
) -> ChartDataType | NamedData | InlineData | UrlData:
    values: Any = Undefined
    kwds: dict = {}

    # InlineData 类型
    if isinstance(data, core.InlineData):
        if utils.is_undefined(data.name) and not utils.is_undefined(data.values):
            if isinstance(data.values, core.InlineDataset):
                values = data.to_dict()["values"]
            else:
                values = data.values
            kwds = {"format": data.format}

    # Dict 类型（含 values）
    elif isinstance(data, dict) and ("name" not in data) and ("values" in data):
        values = data["values"]
        kwds = {k: v for k, v in data.items() if k != "values"}

    # 有 values → 计算 hash，合并到 datasets
    if not utils.is_undefined(values):
        name = _dataset_name(values)  # 基于内容的 hash
        data = core.NamedData(name=name, **kwds)
        context.setdefault("datasets", {})[name] = values

    return data
```

**合并机制的优势：**
- **去重**：相同内容的数据使用相同的 name，避免重复
- **共享**：多层图表（layer, concat）中的相同数据只存储一次
- **引用**：使用 `{"name": "data-xxx"}` 引用顶层 `datasets`

#### 3.2.7 阶段六：渲染分发（渲染后端层核心）

回到 `_repr_mimebundle_` 的渲染阶段：

```python
# to_dict() 已返回 spec
dct = self.to_dict(context={"pre_transform": False})

# 获取当前渲染器
if renderer := renderers.get():
    return renderer(dct)
```

**渲染器获取：** `renderers.get()` 方法 (`altair/utils/plugin_registry.py:262-276`)：

```python
def get(self) -> partial[R] | Plugin[R] | None:
    if (func := self._active) and self.plugin_type(func):
        # 如果有 options，用 partial 包装
        return partial(func, **self._options) if self._options else func
    elif self._active is not None:
        raise TypeError(...)
    elif TYPE_CHECKING:
        raise NotImplementedError
```

**渲染器调用链路（以 default/html 渲染器为例）：**

```
renderer(spec)
    │
    ▼
HTMLRenderer.__call__(spec, **metadata)
    │
    ▼
spec_to_mimebundle(spec, format="html", ...)
    │
    ▼
spec_to_html(spec, mode="vega-lite", ...)
    │
    ▼
Jinja2 模板渲染 → 返回完整 HTML 字符串
    │
    ▼
{"text/html": "<!DOCTYPE html><html>..."}
```

### 3.3 不同输出场景的协作示例

#### 3.3.1 场景一：JupyterLab 显示（mimetype 渲染器）

```python
import altair as alt

# 切换到 mimetype 渲染器（JupyterLab 原生支持）
alt.renderers.enable("mimetype")

# 创建并显示图表
chart = alt.Chart(df).mark_point().encode(x="x", y="y")
chart  # 触发 _repr_mimebundle_
```

**流程详解：**

| 步骤 | 层级 | 关键操作 | 输出 |
|-----|------|---------|------|
| 1 | 用户层 | `chart` 表达式触发 Jupyter 显示机制 | - |
| 2 | 渲染入口 | `_repr_mimebundle_()` 被调用 | - |
| 3 | 数据适配层 | `to_dict()` → `_prepare_data()` → `default_data_transformer()` | `{"values": [...]}` |
| 4 | 数据适配层 | 生成完整 spec，包含 `$schema`、主题等 | 完整 Vega-Lite dict |
| 5 | 渲染后端层 | `renderers.get()` → `mimetype_renderer` | `mimetype_renderer` 函数 |
| 6 | 渲染后端层 | `mimetype_renderer(spec)` → `default_renderer_base()` | MIME bundle |

**最终输出（MIME bundle）：**
```python
({
    "application/vnd.vegalite.v6+json": {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": [...]},
        "mark": "point",
        "encoding": {...}
    },
    "text/plain": "<VegaLite 6 object>\n\nIf you see this message..."
}, {})
```

#### 3.3.2 场景二：保存为 PNG 文件

```python
chart.save("chart.png", scale_factor=2)
```

**流程详解（基于 `altair/utils/save.py:171-284`）：**

| 步骤 | 层级 | 关键操作 |
|-----|------|---------|
| 1 | 用户层 | 调用 `chart.save("chart.png")` |
| 2 | 保存入口 | `set_inspect_format_argument()` 推断 format = "png" |
| 3 | 数据适配层 | `with data_transformers.enable("default"), data_transformers.disable_max_rows():` → `chart.to_dict()` |
| 4 | 数据适配层 | 确保数据内联（vl-convert 无法访问本地文件） |
| 5 | 渲染后端层 | `_save_mimebundle_format()` → `spec_to_mimebundle(format="png")` |
| 6 | 渲染后端层 | `_spec_to_mimebundle_with_engine()` → `vlc.vegalite_to_png()` |
| 7 | 输出层 | `write_file_or_filename()` 写入文件 |

**关键代码（save 函数中的数据转换器处理）：**

```python
def save(...):
    def perform_save() -> None:
        spec = chart.to_dict(context={"pre_transform": False})
        # ... 后续处理
    
    if using_vegafusion():
        # VegaFusion 模式
        with data_transformers.disable_max_rows():
            perform_save()
    else:
        # 保存时强制使用 default 转换器（内联数据）
        # 因为 vl-convert 无法访问本地 JSON/CSV 文件
        with data_transformers.enable("default"), data_transformers.disable_max_rows():
            perform_save()
```

#### 3.3.3 场景三：使用 JSON 数据转换器（外部文件）

```python
import altair as alt

# 启用 json 数据转换器
alt.data_transformers.enable("json")

chart = alt.Chart(df).mark_point().encode(x="x", y="y")
chart.save("chart.html")
```

**数据转换流程：**

| 步骤 | 操作 | 结果 |
|-----|------|------|
| 1 | `_prepare_data()` 调用 `data_transformers.get()` | 获取 `to_json` 函数 |
| 2 | `to_json(df)` 执行 | - 计算内容 hash<br>- 写入 `altair-data-<hash>.json`<br>- 返回 `{"url": "altair-data-<hash>.json", "format": {"type": "json"}}` |
| 3 | spec 中的 data 字段 | `{"url": "altair-data-xxx.json", "format": {"type": "json"}}` |
| 4 | HTML 输出 | Vega-Lite 运行时从 URL 加载数据 |

**注意限制：**
- `save("chart.png")` 时会**强制切换回 default 转换器**，因为 vl-convert 无法访问本地文件
- 仅在 Jupyter 显示或保存 HTML 时，外部 URL 才有效

### 3.4 关键协作点总结

#### 3.4.1 解耦设计

两层通过 **Vega-Lite spec 字典** 完全解耦：

```
┌─────────────────┐         ┌─────────────────┐
│   数据适配层     │  spec   │   渲染后端层     │
│                 │ ──────▶ │                 │
│  - 类型判断      │  dict   │  - 渲染器选择    │
│  - 数据转换      │         │  - 格式分发      │
│  - 数据合并      │         │  - 模板渲染      │
└─────────────────┘         └─────────────────┘
```

**优势：**
- 数据层不关心最终输出格式
- 渲染层不关心数据来源
- 可以独立扩展数据转换器和渲染器

#### 3.4.2 关键 API 接口

| 接口 | 所属层级 | 作用 |
|-----|---------|------|
| `data_transformers` | 数据适配层 | 全局数据转换器注册表 |
| `data_transformers.enable()` | 数据适配层 | 切换数据转换器 |
| `_prepare_data()` | 数据适配层 | 数据准备入口 |
| `renderers` | 渲染后端层 | 全局渲染器注册表 |
| `renderers.enable()` | 渲染后端层 | 切换渲染器 |
| `to_dict()` | 两层边界 | 生成 Vega-Lite spec（数据层输出） |
| `_repr_mimebundle_()` | 两层边界 | Jupyter 显示入口（调用渲染层） |
| `spec_to_mimebundle()` | 渲染后端层 | 格式分发核心 |

#### 3.4.3 上下文传递机制

`context` 字典在 `to_dict()` 中传递关键信息：

```python
context = {
    "data": original_data,           # 原始数据引用（用于类型推断）
    "datasets": {},                  # 合并后的数据集存储
    "top_level": True,               # 是否为顶层 spec
    "pre_transform": False,          # 是否预计算转换（VegaFusion）
}
```

**数据流中的 context 使用：**
1. `to_dict(context={"pre_transform": False})` 初始化
2. `_prepare_data(data, context)` 传递给数据准备
3. `_consolidate_data(data, context)` 合并数据到 `context["datasets"]`
4. 顶层 spec 合并 `context["datasets"]` 到 `spec["datasets"]`

---

## 四、扩展机制与架构设计

### 4.1 PluginRegistry 通用设计

数据转换器和渲染器都基于 `PluginRegistry` 实现，位于 `altair/utils/plugin_registry.py:83-280`：

```python
class PluginRegistry(Generic[PluginT, R]):
    def __init__(
        self, entry_point_group: str = "", plugin_type: IsPlugin = callable
    ) -> None:
        self.entry_point_group = entry_point_group
        self.plugin_type = plugin_type
        self._active: Plugin[R] | None = None
        self._active_name: str = ""
        self._plugins: dict[str, PluginT] = {}
        self._options: dict[str, Any] = {}
        self._global_settings: dict[str, Any] = self.__class__._global_settings.copy()
```

**核心能力：**

| 能力 | 方法/属性 | 说明 |
|-----|----------|------|
| 显式注册 | `register(name, value)` | 手动注册插件 |
| 自动发现 | `names()` → `entry_points()` | 从 setuptools entry points 加载 |
| 激活切换 | `enable(name, **options)` | 激活指定插件，返回上下文管理器 |
| 状态管理 | `_get_state()` / `_set_state()` | 保存/恢复状态（用于上下文管理器） |
| 获取实例 | `get()` | 获取当前激活的插件（带 options） |

### 4.2 Entry Points 自动发现

通过 setuptools entry points 实现插件自动发现：

**数据转换器入口点：**
- 组名：`altair.vegalite.v6.data_transformer`

**渲染器入口点：**
- 组名：`altair.vegalite.v6.renderer`

**外部包注册示例（setup.py）：**
```python
setup(
    name="my-altair-plugin",
    entry_points={
        "altair.vegalite.v6.renderer": [
            "my_renderer = my_altair_plugin:my_renderer_func",
        ],
        "altair.vegalite.v6.data_transformer": [
            "my_transformer = my_altair_plugin:my_transformer_func",
        ],
    }
)
```

**自动发现代码**（`PluginRegistry.names()`）：
```python
def names(self) -> list[str]:
    exts = list(self._plugins.keys())
    e_points = importlib_metadata_get(self.entry_point_group)
    more_exts = [ep.name for ep in e_points]
    exts.extend(more_exts)
    return sorted(set(exts))
```

### 4.3 典型扩展场景

#### 4.3.1 自定义数据转换器

```python
import altair as alt
from altair.utils.data import DataType, ToValuesReturnType

def parquet_transformer(data: DataType) -> dict:
    """将数据保存为 Parquet 文件并返回 URL 引用"""
    import pandas as pd
    import hashlib
    
    if isinstance(data, pd.DataFrame):
        # 计算 hash
        data_hash = hashlib.sha256(
            pd.util.hash_pandas_object(data).values
        ).hexdigest()[:16]
        
        filename = f"data-{data_hash}.parquet"
        data.to_parquet(filename)
        
        return {
            "url": filename,
            "format": {"type": "parquet"}  # Vega-Lite 需要支持此格式
        }
    else:
        raise ValueError("Only pandas DataFrame supported")

# 注册自定义转换器
alt.data_transformers.register("parquet", parquet_transformer)

# 使用
alt.data_transformers.enable("parquet")
```

#### 4.3.2 自定义渲染器

```python
import altair as alt
from altair.utils.mimebundle import spec_to_mimebundle

def markdown_renderer(spec: dict, **metadata):
    """将图表渲染为 Markdown 代码块格式"""
    import json
    
    spec_json = json.dumps(spec, indent=2)
    
    return {
        "text/markdown": f"""## Vega-Lite 图表

```json
{spec_json}
```

使用 Vega-Embed 渲染此图表。
""",
        "text/plain": f"<VegaLite spec with {len(spec.get('data', {}).get('values', []))} rows>"
    }

# 注册自定义渲染器
alt.renderers.register("markdown", markdown_renderer)

# 使用
alt.renderers.enable("markdown")
```

### 4.4 架构设计亮点

#### 4.4.1 关注点分离

| 层级 | 职责 | 不关心的事情 |
|-----|------|-------------|
| 数据适配层 | 数据类型判断、转换、合并 | 最终输出格式（HTML/PNG/JSON） |
| 渲染后端层 | 格式分发、模板渲染、引擎调用 | 数据来源（DataFrame/CSV/URL） |

#### 4.4.2 基于协议的类型系统

使用 Python Protocol 实现鸭子类型：

```python
@runtime_checkable
class SupportsGeoInterface(Protocol):
    __geo_interface__: MutableMapping

@runtime_checkable
class DataFrameLike(Protocol):
    def __dataframe__(
        self, nan_as_null: bool = False, allow_copy: bool = True
    ) -> DfiDataFrame: ...
```

**优势：**
- 无需继承特定基类
- 任何实现对应方法/属性的对象都可使用
- 支持 GeoPandas、Ibis 等第三方库无需修改 Altair 代码

#### 4.4.3 上下文管理器模式

使用 `PluginEnabler` 实现安全的临时切换：

```python
# 临时切换，自动恢复
with alt.renderers.enable("png"):
    chart.save("temp.png")

# 恢复到之前的渲染器
chart.display()
```

**关键实现：**
```python
class PluginEnabler(Generic[PluginT, R]):
    def __init__(self, registry, name, **options):
        self.original_state = registry._get_state()  # 保存
        self.registry._enable(name, **options)        # 修改

    def __exit__(self, typ, value, traceback):
        self.registry._set_state(self.original_state)  # 恢复
```

#### 4.4.4 Narwhals 抽象层

使用 Narwhals 库统一多种 DataFrame 实现：

```python
import narwhals.stable.v1 as nw
from narwhals.stable.v1.typing import IntoDataFrame

# 统一接口
data_nw = nw.from_native(data, eager_or_interchange_only=True)

# 统一操作
data_nw = sanitize_narwhals_dataframe(data_nw)
values = data_nw.rows(named=True)
```

**支持的 DataFrame 库：**
- pandas
- Polars
- PyArrow
- cuDF (GPU)
- Modin
- 任何实现 DataFrame Interchange Protocol 的库

---

## 五、关键代码位置索引

| 功能模块 | 文件路径 | 关键类/函数 |
|---------|---------|------------|
| 数据类型判断 | `altair/utils/data.py` | `is_data_type`, `DataType` |
| 数据转换器注册表 | `altair/utils/data.py` | `DataTransformerRegistry` |
| 默认数据转换器 | `altair/vegalite/data.py` | `default_data_transformer` |
| to_values 转换 | `altair/utils/data.py` | `to_values`, `to_json`, `to_csv` |
| 数据清理 | `altair/utils/core.py` | `sanitize_pandas_dataframe`, `sanitize_narwhals_dataframe`, `sanitize_geo_interface` |
| 渲染器注册表 | `altair/utils/display.py` | `RendererRegistry` |
| 渲染器实现 | `altair/vegalite/v6/display.py` | `renderers`, `mimetype_renderer`, `html_renderer` |
| MIME 分发 | `altair/utils/mimebundle.py` | `spec_to_mimebundle`, `_spec_to_mimebundle_with_engine` |
| HTML 模板 | `altair/utils/html.py` | `spec_to_html`, `TEMPLATES` |
| 图表 to_dict | `altair/vegalite/v6/api.py` | `TopLevelMixin.to_dict`, `_prepare_data`, `_consolidate_data` |
| Jupyter 显示 | `altair/vegalite/v6/api.py` | `_repr_mimebundle_`, `display`, `show` |
| 保存文件 | `altair/utils/save.py` | `save`, `_save_mimebundle_format` |
| 插件基类 | `altair/utils/plugin_registry.py` | `PluginRegistry`, `PluginEnabler` |
