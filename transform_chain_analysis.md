# Altair 链式变换操作（Transform）分析报告

## 1. 概述

Altair 提供了一套流畅的链式变换 API，允许用户通过多次调用 `transform_*` 方法来构建复杂的数据处理流程。这些变换最终会被合成到 Vega-Lite 规格中的 `transforms` 数组中。本文档深入分析这一机制的设计实现。

---

## 2. 不可变性与追加/替换语义

### 2.1 核心机制：`_add_transform` 方法

所有 `transform_*` 方法的底层实现都依赖于 `_add_transform` 方法，该方法定义在 `TopLevelMixin` 类中：

```python
# 文件: altair/vegalite/v6/api.py, 行 2683-2689
def _add_transform(self, *transforms: Transform) -> Self:
    """Copy the chart and add specified transforms to chart.transform."""
    copy = _top_schema_base(self).copy(deep=["transform"])
    if copy.transform is Undefined:
        copy.transform = []
    copy.transform.extend(transforms)
    return t.cast("Self", copy)
```

### 2.2 不可变性设计

**每次调用 `transform_*` 方法都会返回一个新的 Chart 对象**，原对象保持不变。这通过以下机制实现：

1. **深拷贝创建新实例**：
   ```python
   copy = _top_schema_base(self).copy(deep=["transform"])
   ```
   - `copy(deep=["transform"])` 对 `transform` 属性进行深拷贝
   - 确保原对象的 `transform` 列表与新对象完全独立

2. **惰性初始化**：
   ```python
   if copy.transform is Undefined:
       copy.transform = []
   ```
   - 当不存在变换时，初始化为空列表
   - 避免在没有变换时占用不必要的内存

3. **返回新对象而非修改原对象**：
   ```python
   return t.cast("Self", copy)
   ```

### 2.3 追加语义

所有 `transform_*` 方法都采用**追加**而非**替换**语义：

```python
copy.transform.extend(transforms)
```

- 使用 `list.extend()` 方法将新变换追加到列表末尾
- 每次调用都会在现有变换链后添加新的变换项
- **不存在替换原有变换的机制**

### 2.4 各变换方法的具体实现

所有 `transform_*` 方法遵循相同的模式：创建对应的 Transform 对象，然后调用 `_add_transform`。

#### transform_aggregate (聚合变换)

```python
# 文件: altair/vegalite/v6/api.py, 行 2691-2767
def transform_aggregate(
    self,
    aggregate: Optional[list[AggregatedFieldDef]] = Undefined,
    groupby: Optional[list[str | FieldName]] = Undefined,
    **kwds: dict[str, Any] | str,
) -> Self:
    # ... 参数解析逻辑 ...
    return self._add_transform(
        core.AggregateTransform(aggregate=aggregate, groupby=groupby)
    )
```

#### transform_filter (过滤变换)

```python
# 文件: altair/vegalite/v6/api.py, 行 3123-3229
def transform_filter(
    self,
    predicate: Optional[_PredicateType] = Undefined,
    *more_predicates: _ComposablePredicateType,
    empty: Optional[bool] = Undefined,
    **constraints: _FieldEqualType,
) -> Self:
    # ... 谓词解析逻辑，支持 &, |, ~ 操作符 ...
    cond = _parse_when(predicate, *more_predicates, empty=empty, **constraints)
    return self._add_transform(core.FilterTransform(filter=cond.get("test", cond)))
```

#### transform_window (窗口变换)

```python
# 文件: altair/vegalite/v6/api.py, 行 3696-3795
def transform_window(
    self,
    window: Optional[list[WindowFieldDef]] = Undefined,
    frame: Optional[list[int | None]] = Undefined,
    groupby: Optional[list[str]] = Undefined,
    ignorePeers: Optional[bool] = Undefined,
    sort: Optional[list[SortField | dict[str, str]]] = Undefined,
    **kwargs: str,
) -> Self:
    # ... 窗口字段解析 ...
    return self._add_transform(
        core.WindowTransform(
            window=w or Undefined,
            frame=frame,
            groupby=groupby,
            ignorePeers=ignorePeers,
            sort=sort,
        )
    )
```

### 2.5 完整的变换方法列表

| 方法名 | 对应 Transform 类 | 功能描述 |
|--------|------------------|----------|
| `transform_aggregate` | `AggregateTransform` | 数据聚合（求和、平均等） |
| `transform_bin` | `BinTransform` | 连续数据分箱 |
| `transform_calculate` | `CalculateTransform` | 计算新字段 |
| `transform_density` | `DensityTransform` | 密度估计 |
| `transform_extent` | `ExtentTransform` | 计算数据范围 |
| `transform_filter` | `FilterTransform` | 数据过滤 |
| `transform_flatten` | `FlattenTransform` | 展平数组字段 |
| `transform_fold` | `FoldTransform` | 宽表转长表 |
| `transform_impute` | `ImputeTransform` | 缺失值填充 |
| `transform_joinaggregate` | `JoinAggregateTransform` | 连接聚合 |
| `transform_loess` | `LoessTransform` | LOESS 平滑 |
| `transform_lookup` | `LookupTransform` | 数据查找连接 |
| `transform_pivot` | `PivotTransform` | 长表转宽表 |
| `transform_quantile` | `QuantileTransform` | 分位数计算 |
| `transform_regression` | `RegressionTransform` | 回归分析 |
| `transform_sample` | `SampleTransform` | 数据采样 |
| `transform_stack` | `StackTransform` | 堆叠变换 |
| `transform_timeunit` | `TimeUnitTransform` | 时间单位转换 |
| `transform_window` | `WindowTransform` | 窗口函数 |

---

## 3. 多层链式变换的顺序保持机制

### 3.1 顺序保持的核心原理

顺序保持通过以下三层机制实现：

```
用户调用顺序 → Python 列表 append 顺序 → Vega-Lite transforms 数组顺序
```

### 3.2 具体实现流程

1. **首次调用初始化**：
   ```python
   # 第一次调用 transform_filter
   chart = alt.Chart(data).transform_filter(datum.x > 0)
   # 内部执行:
   # copy.transform = []  (因为原来的 transform 是 Undefined)
   # copy.transform.extend([FilterTransform(...)])
   # 结果: copy.transform = [Filter1]
   ```

2. **后续调用追加**：
   ```python
   # 链式调用 transform_aggregate
   chart = chart.transform_aggregate(sum_y="sum(y)", groupby=["category"])
   # 内部执行:
   # 新的 copy 从之前的 copy 复制，transform = [Filter1]
   # copy.transform.extend([AggregateTransform(...)])
   # 结果: copy.transform = [Filter1, Aggregate1]
   ```

3. **继续链式调用**：
   ```python
   # 再添加窗口变换
   chart = chart.transform_window(running_sum="sum(sum_y)")
   # 结果: copy.transform = [Filter1, Aggregate1, Window1]
   ```

### 3.3 不可变性对顺序保持的影响

由于每次调用都返回新对象，以下两种写法**完全等价**：

**写法 1: 连续链式调用**
```python
chart = (
    alt.Chart(data)
    .transform_filter(datum.x > 0)           # 第1个变换
    .transform_aggregate(sum_y="sum(y)")     # 第2个变换
    .transform_window(running_sum="sum(sum_y)")  # 第3个变换
)
```

**写法 2: 分步调用（保存中间变量）**
```python
base = alt.Chart(data)
filtered = base.transform_filter(datum.x > 0)        # 第1个变换
aggregated = filtered.transform_aggregate(sum_y="sum(y)")  # 第2个变换
chart = aggregated.transform_window(running_sum="sum(sum_y)")  # 第3个变换
```

两种写法最终的 `transform` 数组顺序完全一致：
```json
{
  "transform": [
    {"filter": "datum.x > 0"},
    {"aggregate": [{"op": "sum", "field": "y", "as": "sum_y"}]},
    {"window": [{"op": "sum", "field": "sum_y", "as": "running_sum"}]}
  ]
}
```

### 3.4 中间对象的独立性

由于不可变性设计，中间对象可以被安全复用：

```python
base = alt.Chart(data)
filtered = base.transform_filter(datum.x > 0)  # 过滤后的数据

# 分支 1: 聚合
aggregated = filtered.transform_aggregate(sum_y="sum(y)")

# 分支 2: 窗口函数（独立于分支 1）
windowed = filtered.transform_window(ma="mean(y)", frame=[-5, 0])

# filtered 仍然只包含 filter 变换，不受 aggregated 和 windowed 影响
```

---

## 4. 继承链设计与方法委托

### 4.1 Chart 类的完整继承链

```
                              ┌─────────────────────┐
                              │  SchemaBase (utils) │
                              │  (底层 schema 包装)  │
                              └──────────┬──────────┘
                                         │
                    ┌────────────────────┼────────────────────┐
                    │                    │                    │
         ┌──────────▼──────────┐         │         ┌─────────▼─────────┐
         │  TopLevelUnitSpec   │         │         │   _EncodingMixin   │
         │   (core.py)         │         │         │  (channels.py)     │
         │  Vega-Lite 顶层规格  │         │         │   encode() 方法    │
         └──────────┬──────────┘         │         └─────────┬─────────┘
                    │                    │                    │
                    └────────────────────┼────────────────────┘
                                         │
                    ┌────────────────────┼────────────────────┐
                    │                    │                    │
         ┌──────────▼──────────┐         │         ┌─────────▼─────────┐
         │   MarkMethodMixin   │         │         │   ConfigMethodMixin │
         │    (mixins.py)      │         │         │     (mixins.py)     │
         │  mark_*() 系列方法  │         │         │  configure_*() 方法  │
         └──────────┬──────────┘         │         └─────────┬─────────┘
                    │                    │                    │
                    └────────────────────┼────────────────────┘
                                         │
                              ┌──────────▼──────────┐
                              │    TopLevelMixin     │
                              │     (api.py)         │
                              │  - _add_transform()  │
                              │  - transform_*()    │
                              │  - to_dict()         │
                              │  - save(), display() │
                              └──────────┬──────────┘
                                         │
                              ┌──────────▼──────────┐
                              │     Chart (api.py)   │
                              │  用户直接使用的类     │
                              └─────────────────────┘
```

### 4.2 关键混入类的职责划分

#### 4.2.1 TopLevelMixin (api.py)

这是变换功能的核心实现位置，包含：

- **`_add_transform()`**: 所有变换方法的底层实现
- **所有 `transform_*()` 方法**: `transform_aggregate`, `transform_filter`, `transform_window` 等
- **`to_dict()`**: 序列化方法，处理 transform 数组的导出
- **数据相关方法**: `save()`, `display()`, `to_url()` 等

```python
# 文件: altair/vegalite/v6/api.py, 行 2038
class TopLevelMixin(mixins.ConfigMethodMixin):
    """Mixin for top-level chart objects such as Chart, LayeredChart, etc."""
    
    # ... to_dict, save, display 等方法 ...
    
    def _add_transform(self, *transforms: Transform) -> Self:
        # 核心变换追加逻辑
        ...
    
    def transform_aggregate(self, ...) -> Self:
        ...
    
    def transform_filter(self, ...) -> Self:
        ...
    
    # ... 其他 transform_* 方法 ...
```

#### 4.2.2 _EncodingMixin (api.py 和 channels.py)

负责编码（encoding）相关功能：

- **`encode()` 方法**: 定义 x, y, color 等通道映射
- **`facet()` 方法**: 创建分面图表

```python
# 文件: altair/vegalite/v6/api.py, 行 3930
class _EncodingMixin(channels._EncodingMixin):
    data: Any

    def facet(self, ...) -> FacetChart:
        # 创建分面图表
        ...
```

#### 4.2.3 MarkMethodMixin (mixins.py)

负责标记类型方法：

- **`mark_point()`, `mark_line()`, `mark_bar()` 等**: 设置图表标记类型

#### 4.2.4 ConfigMethodMixin (mixins.py)

负责配置相关方法：

- **`configure_*()` 系列方法**: 配置图表样式、坐标轴、图例等

#### 4.2.5 TopLevelUnitSpec (core.py)

这是从 Vega-Lite schema 自动生成的类，定义了：

- `transform` 属性（类型为 `List[Transform]`）
- `data`, `mark`, `encoding`, `width`, `height` 等基本属性

### 4.3 方法委托机制

当用户调用 `chart.transform_filter(...)` 时，实际的方法查找顺序：

1. **Chart 类**: 不直接定义 `transform_filter`
2. **TopLevelMixin**: 找到 `transform_filter` 方法定义
3. **方法执行**:
   - `transform_filter` 解析参数，创建 `FilterTransform` 对象
   - 调用 `self._add_transform(FilterTransform(...))`
   - `_add_transform` 创建副本并追加变换
   - 返回新的 Chart 对象

### 4.4 MRO（方法解析顺序）

Chart 类的 MRO 如下（简化版）：

```
Chart → TopLevelMixin → _EncodingMixin → MarkMethodMixin 
      → ConfigMethodMixin → TopLevelUnitSpec → SchemaBase → object
```

这意味着：
- `transform_*` 方法来自 `TopLevelMixin`
- `encode()` 方法来自 `_EncodingMixin`
- `mark_*()` 方法来自 `MarkMethodMixin`
- `configure_*()` 方法来自 `ConfigMethodMixin`
- 基础属性和序列化来自 `TopLevelUnitSpec` 和 `SchemaBase`

---

## 5. 顺序敏感性场景分析

### 5.1 Vega-Lite 变换执行模型

Vega-Lite 的 `transform` 数组是**有序执行**的：

```
输入数据
    ↓
transform[0] 执行
    ↓
transform[1] 执行（基于 transform[0] 的输出）
    ↓
transform[2] 执行（基于 transform[1] 的输出）
    ↓
...
    ↓
最终数据 → 渲染
```

### 5.2 场景 1: 先过滤再聚合 vs 先聚合再过滤

这是最常见的顺序敏感性场景。

#### 示例数据

```python
data = pd.DataFrame({
    "category": ["A", "A", "B", "B", "C", "C"],
    "value": [10, 20, 30, 40, 50, 60]
})
```

#### 情况 A: 先过滤再聚合

```python
chart_a = (
    alt.Chart(data)
    .transform_filter(alt.datum.category != "C")  # 先过滤掉 C 类
    .transform_aggregate(
        total="sum(value)",
        groupby=["category"]
    )  # 仅聚合 A 和 B
)
```

**输出规格**:
```json
{
  "transform": [
    {"filter": "datum.category != 'C'"},
    {
      "aggregate": [{"op": "sum", "field": "value", "as": "total"}],
      "groupby": ["category"]
    }
  ]
}
```

**执行结果**:
| category | total |
|----------|-------|
| A        | 30    |
| B        | 70    |

**解释**:
1. 过滤后的数据: `[(A,10), (A,20), (B,30), (B,40)]`
2. 聚合: A 类总和 30，B 类总和 70
3. C 类被完全排除

#### 情况 B: 先聚合再过滤

```python
chart_b = (
    alt.Chart(data)
    .transform_aggregate(
        total="sum(value)",
        groupby=["category"]
    )  # 先聚合所有类别
    .transform_filter(alt.datum.category != "C")  # 再过滤
)
```

**输出规格**:
```json
{
  "transform": [
    {
      "aggregate": [{"op": "sum", "field": "value", "as": "total"}],
      "groupby": ["category"]
    },
    {"filter": "datum.category != 'C'"}
  ]
}
```

**执行结果**:
| category | total |
|----------|-------|
| A        | 30    |
| B        | 70    |

**解释**:
1. 聚合后的数据: `[(A,30), (B,70), (C,110)]`
2. 过滤后: `[(A,30), (B,70)]`
3. C 类在聚合后被排除

**看似结果相同，但语义差异巨大**：

如果过滤条件是基于聚合后的字段：

```python
# 先聚合再过滤：过滤聚合后的总和
chart_b2 = (
    alt.Chart(data)
    .transform_aggregate(total="sum(value)", groupby=["category"])
    .transform_filter(alt.datum.total > 50)  # 过滤聚合后的 total
)
# 结果: B(70), C(110)

# 先过滤再聚合：无法在聚合前过滤聚合后产生的字段
chart_a2 = (
    alt.Chart(data)
    .transform_filter(alt.datum.total > 50)  # 错误！total 字段不存在
    .transform_aggregate(total="sum(value)", groupby=["category"])
)
# 运行时错误：total 字段在原始数据中不存在
```

### 5.3 场景 2: 窗口函数与聚合的顺序

窗口函数和聚合的顺序也至关重要。

#### 情况 A: 先窗口再聚合

```python
chart = (
    alt.Chart(data)
    .transform_window(
        running_total="sum(value)",
        groupby=["category"],
        sort=[{"field": "id", "order": "ascending"}]
    )  # 先计算累计和
    .transform_aggregate(
        max_running="max(running_total)",
        groupby=["category"]
    )  # 再聚合
)
```

**语义**: 计算每个类别内的累计和，然后取每个类别累计和的最大值。

#### 情况 B: 先聚合再窗口

```python
chart = (
    alt.Chart(data)
    .transform_aggregate(
        total="sum(value)",
        groupby=["category"]
    )  # 先聚合
    .transform_window(
        overall_percent="total / sum(total)",
        sort=[]
    )  # 再计算窗口（基于聚合结果）
)
```

**语义**: 先计算每个类别的总和，然后计算每个类别占总体的百分比。

### 5.4 场景 3: calculate 和 filter 的顺序

`transform_calculate` 创建新字段，`transform_filter` 可以基于这些新字段进行过滤。

```python
# 正确顺序：先计算再过滤
chart = (
    alt.Chart(data)
    .transform_calculate(double_value="datum.value * 2")  # 创建新字段
    .transform_filter(alt.datum.double_value > 100)  # 基于新字段过滤
)
```

```python
# 错误顺序：先过滤再计算（可能导致不同结果）
chart = (
    alt.Chart(data)
    .transform_filter(alt.datum.value > 50)  # 先过滤原始字段
    .transform_calculate(double_value="datum.value * 2")  # 再计算
)
```

### 5.5 框架的约束与警告机制

**Altair 框架不提供任何顺序敏感性检查或警告**。

#### 5.5.1 设计哲学

Altair 的设计哲学是：
1. **用户负责顺序正确性**: 框架假设用户理解变换顺序的语义
2. **直接映射到 Vega-Lite**: Altair 的 transform 数组直接对应 Vega-Lite 的 transform 数组
3. **运行时处理**: 所有顺序相关的语义由 Vega-Lite 运行时处理

#### 5.5.2 无静态检查

以下代码在 Altair 中完全合法（尽管可能是错误的）：

```python
# 这可能是个错误：先聚合再过滤聚合前的字段
# 但 Altair 不会报错
chart = (
    alt.Chart(data)
    .transform_aggregate(total="sum(value)", groupby=["category"])
    .transform_filter(alt.datum.value > 100)  # value 字段在聚合后已不存在！
)
```

**运行时行为**:
- Vega-Lite 会静默处理：`datum.value` 在聚合后的行中不存在，结果为 `null`
- `null > 100` 可能导致意外的过滤行为（通常所有行都被过滤掉）

#### 5.5.3 无运行时警告

即使变换顺序导致空结果或意外行为，Altair 和 Vega-Lite 都不会发出警告。

### 5.6 最佳实践建议

1. **理解每个变换的输入输出**:
   - 聚合会改变数据结构（行数减少，字段变化）
   - 窗口函数保持行数不变，但添加新字段
   - 过滤减少行数

2. **使用中间变量提高可读性**:
   ```python
   with_calculated = base.transform_calculate(...)
   filtered = with_calculated.transform_filter(...)
   aggregated = filtered.transform_aggregate(...)
   ```

3. **通过 `to_dict()` 验证变换顺序**:
   ```python
   pprint(chart.to_dict()["transform"])
   # 检查变换顺序是否符合预期
   ```

4. **使用小样本数据测试**:
   ```python
   test_data = pd.DataFrame({...})  # 小样本数据
   test_chart = alt.Chart(test_data).transform_...
   # 检查输出是否符合预期
   ```

---

## 6. 代码位置汇总

| 功能 | 文件 | 行号 |
|------|------|------|
| `_add_transform` 核心方法 | `altair/vegalite/v6/api.py` | 2683-2689 |
| `transform_aggregate` | `altair/vegalite/v6/api.py` | 2691-2767 |
| `transform_filter` | `altair/vegalite/v6/api.py` | 3123-3229 |
| `transform_window` | `altair/vegalite/v6/api.py` | 3696-3795 |
| `TopLevelMixin` 类定义 | `altair/vegalite/v6/api.py` | 2038 |
| `Chart` 类定义 | `altair/vegalite/v6/api.py` | 4001 |
| `_EncodingMixin` (api.py) | `altair/vegalite/v6/api.py` | 3930 |
| `_EncodingMixin` (channels.py) | `altair/vegalite/v6/schema/channels.py` | 21990 |
| `TopLevelUnitSpec` | `altair/vegalite/v6/schema/core.py` | 自动生成 |
| 测试用例 | `tests/vegalite/v6/test_api.py` | 1141+ |

---

## 7. 总结

### 7.1 关键设计要点

1. **不可变性**: 每次 `transform_*` 调用都返回新对象，原对象保持不变
2. **追加语义**: 新变换总是追加到列表末尾，从不替换
3. **顺序保持**: 调用顺序严格对应最终 `transform` 数组的顺序
4. **Mixin 设计**: 通过多继承将功能分散到不同混入类中

### 7.2 与 Vega-Lite 的关系

Altair 的 transform 机制是 Vega-Lite transform 的**薄封装**：
- Altair 的 `chart.transform` 列表 → Vega-Lite 的 `transform` 数组
- 顺序、语义完全由 Vega-Lite 定义
- Altair 主要提供流畅的 API 和类型安全

### 7.3 对用户的启示

- **链式调用是安全的**: 不用担心副作用
- **顺序很重要**: 理解数据流转过程
- **无静态验证**: 依靠测试和文档确保正确性
