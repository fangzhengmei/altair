# Altair 数据转换器决策链路分析报告（修订版）

## 1. 概述

本报告详细梳理 Altair 数据转换器在大数据集场景下的完整决策链路，包括：
- 四种内置转换器（default、json、csv、vegafusion）的触发条件
- 行数限制机制的差异（修正前一版的错误理解）
- save 操作时的强制内嵌触发条件
- 上下文标记（context）的传递机制

## 2. 核心决策流程图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      图表序列化入口点                                      │
│                                                                          │
│   触发方式：                                                              │
│   ├── chart.to_dict()           # 转换为字典                             │
│   ├── chart.to_json()          # 转换为 JSON 字符串                      │
│   ├── chart.to_html()          # 转换为 HTML 页面                        │
│   ├── chart.save()             # 保存为文件（特殊处理）                   │
│   └── Jupyter _repr_mimebundle_()  #  Notebook 显示                      │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    TopLevelMixin.to_dict() 主流程                         │
│                    (altair/vegalite/v6/api.py:2044)                      │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
            ┌───────────────────────┼───────────────────────┐
            ▼                       ▼                       ▼
┌───────────────────┐   ┌───────────────────┐   ┌───────────────────┐
│ 步骤1: 准备 context│   │ 步骤2: 数据准备    │   │ 步骤3: 递归转换    │
│                   │   │                   │   │                   │
│ context = {       │   │ _prepare_data()  │   │ super().to_dict() │
│   "datasets": {}, │   │ 调用数据转换器    │   │ 传入:             │
│   "top_level": T, │   │                   │   │ context=dict(     │
│ }                 │   │                   │   │   context,         │
│                   │   │                   │   │   pre_transform=F  │
│                   │   │                   │   │ )                  │
└───────────────────┘   └───────────────────┘   └───────────────────┘
            │                       │                       │
            └───────────────────────┼───────────────────────┘
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 步骤4: 顶层后处理（仅当 is_top_level=True 时）                             │
│                                                                          │
│ ┌─────────────────────────────────────────────────────────────────────┐ │
│ │ 4.1 添加 $schema 键                                                  │ │
│ │ 4.2 应用主题 (theme)                                                 │ │
│ │ 4.3 合并数据集: if context["datasets"]:                             │ │
│ │         spec.setdefault("datasets", {}).update(context["datasets"])│ │
│ └─────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 步骤5: 格式分支与 VegaFusion 预转换                                        │
│                                                                          │
│ 判断条件:                                                                │
│   pre_transform = context.get("pre_transform", True)                    │
│   using_vegafusion = (data_transformers.active == "vegafusion")        │
│                                                                          │
│ ┌─────────────────────────────────────────────────────────────────────┐ │
│ │ 分支 A: pre_transform=True AND using_vegafusion=True                │ │
│ │ ─────────────────────────────────────────────────────────────────── │ │
│ │ if format == "vega-lite":                                            │ │
│ │     raise ValueError("必须使用 format='vega'")                       │ │
│ │ else:                                                                 │ │
│ │     return _compile_with_vegafusion(vegalite_spec)  ← 预转换       │ │
│ └─────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│ ┌─────────────────────────────────────────────────────────────────────┐ │
│ │ 分支 B: format == "vega" (非 vegafusion 或 pre_transform=False)    │ │
│ │ ─────────────────────────────────────────────────────────────────── │ │
│ │ plugin = vegalite_compilers.get()  # vl-convert                     │ │
│ │ return plugin(vegalite_spec)                                        │ │
│ └─────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│ ┌─────────────────────────────────────────────────────────────────────┐ │
│ │ 分支 C: 其他情况 (format == "vega-lite")                            │ │
│ │ ─────────────────────────────────────────────────────────────────── │ │
│ │ return vegalite_spec  # 直接返回 Vega-Lite spec                      │ │
│ └─────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

## 3. 数据转换器调用机制

### 3.1 核心调用点

**位置**: `altair/vegalite/v6/api.py:266-302`

```python
def _prepare_data(data: ChartDataType, context: dict | None = None):
    if data is Undefined:
        return data
    
    # 关键点：只对非字典类型的数据应用转换器
    elif not isinstance(data, dict) and _is_data_type(data):
        if func := data_transformers.get():
            # narwhals 统一处理各种 DataFrame 类型
            data = func(nw.to_native(data, pass_through=True))
    
    # 字符串转换为 URL 数据（用户手动指定的 URL）
    elif isinstance(data, str):
        data = core.UrlData(data)
    
    # 数据集合并（如果启用）
    if context is not None and data_transformers.consolidate_datasets:
        data = _consolidate_data(data, context)
    
    return data
```

### 3.2 PluginRegistry.get() 的工作原理

**位置**: `altair/utils/plugin_registry.py:262-276`

```python
def get(self) -> partial[R] | Plugin[R] | None:
    """Return the currently active plugin."""
    if (func := self._active) and self.plugin_type(func):
        # 关键：如果有 options，返回 partial；否则直接返回函数
        return partial(func, **self._options) if self._options else func
    ...
```

**行为说明**:
| 用户调用 | `_options` | `get()` 返回 |
|---------|-----------|-------------|
| `enable('default')` | `{}` | `default_data_transformer` 函数本身 |
| `enable('default', max_rows=10000)` | `{'max_rows': 10000}` | `partial(default_data_transformer, max_rows=10000)` |

## 4. 四种转换器详细对比

### 4.1 default 转换器（内嵌策略）

**实现位置**: `altair/vegalite/data.py:32-44`

```python
def default_data_transformer(
    data: DataType | None = None, max_rows: int = 5000
):
    if data is None:
        # 返回一个闭包，max_rows=5000 被硬编码在闭包中
        def pipe(data: DataType, /) -> ToValuesReturnType:
            data = limit_rows(data, max_rows=max_rows)
            return to_values(data)
        return pipe
    else:
        return to_values(limit_rows(data, max_rows=max_rows))
```

#### 行数限制机制

**位置**: `altair/utils/data.py:135-165`

```python
def limit_rows(data: DataType | None = None, max_rows: int | None = 5000):
    if data is None:
        return partial(limit_rows, max_rows=max_rows)
    
    # 根据数据类型提取 values 以计算行数
    if isinstance(data, SupportsGeoInterface):
        if data.__geo_interface__["type"] == "FeatureCollection":
            values = data.__geo_interface__["features"]
        else:
            values = data.__geo_interface__
    elif isinstance(data, dict):
        if "values" in data:
            values = data["values"]
        else:
            return data  # 无 values 的 dict 跳过检查
    else:
        data = to_eager_narwhals_dataframe(data)
        values = data
    
    n = len(values)
    # 关键点：检查原始数据行数
    if max_rows is not None and n > max_rows:
        raise MaxRowsError.from_limit_rows(n, max_rows)
    
    return data
```

#### default 转换器关键属性

| 属性 | 值 | 说明 |
|------|-----|------|
| **默认行数限制** | 5000 行 | 来自函数默认参数 `max_rows: int = 5000` |
| **限制可覆盖** | 是 | `enable('default', max_rows=10000)` |
| **检查时机** | 数据转换前 | 在 `limit_rows()` 中检查 |
| **检查对象** | 原始数据行数 | `len(values)` 即 DataFrame 行数 |
| **超限处理** | 直接抛出 `MaxRowsError` | 不进行任何转换 |

#### 实际触发场景

```python
import altair as alt
import pandas as pd

# 场景1: 超过默认限制，触发 MaxRowsError
data = pd.DataFrame({"x": range(6000)})  # 6000 > 5000
alt.Chart(data).mark_point()
# 抛出: MaxRowsError: The number of rows in your dataset (6000) 
#        is greater than the maximum allowed (5000).

# 场景2: 覆盖默认限制
alt.data_transformers.enable('default', max_rows=10000)
alt.Chart(data).mark_point()  # 正常工作

# 场景3: 禁用限制
alt.data_transformers.disable_max_rows()
# 等价于: enable(..., max_rows=None)
```

---

### 4.2 json / csv 转换器（文件策略）

**实现位置**: `altair/utils/data.py:226-295`

#### 核心工作流程

```
输入: DataFrame
    │
    ▼
_data_to_json_string(data) 或 _data_to_csv_string(data)
    │
    ├── Pandas: df.to_json(orient='records') 或 df.to_csv()
    ├── Dict: json.dumps(data['values'])
    └── Narwhals: json.dumps(df.rows(named=True)) 或 df.write_csv()
    │
    ▼
_compute_data_hash(json_str)
    │
    └── hashlib.sha256(data.encode()).hexdigest()[:32]
    │
    ▼
写入文件: {prefix}-{hash}.{extension}
    │
    ▼
返回: {"url": "altair-data-xxx.json", "format": {"type": "json"}}
```

#### json/csv 转换器关键属性

| 属性 | 值 | 说明 |
|------|-----|------|
| **行数限制** | **无限制** | 不调用 `limit_rows()` |
| **数据位置** | 本地文件系统 | 当前工作目录下 |
| **文件名生成** | 内容哈希 | 相同内容生成相同文件名 |
| **返回形式** | URL 引用 | `{"url": "...", "format": {...}}` |

#### ⚠️ 重要限制：save 时会被强制切换

```python
# 当用户启用 json 转换器时
alt.data_transformers.enable('json')

# 正常显示/转换时：工作正常，数据写入文件
chart = alt.Chart(data).mark_point()
chart.to_dict()  # 返回 {"url": "altair-data-xxx.json", ...}

# 但 save() 时：
chart.save('chart.png')
# ↑ 会强制切换回 default 转换器！
# 原因：vl-convert 无法访问本地文件系统中的 JSON 文件
```

---

### 4.3 vegafusion 转换器（服务端策略）

**实现位置**: `altair/utils/_vegafusion_data.py:90-105`

#### ⚠️ 重要修正：关于行数限制

**前一版错误理解**:
> vegafusion 默认限制 100,000 行（转换后）

**实际情况**:

```python
# 函数签名中的 max_rows=100000
def vegafusion_data_transformer(
    data: DataType | None = None, max_rows: int = 100000  # ← 这个默认值实际上未被使用！
):
    if data is None:
        return vegafusion_data_transformer  # ← 关键：只是返回自身，没有绑定参数！
    
    # 转换器内部不检查行数！
    if is_supported_by_vf(data) and not isinstance(data, SupportsGeoInterface):
        table_name = f"table_{uuid.uuid4()}".replace("-", "_")
        extracted_inline_tables[table_name] = data  # 存储到 WeakValueDictionary
        return {"url": VEGAFUSION_PREFIX + table_name}  # 特殊 URL
    else:
        # GeoInterface 或不支持的类型：回退到 default
        return default_data_transformer(data)
```

**真正的行数限制检查位置** (`altair/utils/_vegafusion_data.py:235-283`):

```python
def compile_with_vegafusion(vegalite_spec: dict[str, Any]) -> dict[str, Any]:
    # ... 省略编译到 Vega 的步骤 ...
    
    # 关键点：从 options 获取，而不是从函数默认参数获取！
    row_limit = data_transformers.options.get("max_rows", None)
    #                                      ↑
    #                           如果 options 为空，返回 None！
    
    # 调用 VegaFusion 运行时
    transformed_vega_spec, warnings = vf.runtime.pre_transform_spec(
        vega_spec,
        vf.get_local_tz(),
        inline_datasets=inline_tables,
        row_limit=row_limit,  # 传入 options 中的值
    )
    
    # 检查警告
    handle_row_limit_exceeded(row_limit, warnings)
    
    return transformed_vega_spec
```

**警告处理函数**:

```python
def handle_row_limit_exceeded(row_limit: int | None, warnings: list):
    for warning in warnings:
        if warning.get("type") == "RowLimitExceeded":
            # 只有当 VegaFusion 运行时返回此警告才抛出
            # 如果 row_limit = None，VegaFusion 不会返回此警告
            msg = (
                "The number of dataset rows after filtering and aggregation exceeds\n"
                f"the current limit of {row_limit}..."
            )
            raise MaxRowsError(msg)
```

#### vegafusion 转换器关键属性（修正后）

| 属性 | 实际值 | 说明 |
|------|--------|------|
| **默认行数限制** | **无限制** | `options.get("max_rows", None)` 返回 `None` |
| **函数签名中的 100000** | **未使用** | 只存在于签名，实际限制来自 `options` |
| **检查时机** | 预转换后 | `vf.runtime.pre_transform_spec()` 返回警告时 |
| **检查对象** | **转换后的数据** | 聚合、过滤、选择后的结果 |
| **限制覆盖方式** | `enable('vegafusion', max_rows=xxx)` | 显式传入才会生效 |
| **GeoDataFrame 处理** | 回退到 default | VegaFusion 不直接支持地理数据 |

#### 实际触发场景

```python
import altair as alt
import pandas as pd

# 准备 100,000 行数据
data = pd.DataFrame({"x": range(100000), "y": range(100000)})

# 场景1: 启用 vegafusion（不传参数）
alt.data_transformers.enable('vegafusion')
# 此时 data_transformers.options = {}
# row_limit = options.get("max_rows", None) = None

# 转换：正常工作，无行数限制！
chart = alt.Chart(data).mark_point()
spec = chart.to_dict(format="vega")
# ↑ 没有抛出 MaxRowsError

# 场景2: 显式设置 max_rows
alt.data_transformers.enable('vegafusion', max_rows=50000)
# 此时 data_transformers.options = {'max_rows': 50000}

# 如果转换后超过 50000 行，抛出 MaxRowsError
# 注意：是转换后的行数，不是原始行数！

# 场景3: 聚合场景（推荐用法）
chart = alt.Chart(large_data).mark_bar().encode(
    x=alt.X('category:N'),
    y='count()'
)
# 即使原始数据有 1000 万行，聚合后可能只有 10 行
# 永远不会触发行限制
```

#### VegaFusion 完整流程图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    VegaFusion 转换器两阶段流程                            │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
            ┌───────────────────────┴───────────────────────┐
            ▼                                               ▼
┌───────────────────────────┐               ┌───────────────────────────┐
│ Phase 1: 数据准备阶段      │               │ Phase 2: 预转换阶段        │
│ (to_dict() 前半部分)       │               │ (仅顶层调用且 pre_transform │
│                           │               │  = True 时触发)           │
├───────────────────────────┤               ├───────────────────────────┤
│ 1. vegafusion_data_       │               │ 1. 判断条件:               │
│    transformer(data)      │               │    context.get(            │
│                           │               │      "pre_transform", True) │
│ 2. 生成 UUID 表名          │               │    AND using_vegafusion()  │
│                           │               │                           │
│ 3. 存储到 WeakValue-      │               │ 2. 从 options 获取 row_limit│
│    Dictionary:            │               │    row_limit = options.get( │
│    extracted_inline_      │               │      "max_rows", None)      │
│    tables[table_name]     │               │                           │
│    = df                   │               │ 3. 编译 Vega-Lite → Vega    │
│                           │               │    (vl-convert)             │
│ 4. 返回特殊 URL:          │               │                           │
│    {"url": "vegafusion+  │               │ 4. 提取内联表:              │
│     dataset://table_xxx"} │               │    get_inline_tables()     │
│                           │               │    从 WeakValueDictionary   │
│                           │               │    中 pop 取出              │
│                           │               │                           │
│                           │               │ 5. 调用 VegaFusion:        │
│                           │               │    vf.runtime.             │
│                           │               │    pre_transform_spec(      │
│                           │               │      vega_spec,             │
│                           │               │      inline_datasets=...,   │
│                           │               │      row_limit=row_limit    │
│                           │               │    )                         │
│                           │               │                           │
│                           │               │ 6. 检查警告:                │
│                           │               │    handle_row_limit_        │
│                           │               │    exceeded(row_limit,       │
│                           │               │    warnings)                 │
│                           │               │                           │
│                           │               │ 7. 返回转换后的 Vega spec   │
│                           │               │    其中 data 已替换为:      │
│                           │               │    {"values": [...]}        │
│                           │               │    (聚合后的结果数据)         │
└───────────────────────────┘               └───────────────────────────┘
```

---

## 5. save() 操作的特殊处理

**位置**: `altair/utils/save.py:271-284`

### 5.1 决策逻辑

```python
def save(chart, fp, ...):
    def perform_save() -> None:
        # 关键点：pre_transform=False
        spec = chart.to_dict(context={"pre_transform": False})
        # ... 后续保存逻辑
    
    if using_vegafusion():
        # 分支 A: vegafusion 已启用
        # - 不切换转换器，仍使用 vegafusion
        # - 但 pre_transform=False 跳过预转换
        # - 仅禁用 max_rows 检查
        with data_transformers.disable_max_rows():
            perform_save()
    else:
        # 分支 B: 非 vegafusion（json、csv、default 等）
        # - 强制切换到 default 转换器
        # - 禁用 max_rows 检查
        with data_transformers.enable("default"), \
             data_transformers.disable_max_rows():
            perform_save()
```

### 5.2 为什么强制切换？

| 转换器 | save 时行为 | 原因 |
|--------|------------|------|
| `vegafusion` | 保持不变，但 `pre_transform=False` | VegaFusion 数据存储在 Python 内存中，可通过 `inline_datasets` 传递给 vl-convert |
| `json` / `csv` | **强制切换到 default** | vl-convert 运行在独立进程中，无法访问 Python 脚本写入的本地文件 |
| `default` | 保持不变 | 数据内联在 spec 中，无外部依赖 |

### 5.3 pre_transform=False 的影响

当 `save()` 中调用 `to_dict(context={"pre_transform": False})` 时：

```python
# 在 TopLevelMixin.to_dict() 中
if context.get("pre_transform", True) and _using_vegafusion():
    # 因为 context["pre_transform"] = False，这个条件为 False
    # 不会调用 _compile_with_vegafusion()
    ...

# 结果：
# - 如果 format="vega"，使用 vl-convert 编译
# - 但 VegaFusion 的数据通过 inline_datasets 传递给 vl-convert
# - 不会在 Python 中执行预转换
```

### 5.4 实际场景示例

```python
import altair as alt
import pandas as pd

data = pd.DataFrame({"x": range(10000), "y": range(10000)})
chart = alt.Chart(data).mark_point()

# 场景1: 使用 json 转换器
alt.data_transformers.enable('json')

# to_dict() 正常返回 URL 引用
spec = chart.to_dict()
# {"data": {"url": "altair-data-xxx.json", "format": {"type": "json"}}, ...}

# 但 save() 时：
chart.save('chart.html')
# ↑ 内部切换到 default，数据内联

chart.save('chart.png')
# ↑ 同上，vl-convert 需要内联数据

# 场景2: 使用 vegafusion 转换器
alt.data_transformers.enable('vegafusion')

# to_dict(format="vega") 会执行预转换
spec = chart.to_dict(format="vega")
# ↑ 调用 _compile_with_vegafusion()

# 但 save() 时：
chart.save('chart.png')
# ↑ 调用 to_dict(context={"pre_transform": False})
# ↑ 不执行预转换，但数据通过 inline_datasets 传递给 vl-convert
```

---

## 6. 上下文标记（context）传递机制

### 6.1 核心标记说明

| 标记 | 类型 | 默认值 | 设置位置 | 作用 |
|------|------|--------|---------|------|
| `context["datasets"]` | dict | `{}` | `to_dict()` 开头 | 存储合并后的命名数据集 |
| `context["top_level"]` | bool | `True` | `to_dict()` 开头，递归前设为 `False` | 标记是否为顶层调用 |
| `context["data"]` | Any | 未设置 | `_prepare_data()` 后 | 引用原始数据用于列类型推断 |
| `context["pre_transform"]` | bool | `True` (通过 `get` 默认) | 递归调用时显式设为 `False`；`save()` 显式设为 `False` | 控制是否执行 VegaFusion 预转换 |

### 6.2 传递流程详解

```
用户调用: chart.to_dict(format="vega")
           context 参数: None（用户未传入）
              │
              ▼
┌─────────────────────────────────────────────────────────────────┐
│ TopLevelMixin.to_dict() 内部处理                                 │
├─────────────────────────────────────────────────────────────────┤
│ 1. 初始化 context                                                 │
│    context = context.copy() if context else {}                   │
│    → context = {}                                                │
│                                                                  │
│ 2. 设置默认值                                                    │
│    context.setdefault("datasets", {})                            │
│    → context = {"datasets": {}}                                  │
│                                                                  │
│ 3. 获取 top_level 标志                                           │
│    is_top_level = context.get("top_level", True)                 │
│    → True（因为 context 中没有这个键）                           │
└─────────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────┐
│ 调用 _prepare_data(data, context)                                │
├─────────────────────────────────────────────────────────────────┤
│ - 数据转换器被调用                                                │
│ - 如果 consolidate_datasets=True:                                │
│   - 数据被移动到 context["datasets"]                             │
│   - 返回 NamedData(name=...) 引用                                │
│                                                                  │
│ 最后设置:                                                         │
│ context["data"] = data  # 保存原始数据引用                       │
└─────────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────┐
│ 标记非顶层调用                                                    │
├─────────────────────────────────────────────────────────────────┤
│ context["top_level"] = False                                     │
│                                                                  │
│ 现在 context = {                                                  │
│     "datasets": {..., ...},  # 可能已填充                       │
│     "top_level": False,                                          │
│     "data": original_df                                          │
│ }                                                                 │
└─────────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────┐
│ 递归调用父类 to_dict()                                            │
├─────────────────────────────────────────────────────────────────┤
│ vegalite_spec = _top_schema_base(super(...)).to_dict(           │
│     validate=...,                                                 │
│     context=dict(context, pre_transform=False)  ← 关键！        │
│ )                                                                 │
│                                                                  │
│ 传入的 context 是一个新字典:                                      │
│ {                                                                 │
│     "datasets": {..., ...},                                      │
│     "top_level": False,                                          │
│     "data": original_df,                                         │
│     "pre_transform": False  ← 新增                               │
│ }                                                                 │
│                                                                  │
│ 嵌套的 to_dict() 调用会看到 pre_transform=False，                │
│ 因此不会触发 VegaFusion 预转换                                   │
└─────────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────┐
│ 回到顶层，执行后处理                                              │
├─────────────────────────────────────────────────────────────────┤
│ 检查条件 (使用的是原始 context，不是递归调用的那个！):            │
│                                                                  │
│ if context.get("pre_transform", True) and _using_vegafusion(): │
│     ↑                                                            │
│     context 中没有 "pre_transform" 键！                          │
│     所以返回默认值 True                                           │
│                                                                  │
│ 如果条件为 True:                                                  │
│     return _compile_with_vegafusion(vegalite_spec)              │
└─────────────────────────────────────────────────────────────────┘
```

### 6.3 save() 中的显式覆盖

```python
# 在 save() 中
spec = chart.to_dict(context={"pre_transform": False})

# 此时 TopLevelMixin.to_dict() 中的 context：
# context = {"pre_transform": False} （用户传入的）
# context.setdefault("datasets", {}) → 变成 {"pre_transform": False, "datasets": {}}

# 顶层检查:
# context.get("pre_transform", True) → False（因为 context 中有这个键）
# 所以即使 using_vegafusion() 为 True，也不会调用 _compile_with_vegafusion()
```

---

## 7. 完整决策树

### 7.1 转换器选择决策树

```
                        ┌───────────────────────────┐
                        │ 用户是否显式启用转换器？    │
                        └───────────────────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
    ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
    │ enable('json')  │   │ enable('csv')   │   │ enable('vegafus │
    │                 │   │                 │   │ ion')           │
    └─────────────────┘   └─────────────────┘   └─────────────────┘
              │                     │                     │
              └─────────────────────┼─────────────────────┘
                                    ▼
                        ┌───────────────────────────┐
                        │ 当前操作是否为 save()？    │
                        └───────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
            ┌───────────────┐               ┌───────────────┐
            │     Yes       │               │      No       │
            │  (save 操作)  │               │ (to_dict 等)  │
            └───────────────┘               └───────────────┘
                    │                               │
                    ▼                               ▼
    ┌───────────────────────────────┐   ┌───────────────────────────────┐
    │ 进一步判断:                    │   │ 使用用户选择的转换器:          │
    │ using_vegafusion()?           │   │                               │
    └───────────────────────────────┘   │ - json: 写入文件，返回 URL    │
                    │                   │ - csv: 写入文件，返回 URL     │
        ┌───────────┴───────────┐       │ - vegafusion: 存储到内存，   │
        ▼                       ▼       │              返回特殊 URL    │
┌───────────────┐       ┌───────────────┐ └───────────────────────────────┘
│     True      │       │     False     │
│  (vegafusion) │       │  (json/csv)  │
└───────────────┘       └───────────────┘
        │                       │
        ▼                       ▼
┌───────────────┐       ┌───────────────┐
│ 保持 vegafusion│       │ 强制切换到    │
│ 禁用 max_rows │       │ default      │
│ pre_transform │       │ 禁用 max_rows│
│ = False       │       │               │
└───────────────┘       └───────────────┘
```

### 7.2 行数限制决策树

```
                    ┌─────────────────────────────┐
                    │ 当前激活的转换器是哪一个？    │
                    └─────────────────────────────┘
                                    │
        ┌───────────┬───────────────┼───────────────┬───────────┐
        ▼           ▼               ▼               ▼           ▼
   ┌─────────┐ ┌─────────┐   ┌─────────┐   ┌─────────┐ ┌─────────┐
   │ default │ │  json   │   │  csv    │   │vegafusion│ │ 其他    │
   └─────────┘ └─────────┘   └─────────┘   └─────────┘ └─────────┘
        │           │               │               │
        ▼           ▼               ▼               ▼
┌───────────────┐ ┌───────────────────────────┐ ┌───────────────────┐
│ 调用 limit_rows│ │     无行数限制检查        │ │ 分阶段检查:       │
│               │ │                           │ │                   │
│ 检查:         │ │                           │ │ Phase 1 (转换器): │
│ len(原始数据) │ │                           │ │ 无检查            │
│ > max_rows?   │ │                           │ │                   │
│               │ │                           │ │ Phase 2 (预转换): │
│ 默认 max_rows │ │                           │ │ 检查 options 中    │
│ = 5000        │ │                           │ │ 是否有 max_rows    │
│               │ │                           │ │                   │
│ 可通过:       │ │                           │ │ options.get(      │
│ enable('defaul│ │                           │ │   "max_rows",     │
│ t', max_rows=X│ │                           │ │   None)           │
│ ) 覆盖        │ │                           │ │                   │
│               │ │                           │ │ 默认: None        │
│ 或 disable_max│ │                           │ │ → 无限制！         │
│ _rows() 禁用  │ │                           │ │                   │
│               │ │                           │ │ 只有显式传入:     │
│               │ │                           │ │ enable('vegafusion│
│               │ │                           │ │ ', max_rows=X)    │
│               │ │                           │ │ 才会有实际限制     │
│               │ │                           │ │                   │
│               │ │                           │ │ 检查对象:          │
│               │ │                           │ │ 转换后的数据行数   │
│               │ │                           │ │ (聚合/过滤后)      │
└───────────────┘ └───────────────────────────┘ └───────────────────┘
```

---

## 8. 关键修正总结

| 项目 | 前一版理解 | 实际情况（修正后） |
|------|-----------|-------------------|
| **vegafusion 默认行数限制** | 100,000 行 | **无限制**（`options.get("max_rows", None)` 返回 `None`） |
| **vegafusion 函数签名中的 100000** | 是实际限制值 | **未使用**（只是函数默认参数，实际限制来自 `options`） |
| **vegafusion 检查时机** | 不明确 | 分两阶段：Phase 1（转换器）无检查；Phase 2（预转换）检查 `options` |
| **vegafusion 检查对象** | 不明确 | **转换后的数据行数**（聚合、过滤后） |
| **default 检查对象** | - | **原始数据行数** |
| **save 时 vegafusion 行为** | - | `pre_transform=False` 跳过预转换，但数据通过 `inline_datasets` 传递 |

## 9. 最佳实践建议

### 9.1 数据集大小与转换器选择

| 数据规模 | 推荐转换器 | 原因 |
|---------|-----------|------|
| < 5,000 行 | `default`（默认） | 最简单，数据内联，无外部依赖 |
| 5,000 - 100,000 行 | `json` 或 `vegafusion` | `json` 减少 notebook 体积；`vegafusion` 支持聚合 |
| > 100,000 行 | `vegafusion` + 聚合 | 利用服务端预计算减少传输数据量 |
| 地理数据（GeoDataFrame） | `default` 或 `json` | VegaFusion 不直接支持，会回退到 default |

### 9.2 常见问题解决方案

| 问题 | 解决方案 |
|------|---------|
| `MaxRowsError`（default 转换器） | 1. `enable('vegafusion')`（推荐，聚合后通常很小）<br>2. `enable('json')` 或 `enable('csv')`<br>3. `disable_max_rows()`（不推荐，可能导致浏览器崩溃） |
| 保存的 HTML/PNG 不显示图表 | 检查数据转换器：<br>- 如果使用 `json`/`csv`，save 时会自动切换到 `default`<br>- 如果使用 `vegafusion`，确保理解 `pre_transform=False` 的行为 |
| VegaFusion 时报 `format="vega"` 错误 | 使用 `chart.to_dict(format="vega")` 或 `chart.to_json(format="vega")` |
| 大数据集交互式选择慢 | 使用 `JupyterChart` 或 `renderers.enable('jupyter')` + `vegafusion`，支持服务端动态计算 |

### 9.3 代码示例

```python
import altair as alt
import pandas as pd

# 示例：100 万行数据的处理
large_data = pd.DataFrame({
    "category": ["A", "B", "C"] * 333334,
    "value": range(1_000_002)
})

# 推荐方案：VegaFusion + 聚合
alt.data_transformers.enable('vegafusion')

# 聚合后只有 3 行数据，永远不会触发行限制
chart = alt.Chart(large_data).mark_bar().encode(
    x='category:N',
    y='sum(value):Q'
)

# 正常保存
chart.save('chart.html')
chart.save('chart.png')

# 获取聚合后的数据（用于调试）
from altair.utils._transformed_data import transformed_data
aggregated_df = transformed_data(chart)
print(aggregated_df)  # 只有 3 行
```
