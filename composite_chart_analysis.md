# Altair 复合图表合成机制分析报告

## 目录
1. [概述](#1-概述)
2. [运算符重载机制](#2-运算符重载机制)
3. [编码继承与覆盖规则](#3-编码继承与覆盖规则)
4. [嵌套复合图表序列化路径](#4-嵌套复合图表序列化路径)
5. [顶层混入与数据源继承策略](#5-顶层混入与数据源继承策略)
6. [类关系图与数据流向](#6-类关系图与数据流向)

---

## 1. 概述

Altair 提供了五类复合图表组合方式：
- **分层 (Layer)**：使用 `+` 运算符或 `alt.layer()` 函数
- **水平拼接 (HConcat)**：使用 `|` 运算符或 `alt.hconcat()` 函数
- **垂直拼接 (VConcat)**：使用 `&` 运算符或 `alt.vconcat()` 函数
- **重复 (Repeat)**：使用 `.repeat()` 方法
- **分面 (Facet)**：使用 `.facet()` 方法或分面编码通道

这些组合方式通过精心设计的类继承体系和运算符重载机制实现，支持任意深度的嵌套组合。

---

## 2. 运算符重载机制

### 2.1 运算符与复合图表类的映射关系

| 运算符 | 函数形式 | 返回类型 | 文件位置 |
|--------|----------|----------|----------|
| `+` | `alt.layer()` | `LayerChart` 或 `FacetChart` | `api.py:2427` |
| `&` | `alt.vconcat()` | `VConcatChart` | `api.py:2433` |
| `|` | `alt.hconcat()` 或 `alt.concat()` | `HConcatChart` 或 `ConcatChart` | `api.py:2440` |
| `.repeat()` | - | `RepeatChart` | `api.py:2449` |
| `.facet()` | - | `FacetChart` | `api.py:3933` |

### 2.2 运算符重载实现细节

#### 2.2.1 加号运算符 (`+`) - 分层

```python
# api.py:2427-2431
def __add__(self, other: ChartType) -> LayerChart | FacetChart:
    if not is_chart_type(other):
        msg = "Only Chart objects can be layered."
        raise ValueError(msg)
    return layer(t.cast("ChartType", self), other)
```

**关键行为**：
- 调用 `layer()` 函数进行实际的分层操作
- `layer()` 函数会检查所有层是否共享相同的分面编码（`row`、`column`、`facet`）
- 如果所有层共享相同的分面编码，会自动提升为 `FacetChart`

#### 2.2.2 与符号运算符 (`&`) - 垂直拼接

```python
# api.py:2433-2438
def __and__(self, other: ChartType) -> VConcatChart:
    if not is_chart_type(other):
        msg = "Only Chart objects can be concatenated."
        raise ValueError(msg)
    return vconcat(t.cast("ChartType", self), other)
```

**关键行为**：
- 调用 `vconcat()` 函数创建 `VConcatChart`
- 支持任意有效的图表类型作为操作数

#### 2.2.3 竖线运算符 (`|`) - 水平拼接

```python
# api.py:2440-2447
def __or__(self, other: ChartType) -> HConcatChart | ConcatChart:
    if not is_chart_type(other):
        msg = "Only Chart objects can be concatenated."
        raise ValueError(msg)
    elif isinstance(self, ConcatChart):
        return concat(self, other)
    else:
        return hconcat(t.cast("ChartType", self), other)
```

**关键行为**：
- 如果左操作数已是 `ConcatChart`，则调用 `concat()` 函数
- 否则调用 `hconcat()` 函数创建 `HConcatChart`
- 这种设计支持链式拼接，如 `chart1 | chart2 | chart3`

### 2.3 左右操作数类型差异的处理

#### 2.3.1 类型兼容性检查

所有运算符重载方法首先调用 `is_chart_type()` 函数检查操作数类型：

```python
# api.py:5516-5525
def is_chart_type(obj: Any) -> TypeIs[ChartType]:
    return isinstance(
        obj,
        (Chart, RepeatChart, ConcatChart, HConcatChart, VConcatChart, FacetChart, LayerChart)
    )
```

**合法的操作数类型**：
- `Chart` - 基础图表
- `LayerChart` - 分层图表
- `HConcatChart` - 水平拼接图表
- `VConcatChart` - 垂直拼接图表
- `ConcatChart` - 通用拼接图表（支持换行）
- `RepeatChart` - 重复图表
- `FacetChart` - 分面图表

#### 2.3.2 特殊情况处理

**分层操作的限制**（`test_api.py:1716-1741`）：
- 带有 `.config.*` 属性的图表不能直接参与分层
- 拼接图表（HConcatChart、VConcatChart、ConcatChart）不能直接参与分层
- 重复图表（RepeatChart）不能直接参与分层
- 分面图表（FacetChart）的分面编码必须在所有层中一致

**分面编码自动提升**（`_hoist_facet_encodings` 函数，`api.py:4940-4979`）：
- 如果所有层共享相同的 `row`、`column` 或 `facet` 编码
- 这些编码会被从各层中剥离并提升到顶层
- 返回 `FacetChart` 而非 `LayerChart`

---

## 3. 编码继承与覆盖规则

### 3.1 类继承体系中的编码能力

#### 3.1.1 继承关系概览

```
┌─────────────────────────────────────────────────────────────┐
│                      TopLevelMixin                            │
│  (顶层混入：to_dict、to_json、to_html、interactive 等)       │
└──────────────────────────┬──────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
        ▼                  ▼                  ▼
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│ _EncodingMixin│  │    Chart      │  │  LayerChart   │
│  (encode方法) │  │ (继承两者)    │  │ (继承两者)    │
└───────┬───────┘  └───────────────┘  └───────────────┘
        │
        └──────────────────────────────────────────────┐
                                                       │
                    ┌──────────────────────────────────┼─────────────────┐
                    │                                  │                 │
                    ▼                                  ▼                 ▼
            ┌───────────────┐                  ┌───────────────┐  ┌───────────────┐
            │ HConcatChart  │                  │ VConcatChart  │  │ ConcatChart   │
            │ (无_EncodingMixin)               │ (无_EncodingMixin)│ (无_EncodingMixin)│
            └───────────────┘                  └───────────────┘  └───────────────┘
```

#### 3.1.2 关键差异：`_EncodingMixin` 的继承

| 类名 | 继承 `_EncodingMixin` | 是否支持顶层 `encode()` | 文件位置 |
|------|----------------------|------------------------|----------|
| `Chart` | 是 | 是 | `api.py:4001` |
| `LayerChart` | 是 | 是 | `api.py:4803` |
| `HConcatChart` | 否 | 否 | `api.py:4593` |
| `VConcatChart` | 否 | 否 | `api.py:4697` |
| `ConcatChart` | 否 | 否 | `api.py:4488` |
| `RepeatChart` | 否 | 否 | `api.py:4334` |
| `FacetChart` | 否 | 否 | `api.py:4998` |

**设计意图**：
- **分层图表 (LayerChart)**：所有层共享同一坐标系，顶层编码会被所有层继承
- **拼接图表 (HConcatChart/VConcatChart)**：每个子图表是独立的视图，不共享编码

### 3.2 `_EncodingMixin.encode()` 方法的实现

```python
# channels.py:22291-22305
copy = self.copy(deep=["encoding"])
encoding = copy._get("encoding", {})
if isinstance(encoding, core.VegaLiteSchema):
    encoding = {k: v for k, v in encoding._kwds.items() if v is not Undefined}
# update with the new encodings, and apply them to the copy
encoding.update(kwargs)
copy.encoding = core.FacetedEncoding(**encoding)
return copy
```

**核心机制**：
- 使用 `encoding.update(kwargs)` 进行字典合并
- **相同通道的编码会被覆盖**（后来的调用覆盖之前的）
- 返回新的图表对象，不修改原对象

### 3.3 分层图表的编码继承规则

#### 3.3.1 顶层编码的作用

`LayerChart` 继承了 `_EncodingMixin`，因此可以在顶层设置编码：

```python
# 测试用例：test_api.py:1433-1435
def test_layer_encodings():
    chart = alt.LayerChart().encode(x="column:Q")
    assert chart.encoding.x == alt.X(shorthand="column:Q")
```

**继承规则**：
- 分层图表的顶层 `encoding` 会被所有子层继承
- 子层可以通过定义自己的编码来覆盖顶层编码
- 这是通过 Vega-Lite 的规范解析机制实现的，而非 Altair 层面的特殊处理

#### 3.3.2 分面编码的特殊处理

分面编码（`row`、`column`、`facet`）有特殊的提升机制：

```python
# api.py:4940-4979
def _hoist_facet_encodings(
    subcharts: Sequence[LayerType],
) -> tuple[list[LayerType], dict[str, Any]]:
    """
    Extract common facet encodings from layers if all layers share them.
    """
    per_chart = [_get_facet_spec(c) for c in subcharts]
    
    # 检查是否所有层都有相同的分面编码
    first_cmp = _serialize(per_chart[0])
    if not all(_serialize(d) == first_cmp for d in per_chart[1:]):
        # 分面编码不一致，不提升
        return list(subcharts), {}
    
    # 所有层共享相同的分面编码 - 从各层中剥离
    cleaned: list[Any] = []
    for chart in subcharts:
        chart = chart.copy(deep=["encoding"])
        encoding = chart._get("encoding")
        if not utils.is_undefined(encoding):
            for ch in _FACET_CHANNELS:  # ("row", "column", "facet")
                encoding[ch] = Undefined
        cleaned.append(chart)
    
    return cleaned, per_chart[0]
```

**处理流程**：
1. 提取所有层的分面编码
2. 检查是否所有层的分面编码完全相同
3. 如果相同，从各层中剥离这些编码
4. 返回清理后的层和提升的分面编码
5. `layer()` 函数使用这些提升的编码创建 `FacetChart`

### 3.4 拼接图表的编码规则

**拼接图表的特点**：
- 不继承 `_EncodingMixin`，因此**不支持**顶层 `encode()` 调用
- 每个子图表的编码是独立的
- 子图表之间可以通过 `resolve_scale()`、`resolve_axis()`、`resolve_legend()` 来协调

**resolve 方法的实现**（`api.py:3898-3926`）：
```python
def _set_resolve(self, **kwargs: Any):
    """Copy the chart and update the resolve property with kwargs."""
    copy = _top_schema_base(self).copy(deep=["resolve"])
    if copy.resolve is Undefined:
        copy.resolve = core.Resolve()
    for key, val in kwargs.items():
        copy.resolve[key] = val
    return copy
```

**使用示例**：
```python
# 让拼接的图表共享颜色图例
(chart1 | chart2).resolve_legend(color="shared")

# 让拼接的图表使用独立的坐标轴
(chart1 & chart2).resolve_axis(x="independent")
```

---

## 4. 嵌套复合图表序列化路径

### 4.1 序列化入口：`TopLevelMixin.to_dict()`

```python
# api.py:2044-2157
def to_dict(
    self,
    validate: bool = True,
    *,
    format: Literal["vega-lite", "vega"] = "vega-lite",
    ignore: list[str] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # 上下文管理：用于数据类型推断和数据集管理
    context = context.copy() if context else {}
    context.setdefault("datasets", {})
    is_top_level = context.get("top_level", True)
    
    # 数据准备
    copy = _top_schema_base(self).copy(deep=False)
    original_data = getattr(copy, "data", Undefined)
    if not utils.is_undefined(original_data):
        copy.data = _prepare_data(data, context)
        context["data"] = data
    
    # 标记子调用为非顶层
    context["top_level"] = False
    
    # 调用父类的 to_dict 进行递归序列化
    vegalite_spec: Any = _top_schema_base(super(TopLevelMixin, copy)).to_dict(
        validate=validate, ignore=ignore, context=dict(context, pre_transform=False)
    )
    
    # 顶层特殊处理：添加 $schema 和主题
    if is_top_level:
        if "$schema" not in vegalite_spec:
            vegalite_spec["$schema"] = SCHEMA_URL
        if func := theme.get():
            vegalite_spec = utils.update_nested(func(), vegalite_spec, copy=True)
        if context["datasets"]:
            vegalite_spec.setdefault("datasets", {}).update(context["datasets"])
    
    return vegalite_spec
```

### 4.2 基础序列化：`SchemaBase.to_dict()`

```python
# schemapi.py:1174-1227
def to_dict(
    self,
    validate: bool = True,
    *,
    ignore: list[str] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = context or {}
    ignore = ignore or []
    
    # 提取关键字参数
    if self._args and not self._kwds:
        kwds = self._args[0]
    elif not self._args:
        kwds = self._kwds.copy()
        exclude = {*ignore, "shorthand", "_cached_hash"}
        kwds = {k: v for k, v in kwds.items() if k not in exclude}
        # 字符串标记转换为对象形式
        if (mark := kwds.get("mark")) and isinstance(mark, str):
            kwds["mark"] = {"type": mark}
    else:
        raise ValueError(...)
    
    # 递归处理嵌套对象
    result = _todict(kwds, context=context, **opts)
    
    # Schema 验证
    if validate:
        self.validate(result)
    
    return result
```

### 4.3 递归序列化机制

#### 4.3.1 `_todict` 函数的作用

`_todict` 函数（位于 `schemapi.py`）负责递归处理所有嵌套对象：

**处理逻辑**：
1. 遍历字典中的所有值
2. 对于 `SchemaBase` 子类，递归调用其 `to_dict()` 方法
3. 对于列表/元组，递归处理每个元素
4. 对于其他类型，保持不变

#### 4.3.2 嵌套复合图表的序列化路径示例

以 `(chart1 + chart2) | chart3` 为例：

```
HConcatChart.to_dict()
│
├── context["top_level"] = True（首次调用）
├── 准备数据
├── context["top_level"] = False
│
└── 调用 SchemaBase.to_dict()
    │
    ├── 提取 kwds: {"hconcat": [LayerChart, Chart3], ...}
    │
    └── _todict(kwds, context)
        │
        ├── 处理 "hconcat" 列表
        │   │
        │   ├── LayerChart.to_dict(context)
        │   │   │
        │   │   ├── context["top_level"] = False
        │   │   ├── 准备数据（如果有）
        │   │   │
        │   │   └── SchemaBase.to_dict()
        │   │       │
        │   │       ├── 提取 kwds: {"layer": [Chart1, Chart2], ...}
        │   │       │
        │   │       └── _todict(kwds, context)
        │   │           │
        │   │           └── 处理 "layer" 列表
        │   │               ├── Chart1.to_dict(context)
        │   │               └── Chart2.to_dict(context)
        │   │
        │   └── Chart3.to_dict(context)
        │
        └── 处理其他属性
```

### 4.4 Schema 规范化处理

#### 4.4.1 数据规范化

在 `TopLevelMixin.to_dict()` 中：
```python
# 数据准备
original_data = getattr(copy, "data", Undefined)
if not utils.is_undefined(original_data):
    try:
        data = nw.from_native(original_data, eager_or_interchange_only=True)
    except TypeError:
        data = original_data
    copy.data = _prepare_data(data, context)
    context["data"] = data
```

**数据处理流程**：
1. 尝试使用 Narwhals 库转换数据（支持 pandas、polars 等）
2. 调用 `_prepare_data()` 进行实际的数据处理
3. 将处理后的数据存入 `context` 供子图表使用

#### 4.4.2 标记规范化

在 `SchemaBase.to_dict()` 中：
```python
# 字符串标记转换为对象形式
if (mark := kwds.get("mark")) and isinstance(mark, str):
    kwds["mark"] = {"type": mark}
```

**规范化示例**：
- `"point"` → `{"type": "point"}`
- `"bar"` → `{"type": "bar"}`

#### 4.4.3 验证机制

```python
if validate:
    self.validate(result)
```

**验证内容**：
- 检查生成的字典是否符合 Vega-Lite JSON Schema
- 确保所有必需字段存在
- 确保字段类型正确

### 4.5 特殊属性的序列化处理

#### 4.5.1 分层图表的属性提升

```python
# api.py:4826-4831
# Some properties are not allowed within layer; we'll move to parent.
layer_props = ("height", "width", "view")
combined_dict, self.layer = _remove_layer_props(self, self.layer, layer_props)

for prop in combined_dict:
    self[prop] = combined_dict[prop]
```

**`_remove_layer_props` 函数的逻辑**（`api.py:5408-5459`）：
1. 检查顶层是否已定义该属性
2. 如果顶层未定义，检查所有子层的该属性值
3. 如果所有子层的值相同，提升到顶层
4. 如果子层的值不一致，抛出 `ValueError`
5. 从子层中移除该属性

**测试用例**（`test_layer_props.py`）：
```python
def test_layer_props():
    base = alt.Chart().mark_point()
    
    # 允许：相同的 width 或不同的 height/width 组合
    base.properties(width=100) + base
    base.properties(width=100) + base.properties(height=200)
    
    # 不允许：不一致的 width
    with pytest.raises(ValueError, match="inconsistent"):
        base.properties(width=100) + base.properties(width=200)
```

---

## 5. 顶层混入与数据源继承策略

### 5.1 顶层混入 (`TopLevelMixin`)

#### 5.1.1 继承体系

```python
# api.py:2038
class TopLevelMixin(mixins.ConfigMethodMixin):
    """Mixin for top-level chart objects such as Chart, LayeredChart, etc."""
```

**继承的类**：
- `mixins.ConfigMethodMixin`：提供配置相关方法

#### 5.1.2 提供的核心方法

| 方法 | 功能 | 文件位置 |
|------|------|----------|
| `to_dict()` | 序列化为字典 | `api.py:2044` |
| `to_json()` | 序列化为 JSON 字符串 | `api.py:2159` |
| `to_html()` | 转换为 HTML | `api.py:2221` |
| `interactive()` | 添加交互式缩放/平移 | `api.py:2298` |
| `add_params()` | 添加参数/选择器 | `api.py:2323` |
| `_set_resolve()` | 设置 scale/axis/legend 解析 | `api.py:3898` |
| `resolve_axis()` | 配置坐标轴解析 | `api.py:3911` |
| `resolve_legend()` | 配置图例解析 | `api.py:3917` |
| `resolve_scale()` | 配置比例尺解析 | `api.py:3923` |

#### 5.1.3 `interactive()` 方法的差异实现

**基础图表 (Chart)**：
```python
# 直接在自身添加选择器
def interactive(self, ...):
    return self.add_params(selection_interval(bind="scales", encodings=encodings))
```

**分层图表 (LayerChart)**：
```python
# api.py:4879-4908
def interactive(self, ...):
    if not self.layer:
        raise ValueError("LayerChart: cannot call interactive() until a layer is defined")
    copy = self.copy(deep=["layer"])
    # 只在第一层添加交互
    copy.layer[0] = copy.layer[0].interactive(name=name, bind_x=bind_x, bind_y=bind_y)
    return copy
```

**拼接图表 (HConcatChart/VConcatChart/ConcatChart)**：
```python
# api.py:4649-4676 (HConcatChart 示例)
def interactive(self, ...):
    encodings: list[SingleDefUnitChannel_T] = []
    if bind_x:
        encodings.append("x")
    if bind_y:
        encodings.append("y")
    # 在所有子图表中添加参数
    return self.add_params(selection_interval(bind="scales", encodings=encodings))
```

**重复/分面图表**：
```python
# 只在 spec 中添加交互
def interactive(self, ...):
    copy = self.copy(deep=False)
    copy.spec = copy.spec.interactive(name=name, bind_x=bind_x, bind_y=bind_y)
    return copy
```

### 5.2 数据源就近继承策略

#### 5.2.1 `_combine_subchart_data` 函数

这是数据源继承的核心实现：

```python
# api.py:5110-5151
def _combine_subchart_data(
    data: Optional[ChartDataType], subcharts: list[ChartType]
) -> tuple[Optional[ChartDataType], list[ChartType]]:
    def remove_data(subchart: _TSchemaBase) -> _TSchemaBase:
        if subchart.data is not Undefined:
            # 在移除数据前计算并缓存哈希
            if (
                isinstance(subchart, Chart)
                and getattr(subchart, "name", None) in (None, Undefined)
                and hasattr(subchart, "_compute_hash")
            ):
                cached_hash = subchart._compute_hash()
                subchart["_cached_hash"] = cached_hash
            
            subchart = subchart.copy()
            subchart.data = Undefined
        
        return subchart

    if not subcharts:
        pass
    elif data is Undefined:
        # 顶层没有数据；所有子图表数据必须相同才能提升
        subdata = subcharts[0].data
        if subdata is not Undefined and all(c.data is subdata for c in subcharts):
            data = subdata
            subcharts = [remove_data(c) for c in subcharts]
    elif all(c.data is Undefined or c.data is data for c in subcharts):
        # 顶层有数据；子图表数据必须是未定义或与顶层相同
        subcharts = [remove_data(c) for c in subcharts]

    return data, subcharts
```

#### 5.2.2 三种情况的处理规则

| 场景 | 顶层数据 | 子图表数据 | 处理方式 |
|------|----------|------------|----------|
| **情况 1** | 无数据 | 全部相同 | 提升到顶层，子图表移除数据 |
| **情况 2** | 无数据 | 部分相同/不同 | 不做处理，各子图表保留自己的数据 |
| **情况 3** | 有数据 | 未定义或与顶层相同 | 子图表移除数据，使用顶层数据 |
| **情况 4** | 有数据 | 与顶层不同 | 不做处理，子图表保留自己的数据 |

#### 5.2.3 测试用例验证

**测试：顶层无数据，子图表数据相同**（`test_api.py:1667-1703`）：
```python
@pytest.mark.parametrize(
    ["func", "method"],
    [(alt.layer, "layer"), (alt.hconcat, "hconcat"), (alt.vconcat, "vconcat")],
)
def test_compound_data_inheritance(func, method):
    data = pd.DataFrame({"x": [1, 2]})
    point = alt.Chart(data).mark_point()
    nodata = alt.Chart().mark_point()
    
    # 两个图表都使用相同的数据
    chart1 = func(point, point)
    assert chart1.data is data
    assert getattr(chart1, method)[0].data is Undefined
    assert getattr(chart1, method)[1].data is Undefined
    
    # 一个有数据，一个没有
    chart2 = func(point, nodata)
    assert chart2.data is Undefined
    assert getattr(chart2, method)[0].data is data
```

**测试：分层后分面的数据继承**（`test_api.py:1705-1714`）：
```python
def test_layer_facet(basic_chart):
    chart = (basic_chart + basic_chart).facet(row="row:Q")
    assert chart.data is not Undefined
    assert chart.spec.data is Undefined
    for layer in chart.spec.layer:
        assert layer.data is Undefined
    
    dct = chart.to_dict()
    assert "data" in dct
```

### 5.3 参数（选择器）的合并策略

#### 5.3.1 `_combine_subchart_params` 函数

```python
# api.py:5243-...
def _combine_subchart_params(
    params: Optional[Sequence[_Parameter]], subcharts: list[ChartType]
) -> tuple[Optional[Sequence[_Parameter]], list[ChartType]]:
```

**核心功能**：
1. 收集顶层和所有子图表的参数
2. 合并相同的参数（避免重复）
3. 为需要的子图表生成唯一名称
4. 更新参数的 `views` 属性以关联对应的子图表

#### 5.3.2 视图命名机制

```python
# api.py:5271-5285
if _needs_name(subchart):
    # 对于拼接图表，即使相同的图表也需要唯一名称
    base_name = subchart._get_view_hash_name()
    subchart.name = f"{base_name}_{i}"

# 在拼接中，FacetCharts 需要通过位置区分
if is_concat and isinstance(subchart, FacetChart):
    spec = subchart.spec
    subchart.spec = spec.copy(deep=True)
    spec = subchart.spec
    if isinstance(spec, LayerChart) and spec.layer:
        spec.layer[0].name = f"{_view_base_for_chart(spec.layer[0])}_{i}"
    elif isinstance(spec, Chart):
        spec.name = f"{_view_base_for_chart(spec)}_{i}"
```

#### 5.3.3 分层图表的特殊处理

**重复参数移除**（`api.py:5190-5218`）：
```python
def _remove_duplicate_params(layer: list[ChartType]) -> list[ChartType]:
    """
    Currently (Vega-Lite 5.5) the same param can't occur on two layers.
    """
    subcharts = [subchart.copy() for subchart in layer]
    found_params = []
    
    for subchart in subcharts:
        params: list[_Parameter] = []
        for param in subchart.params:
            if isinstance(param, core.VariableParameter):
                params.append(param)
                continue
            
            p = param.copy()
            pd = _viewless_dict(p)
            
            if pd not in found_params:
                params.append(p)
                found_params.append(pd)
        
        if len(params) == 0:
            subchart.params = Undefined
        else:
            subchart.params = params
    
    return subcharts
```

**测试用例**（`test_api.py:1476-1494`）：
```python
def test_layer_add_selection():
    base = alt.Chart("data.csv").mark_point()
    selection = alt.selection_point()
    alt.Chart._counter = 0
    chart1 = alt.layer(base.add_params(selection), base)
    alt.Chart._counter = 0
    chart2 = alt.layer(base, base).add_params(selection)
    assert chart1.to_dict() == chart2.to_dict()
```

---

## 6. 类关系图与数据流向

### 6.1 完整类继承关系图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              SchemaBase                                    │
│  (schemapi.py:1065)                                                        │
│  - to_dict(), to_json(), from_dict(), from_json(), validate()            │
│  - _args, _kwds 存储实际数据                                               │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        │                       │                       │
        ▼                       ▼                       ▼
┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐
│  TopLevelSpecs    │  │  Encoding Classes │  │  Other Schema     │
│  (core.py)        │  │  (channels.py)    │  │  Classes          │
└─────────┬─────────┘  └─────────┬─────────┘  └───────────────────┘
          │                      │
          │        ┌─────────────┘
          │        │
          ▼        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        API Layer (api.py)                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                      Mixins                                       │   │
│  ├───────────────────────────────┬───────────────────────────────┤   │
│  │  TopLevelMixin                │  _EncodingMixin               │   │
│  │  (继承 ConfigMethodMixin)     │  (继承 channels._EncodingMixin)│   │
│  ├───────────────────────────────┼───────────────────────────────┤   │
│  │  - to_dict() / to_json()     │  - encode()                   │   │
│  │  - to_html()                  │  - facet()                    │   │
│  │  - interactive()              │                               │   │
│  │  - add_params()               │                               │   │
│  │  - resolve_*()                │                               │   │
│  └───────────────────────────────┴───────────────────────────────┘   │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                      Chart Classes                                │   │
│  ├─────────────────────────────────────────────────────────────────┤   │
│  │                                                                   │   │
│  │  Chart                                                           │   │
│  │  ├── 继承: TopLevelMixin, _EncodingMixin, MarkMethodMixin      │   │
│  │  ├── 基类: core.TopLevelUnitSpec                                │   │
│  │  └── 用途: 基础单视图图表                                        │   │
│  │                                                                   │   │
│  │  LayerChart                                                      │   │
│  │  ├── 继承: TopLevelMixin, _EncodingMixin                        │   │
│  │  ├── 基类: core.TopLevelLayerSpec                               │   │
│  │  ├── 运算符: + / __add__ / __iadd__                             │   │
│  │  ├── 支持顶层 encode()                                          │   │
│  │  └── 特殊处理: height/width/view 属性提升                        │   │
│  │                                                                   │   │
│  │  HConcatChart                                                    │   │
│  │  ├── 继承: TopLevelMixin (无 _EncodingMixin)                    │   │
│  │  ├── 基类: core.TopLevelHConcatSpec                             │   │
│  │  ├── 运算符: | / __or__ / __ior__                               │   │
│  │  └── 不支持顶层 encode()                                         │   │
│  │                                                                   │   │
│  │  VConcatChart                                                    │   │
│  │  ├── 继承: TopLevelMixin (无 _EncodingMixin)                    │   │
│  │  ├── 基类: core.TopLevelVConcatSpec                             │   │
│  │  ├── 运算符: & / __and__ / __iand__                             │   │
│  │  └── 不支持顶层 encode()                                         │   │
│  │                                                                   │   │
│  │  ConcatChart                                                     │   │
│  │  ├── 继承: TopLevelMixin (无 _EncodingMixin)                    │   │
│  │  ├── 基类: core.TopLevelConcatSpec                              │   │
│  │  ├── 用途: 通用拼接（支持 columns 参数自动换行）                  │   │
│  │  └── 不支持顶层 encode()                                         │   │
│  │                                                                   │   │
│  │  RepeatChart                                                     │   │
│  │  ├── 继承: TopLevelMixin (无 _EncodingMixin)                    │   │
│  │  ├── 基类: core.TopLevelRepeatSpec                              │   │
│  │  ├── 方法: .repeat()                                            │   │
│  │  ├── 用途: 按行/列重复图表（不同编码）                            │   │
│  │  └── 不支持顶层 encode()                                         │   │
│  │                                                                   │   │
│  │  FacetChart                                                      │   │
│  │  ├── 继承: TopLevelMixin (无 _EncodingMixin)                    │   │
│  │  ├── 基类: core.TopLevelFacetSpec                               │   │
│  │  ├── 方法: .facet() 或 分面编码自动提升                          │   │
│  │  ├── 用途: 按数据子集分面（小多图）                               │   │
│  │  └── 不支持顶层 encode()                                         │   │
│  │                                                                   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                           │
└─────────────────────────────────────────────────────────────────────────┘
```

### 6.2 数据流向图

#### 6.2.1 运算符操作数据流

```
用户代码: chart1 + chart2
           │
           ▼
┌─────────────────────────────────┐
│  Chart.__add__(other)           │
│  api.py:2427-2431               │
└─────────────┬───────────────────┘
              │
              ▼
┌─────────────────────────────────┐
│  layer(chart1, chart2)          │
│  api.py:4982-4995               │
└─────────────┬───────────────────┘
              │
              ▼
┌─────────────────────────────────┐
│  _hoist_facet_encodings()       │
│  api.py:4940-4979               │
│  - 检查是否所有层共享分面编码      │
│  - 如果是，提升为 FacetChart     │
└─────────────┬───────────────────┘
              │
              ▼
┌─────────────────────────────────┐
│  LayerChart.__init__()          │
│  api.py:4806-4831               │
├─────────────────────────────────┤
│  1. _check_if_valid_subspec()   │
│  2. _check_if_can_be_layered()  │
│  3. _combine_subchart_data()    │  ◄── 数据合并
│  4. _remove_duplicate_params()  │  ◄── 去重参数
│  5. _combine_subchart_params()  │  ◄── 合并参数
│  6. _remove_layer_props()       │  ◄── 属性提升
└─────────────────────────────────┘
```

#### 6.2.2 序列化数据流

```
用户代码: chart.to_dict()
           │
           ▼
┌──────────────────────────────────────┐
│  TopLevelMixin.to_dict()             │
│  api.py:2044-2157                    │
├──────────────────────────────────────┤
│  1. 准备 context 字典                 │
│  2. _prepare_data() 处理数据          │
│  3. context["top_level"] = False     │
│  4. 调用父类 to_dict()                │
└──────────────┬───────────────────────┘
               │
               ▼
┌──────────────────────────────────────┐
│  SchemaBase.to_dict()                │
│  schemapi.py:1174-1227               │
├──────────────────────────────────────┤
│  1. 提取 _kwds                        │
│  2. 规范化 mark（字符串→对象）         │
│  3. _todict() 递归处理嵌套对象         │
│  4. validate() 验证 Schema            │
└──────────────┬───────────────────────┘
               │
               ▼
┌──────────────────────────────────────┐
│  _todict() 递归处理                   │
│  schemapi.py 内部函数                 │
├──────────────────────────────────────┤
│  遍历所有值:                           │
│  - SchemaBase 子类 → to_dict()        │
│  - 列表/元组 → 递归处理每个元素        │
│  - 其他 → 保持不变                     │
└──────────────────────────────────────┘
```

### 6.3 复合类型别名

```python
# api.py:5490-5513
ChartType: TypeAlias = (
    Chart
    | RepeatChart
    | ConcatChart
    | HConcatChart
    | VConcatChart
    | FacetChart
    | LayerChart
)

ConcatType: TypeAlias = (
    ChartType
    | core.FacetSpec
    | core.LayerSpec
    | core.RepeatSpec
    | core.FacetedUnitSpec
    | core.LayerRepeatSpec
    | core.NonNormalizedSpec
    | core.NonLayerRepeatSpec
    | core.ConcatSpecGenericSpec
    | core.HConcatSpecGenericSpec
    | core.VConcatSpecGenericSpec
)

LayerType: TypeAlias = ChartType | core.UnitSpec | core.LayerSpec
```

**类型设计意图**：
- `ChartType`：所有高级 API 图表类型
- `ConcatType`：拼接操作支持的更广泛类型（包括底层 schema 类型）
- `LayerType`：分层操作支持的类型

---

## 附录：关键函数位置索引

| 函数/方法 | 文件位置 | 功能 |
|----------|----------|------|
| `Chart.__add__` | `api.py:2427` | 加号运算符重载（分层） |
| `Chart.__and__` | `api.py:2433` | 与符号运算符重载（垂直拼接） |
| `Chart.__or__` | `api.py:2440` | 竖线运算符重载（水平拼接） |
| `layer()` | `api.py:4982` | 创建分层图表 |
| `hconcat()` | `api.py:4692` | 创建水平拼接图表 |
| `vconcat()` | `api.py:4798` | 创建垂直拼接图表 |
| `concat()` | `api.py:4588` | 创建通用拼接图表 |
| `_hoist_facet_encodings()` | `api.py:4940` | 分面编码自动提升 |
| `_combine_subchart_data()` | `api.py:5110` | 数据源合并 |
| `_combine_subchart_params()` | `api.py:5243` | 参数合并 |
| `_remove_duplicate_params()` | `api.py:5190` | 分层参数去重 |
| `_remove_layer_props()` | `api.py:5408` | 分层属性提升 |
| `TopLevelMixin.to_dict()` | `api.py:2044` | 顶层序列化 |
| `SchemaBase.to_dict()` | `schemapi.py:1174` | 基础序列化 |
| `_EncodingMixin.encode()` | `channels.py:21990` | 编码设置 |
| `is_chart_type()` | `api.py:5516` | 类型检查 |

---

## 总结

Altair 的复合图表合成机制设计精妙，主要特点包括：

1. **直观的运算符重载**：使用 `+`、`&`、`|` 等熟悉的运算符，配合函数形式 `alt.layer()`、`alt.hconcat()`、`alt.vconcat()`，提供自然的复合图表构建体验。

2. **清晰的类继承体系**：
   - `TopLevelMixin` 提供所有顶层图表共有的方法（序列化、交互、参数管理等）
   - `_EncodingMixin` 区分支持顶层编码的图表（Chart、LayerChart）和不支持的图表（拼接类）

3. **智能的数据和参数合并**：
   - `_combine_subchart_data()` 实现数据源的就近继承策略
   - `_combine_subchart_params()` 处理参数（选择器）的合并和视图关联
   - `_hoist_facet_encodings()` 自动处理分面编码的提升

4. **递归的序列化机制**：
   - `TopLevelMixin.to_dict()` 处理顶层特殊逻辑（数据准备、主题、$schema）
   - `SchemaBase.to_dict()` 和 `_todict()` 实现递归的嵌套对象序列化
   - 完整的 Schema 验证确保输出规范

5. **灵活的嵌套组合**：支持任意深度的嵌套，如 `(chart1 + chart2) | (chart3 & chart4)`，通过类型别名 `ConcatType` 和 `LayerType` 确保类型安全。

这种设计使得用户可以用简洁的语法构建复杂的多视图图表，同时保持代码的可读性和可维护性。
