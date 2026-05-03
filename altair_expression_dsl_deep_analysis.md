# Altair 表达式 DSL 深度分析报告

## 目录

1. [方法链组装机制](#1-方法链组装机制)
2. [表达式序列化完整路径](#2-表达式序列化完整路径)
3. [日期时间与时区处理规则](#3-日期时间与时区处理规则)
4. [跨模块类型保持机制](#4-跨模块类型保持机制)
5. [关键设计模式总结](#5-关键设计模式总结)

---

## 1. 方法链组装机制

### 1.1 核心原理

Altair 的表达式 DSL 通过 **运算符重载** 和 **方法链** 实现 Python 语法到 Vega-Lite 表达式的转换。每个运算符调用都会创建一个新的表达式对象，形成一个不可变的表达式树。

### 1.2 运算符重载工作流程

当执行 `datum.price * 1.1` 时，实际发生的步骤：

```
步骤 1: Python 解释器识别到 `*` 运算符
        ↓
步骤 2: 调用左操作数的 `__mul__` 方法
        → datum.price.__mul__(1.1)
        ↓
步骤 3: `OperatorMixin.__mul__` 被执行
        → 创建 BinaryExpression("*", self, other)
        ↓
步骤 4: 调用 `_from_expr()` 进行类型转换
        → 保持正确的语义类型
        ↓
步骤 5: 返回新创建的表达式对象
```

### 1.3 详细执行追踪

让我们通过一个具体例子追踪完整的方法链组装过程：

```python
import altair as alt

# 构建复杂表达式
result = (alt.datum.price * 1.1) + alt.expr.sqrt(alt.datum.quantity)
```

#### 阶段 1: `alt.datum.price`

```python
# 执行: datum.price
# 实际调用: datum.__getattr__("price")

class DatumType:
    def __getattr__(self, attr) -> GetAttrExpression:
        if attr.startswith("__") and attr.endswith("__"):
            raise AttributeError(attr)
        return GetAttrExpression("datum", attr)
```
<mcfile name="core.py" path="altair/expr/core.py"></mcfile>

**结果**：`GetAttrExpression(group="datum", name="price")`
- `repr()` → `"datum.price"`

#### 阶段 2: `datum.price * 1.1`

```python
# 执行: datum.price * 1.1
# 实际调用: GetAttrExpression.__mul__(1.1)
# 继承自: OperatorMixin.__mul__

class OperatorMixin:
    def __mul__(self, other):
        comp_value = BinaryExpression("*", self, other)
        return self._from_expr(comp_value)
```
<mcfile name="core.py" path="altair/expr/core.py"></mcfile>

**发生了什么**：

1. **创建 BinaryExpression 对象**：
   ```python
   BinaryExpression(
       op="*",
       lhs=GetAttrExpression("datum", "price"),
       rhs=1.1
   )
   ```

2. **调用 `_from_expr()`**：
   - `Expression` 没有重写 `_from_expr`
   - 使用基类 `OperatorMixin._from_expr`，直接返回表达式

**结果**：`BinaryExpression(op="*", lhs=GetAttrExpression(...), rhs=1.1)`
- `repr()` → `"(datum.price * 1.1)"`

#### 阶段 3: `alt.expr.sqrt(datum.quantity)`

```python
# 执行: alt.expr.sqrt(datum.quantity)
# 实际调用: expr.sqrt(GetAttrExpression("datum", "quantity"))

class expr(_ExprRef, metaclass=_ExprMeta):
    @classmethod
    def sqrt(cls, value: IntoExpression, /) -> Expression:
        return FunctionExpression("sqrt", (value,))
```
<mcfile name="__init__.py" path="altair/expr/__init__.py"></mcfile>

**结果**：`FunctionExpression(name="sqrt", args=(GetAttrExpression("datum", "quantity"),))`
- `repr()` → `"sqrt(datum.quantity)"`

#### 阶段 4: `(datum.price * 1.1) + sqrt(datum.quantity)`

```python
# 执行: BinaryExpression(...) + FunctionExpression(...)
# 实际调用: BinaryExpression.__add__(FunctionExpression(...))
```

**最终的表达式对象树**：

```
BinaryExpression("+")
├── lhs: BinaryExpression("*")
│   ├── lhs: GetAttrExpression("datum", "price")
│   └── rhs: 1.1 (Python 字面量)
└── rhs: FunctionExpression("sqrt")
    └── args: (GetAttrExpression("datum", "quantity"),)
```

**字符串表示**：`"((datum.price * 1.1) + sqrt(datum.quantity))"`

### 1.4 反向运算符处理

当 Python 字面量在左侧时，会调用反向运算符方法：

```python
# 执行: 2 + datum.price
# Python 解释器首先尝试: (2).__add__(datum.price) → NotImplemented
# 然后尝试: datum.price.__radd__(2)

class OperatorMixin:
    def __radd__(self, other):
        comp_value = BinaryExpression("+", other, self)
        return self._from_expr(comp_value)
```
<mcfile name="core.py" path="altair/expr/core.py"></mcfile>

**比较运算符的特殊处理**：

比较运算符的反向操作会交换运算符：

| 原运算符 | 反向运算符 |
|---------|-----------|
| `>` | `<` |
| `<` | `>` |
| `>=` | `<=` |
| `<=` | `>=` |
| `==` | `==` |
| `!=` | `!=` |

**测试验证**：
```python
# 2 > datum.price 实际上会调用 datum.price.__lt__(2)
# 结果: "(datum.price < 2)" 而不是 "(2 > datum.price)"
```
<mcfile name="test_expr.py" path="tests/expr/test_expr.py"></mcfile>

### 1.5 方法链组装流程图

```
┌─────────────────────────────────────────────────────────────────┐
│                    Python 表达式: a + b * c                       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  步骤 1: 确定运算顺序（Python 运算符优先级）                        │
│          先计算 b * c，再计算 a + (b * c)                         │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  步骤 2: b * c                                                    │
│          - b.__mul__(c) 被调用                                    │
│          - 创建 BinaryExpression("*", b, c)                       │
│          - 返回 self._from_expr(BinaryExpression)                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  步骤 3: a + (b * c)                                              │
│          - a.__add__(BinaryExpression) 被调用                     │
│          - 创建 BinaryExpression("+", a, BinaryExpression)        │
│          - 返回 self._from_expr(BinaryExpression)                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  最终结果: 嵌套的表达式对象树                                       │
│          BinaryExpression(                                        │
│              "+",                                                 │
│              a,                                                   │
│              BinaryExpression("*", b, c)                          │
│          )                                                        │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 表达式序列化完整路径

### 2.1 序列化的触发时机

表达式对象的序列化发生在以下场景：

1. **调用 `to_dict()` 方法** - 显式转换为字典
2. **调用 `repr()` 或 `str()`** - 获取字符串表示
3. **图表 `to_dict()` 或 `to_json()`** - 生成 Vega-Lite 规范
4. **嵌套在其他 `SchemaBase` 对象中** - 通过 `_todict()` 递归处理

### 2.2 不同类型的序列化行为

#### 2.2.1 Expression 类的序列化

```python
class Expression(OperatorMixin, SchemaBase):
    _schema = {"type": "string"}

    def to_dict(self, *args, **kwargs):
        return repr(self)
```
<mcfile name="core.py" path="altair/expr/core.py"></mcfile>

**关键特点**：
- `Expression.to_dict()` 返回字符串，而不是字典
- 这是因为 Vega-Lite 规范中，表达式字段直接接受字符串

#### 2.2.2 Parameter 类的序列化

```python
class Parameter(_expr_core.OperatorMixin):
    def to_dict(self) -> dict[str, str | dict[str, Any]]:
        if self.param_type == "variable":
            return {"expr": self.name}
        elif self.param_type == "selection":
            nm: Any = self.name
            return {"param": nm.to_dict() if hasattr(nm, "to_dict") else nm}
        else:
            raise ValueError(f"Unrecognized parameter type: {self.param_type}")
```
<mcfile name="api.py" path="altair/vegalite/v6/api.py"></mcfile>

#### 2.2.3 ParameterExpression 类的序列化

```python
class ParameterExpression(_expr_core.OperatorMixin):
    def __init__(self, expr: IntoExpression) -> None:
        self.expr = expr

    def to_dict(self) -> dict[str, str]:
        return {"expr": repr(self.expr)}

    def _to_expr(self) -> str:
        return repr(self.expr)

    def _from_expr(self, expr: IntoExpression) -> ParameterExpression:
        return ParameterExpression(expr=expr)
```
<mcfile name="api.py" path="altair/vegalite/v6/api.py"></mcfile>

#### 2.2.4 各类表达式序列化对比

| 类型 | `to_dict()` 返回 | `repr()` 返回 | 用途 |
|------|------------------|---------------|------|
| `Expression` | `str` | `str` | 基础表达式 |
| `Parameter` | `dict` | 继承自 `object` | 参数引用 |
| `ParameterExpression` | `dict` | 继承自 `object` | 参数参与运算后的表达式 |
| `SelectionExpression` | `dict` | 继承自 `object` | 选择表达式 |

### 2.3 完整序列化路径

让我们追踪一个完整的序列化流程：

```python
import altair as alt
import pandas as pd

# 创建数据和参数
data = pd.DataFrame({"price": [10, 20, 30], "quantity": [5, 3, 1]})
threshold = alt.param(value=15, name="threshold")

# 构建图表
chart = (
    alt.Chart(data)
    .mark_point()
    .encode(
        x="price:Q",
        y="quantity:Q",
        color=alt.condition(
            alt.datum.price > threshold,  # 表达式
            alt.value("red"),
            alt.value("blue")
        )
    )
    .add_params(threshold)
)

# 触发序列化
spec = chart.to_dict()
```

**序列化追踪**：

#### 阶段 1: 表达式构建

```python
# alt.datum.price > threshold
# 执行: GetAttrExpression("datum", "price").__gt__(threshold)

# 1. 创建 BinaryExpression
BinaryExpression(
    op=">",
    lhs=GetAttrExpression("datum", "price"),
    rhs=Parameter(name="threshold", param_type="variable")
)

# 2. 调用 _from_expr()
# Expression._from_expr 没有重写，返回 BinaryExpression
```

#### 阶段 2: 图表序列化

当调用 `chart.to_dict()` 时：

```
chart.to_dict()
    │
    ▼
SchemaBase.to_dict()
    │
    ▼
_todict(kwds, context, ...)  # 递归转换
    │
    ├── 遇到 dict → 递归处理每个值
    │
    ├── 遇到 SchemaBase → 调用 obj.to_dict()
    │
    ├── 遇到 SchemaLike → 调用 obj.to_dict()
    │       │
    │       ├── ParameterExpression.to_dict()
    │       │       └── {"expr": repr(self.expr)}
    │       │               │
    │       │               └── repr(BinaryExpression)
    │       │                       │
    │       │                       └── BinaryExpression.__repr__()
    │       │                               │
    │       │                               └── _js_repr() 递归处理
    │       │
    │       └── Expression.to_dict()
    │               └── repr(self)
    │
    └── 遇到 dt.date/dt.datetime → _from_date_datetime()
```

### 2.4 _js_repr 函数的递归处理

`_js_repr` 是序列化的核心函数：

```python
def _js_repr(val) -> str:
    """Return a javascript-safe string representation of val."""
    if val is True:
        return "true"
    elif val is False:
        return "false"
    elif val is None:
        return "null"
    elif isinstance(val, OperatorMixin):
        return val._to_expr()  # 关键：递归处理表达式对象
    elif isinstance(val, dt.date):
        return _from_date_datetime(val)
    elif _is_numpy_generic(val):
        return repr(val.item())
    else:
        return repr(val)
```
<mcfile name="core.py" path="altair/expr/core.py"></mcfile>

**关键点**：
- 对于 `OperatorMixin` 子类（包括 `Expression`、`Parameter`、`ParameterExpression` 等）
- 调用 `val._to_expr()` 获取字符串表示

### 2.5 _to_expr 方法的差异

不同类对 `_to_expr` 的实现不同：

| 类 | `_to_expr()` 实现 | 返回值 |
|---|-------------------|--------|
| `OperatorMixin` (基类) | `return repr(self)` | 对象的 `repr()` |
| `Parameter` | **未重写** | `repr(self)` |
| `ParameterExpression` | `return repr(self.expr)` | 内部表达式的 `repr()` |
| `SelectionExpression` | `return repr(self.expr)` | 内部表达式的 `repr()` |

### 2.6 序列化流程图

```
┌─────────────────────────────────────────────────────────────────┐
│  触发: chart.to_dict()                                            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  SchemaBase.to_dict()                                             │
│  - 收集所有 kwds                                                  │
│  - 调用 _todict(kwds, context, ...)                              │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  _todict() 递归处理                                               │
│                                                                   │
│  对每个值:                                                        │
│  ├── SchemaBase → obj.to_dict(validate=False)                   │
│  ├── SchemaLike → obj.to_dict()                                  │
│  ├── dict → 递归处理每个值                                        │
│  ├── list/tuple → 递归处理每个元素                                │
│  └── dt.date/dt.datetime → _from_date_datetime()                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  当遇到 Expression 对象时:                                        │
│                                                                   │
│  1. SchemaLike 检查:                                              │
│     isinstance(obj, SchemaLike) → True (Expression 继承 SchemaBase)│
│     → obj.to_dict()                                               │
│                                                                   │
│  2. Expression.to_dict():                                         │
│     def to_dict(self, *args, **kwargs):                          │
│         return repr(self)                                         │
│                                                                   │
│  3. repr(self) 调用:                                              │
│     BinaryExpression.__repr__()                                   │
│     → f"({_js_repr(self.lhs)} {self.op} {_js_repr(self.rhs)})" │
│                                                                   │
│  4. _js_repr() 递归处理:                                          │
│     - 对于 OperatorMixin 子类 → val._to_expr()                   │
│     - 对于其他值 → 转换为 JavaScript 安全字符串                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. 日期时间与时区处理规则

### 3.1 核心实现

日期时间处理的核心函数是 `_from_date_datetime`：

```python
def _from_date_datetime(obj: dt.date | dt.datetime, /) -> str:
    """
    Parse native `datetime.(date|datetime)` into a `datetime expression`_ string.

    **Month is 0-based**
    """
    fn_name: Literal["datetime", "utc"] = "datetime"
    args: tuple[int, ...] = obj.year, obj.month - 1, obj.day
    
    if isinstance(obj, dt.datetime):
        if tzinfo := obj.tzinfo:
            if tzinfo is dt.timezone.utc:  # ⚠️ 身份比较（is），不是值比较（==）
                fn_name = "utc"
            else:
                msg = (
                    f"Unsupported timezone {tzinfo!r}.\n"
                    "Only `'UTC'` or naive (local) datetimes are permitted.\n"
                    "See https://altair-viz.github.io/user_guide/generated/core/altair.DateTime.html"
                )
                raise TypeError(msg)
        
        us = obj.microsecond
        ms = us if us == 0 else us // 1_000  # 微秒转换为毫秒
        args = *args, obj.hour, obj.minute, obj.second, ms
    
    return FunctionExpression(fn_name, args)._to_expr()
```
<mcfile name="core.py" path="altair/expr/core.py"></mcfile>

### 3.2 处理规则详解

#### 规则 1: date 与 datetime 的区别

| 输入类型 | 参数提取 | 函数调用 |
|----------|----------|----------|
| `dt.date` | `(year, month-1, day)` | `datetime(year, month-1, day)` |
| `dt.datetime` (naive) | `(year, month-1, day, hour, minute, second, ms)` | `datetime(...)` |
| `dt.datetime` (UTC) | `(year, month-1, day, hour, minute, second, ms)` | `utc(...)` |

#### 规则 2: 月份是 0-based 的

**重要**：Vega-Lite 中的月份是 0-based 的（1月 = 0）。

```python
args: tuple[int, ...] = obj.year, obj.month - 1, obj.day  # 月份减 1
```

#### 规则 3: 时区处理（关键修正）

时区检查使用 **身份比较**（`is`），**不是值比较**（`==`）：

```python
if tzinfo is dt.timezone.utc:  # 使用 is，不是 ==
    fn_name = "utc"
else:
    raise TypeError("Unsupported timezone...")
```
<mcfile name="core.py" path="altair/expr/core.py"></mcfile>

**这意味着**：

| 时区对象 | 是否被支持 | 说明 |
|----------|-----------|------|
| `dt.timezone.utc` | ✅ 支持 | 单例对象，身份匹配 |
| `dt.timezone(dt.timedelta(hours=0), "UTC")` | ❌ 不支持 | 新对象，身份不匹配 |
| `dt.timezone(dt.timedelta(hours=8), "CST")` | ❌ 不支持 | 非 UTC 时区 |
| `None` (naive datetime) | ✅ 支持 | 被视为本地时间 |

**测试验证**：

```python
@pytest.mark.parametrize(
    "tzinfo",
    [
        dt.timezone(dt.timedelta(hours=2), "UTC+2"),    # ❌
        dt.timezone(dt.timedelta(hours=1), "BST"),       # ❌
        dt.timezone(dt.timedelta(hours=-7), "pdt"),      # ❌
        dt.timezone(dt.timedelta(hours=-3), "BRT"),      # ❌
        dt.timezone(dt.timedelta(hours=9), "UTC"),       # ❌ 即使偏移为 0，也不是单例
        dt.timezone(dt.timedelta(minutes=60), "utc"),    # ❌ 名称是 "utc"，但不是单例
    ],
)
def test_expr_datetime_unsupported_timezone(tzinfo: dt.timezone) -> None:
    datetime = dt.datetime(2003, 5, 1, 1, 30)
    
    # 带时区的 datetime 抛出错误
    with pytest.raises(TypeError, match=r"Unsupported timezone.+\n.+UTC.+local"):
        datum.date == datetime.replace(tzinfo=tzinfo)
```
<mcfile name="test_expr.py" path="tests/expr/test_expr.py"></mcfile>

#### 规则 4: 微秒转换为毫秒

```python
us = obj.microsecond
ms = us if us == 0 else us // 1_000  # 整数除法，截断而非四舍五入
```

**示例**：
- `2999` 微秒 → `2` 毫秒（不是 `3`）
- `1000` 微秒 → `1` 毫秒

**测试验证**：

```python
(dt.datetime(2001, 1, 1, 9, 30, 0, 2999), "datetime(2001,0,1,9,30,0,2)")
# 2999 微秒 → 2 毫秒（2999 // 1000 = 2）
```
<mcfile name="test_expr.py" path="tests/expr/test_expr.py"></mcfile>

### 3.3 转换示例

#### 示例 1: date 对象

```python
import datetime as dt

d = dt.date(2000, 1, 1)  # 2000年1月1日
result = datum.date >= d
assert repr(result) == "(datum.date >= datetime(2000,0,1))"
```
<mcfile name="test_expr.py" path="tests/expr/test_expr.py"></mcfile>

**注意**：
- `1` 月 → `0`（0-based）
- 只提取年、月、日

#### 示例 2: naive datetime

```python
dt_obj = dt.datetime(2000, 1, 1)  # 2000年1月1日 00:00:00
result = datum.date >= dt_obj
assert repr(result) == "(datum.date >= datetime(2000,0,1,0,0,0,0))"
```
<mcfile name="test_expr.py" path="tests/expr/test_expr.py"></mcfile>

#### 示例 3: 带微秒的 datetime

```python
dt_obj = dt.datetime(2001, 1, 1, 9, 30, 0, 2999)
# 2001年1月1日 9:30:00.002999
result = datum.date >= dt_obj
assert repr(result) == "(datum.date >= datetime(2001,0,1,9,30,0,2))"
# 2999 微秒 → 2 毫秒
```
<mcfile name="test_expr.py" path="tests/expr/test_expr.py"></mcfile>

#### 示例 4: UTC 时区的 datetime

```python
dt_obj = dt.datetime(2003, 5, 1, 1, 30, tzinfo=dt.timezone.utc)
result = datum.date >= dt_obj
assert repr(result) == "(datum.date >= utc(2003,4,1,1,30,0,0))"
# 注意：使用 utc() 函数，不是 datetime()
```
<mcfile name="test_expr.py" path="tests/expr/test_expr.py"></mcfile>

### 3.4 日期时间处理流程图

```
┌─────────────────────────────────────────────────────────────────┐
│  输入: dt.date 或 dt.datetime 对象                                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  步骤 1: 提取基础参数                                             │
│          args = (year, month-1, day)                            │
│          ⚠️ 月份减 1（0-based）                                   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  步骤 2: 检查是否是 dt.datetime                                   │
│          isinstance(obj, dt.datetime)?                           │
└─────────────────────────────────────────────────────────────────┘
         │                    │
         │ Yes                │ No (dt.date)
         ▼                    ▼
┌──────────────────┐    ┌─────────────────────────────────────┐
│ 步骤 3: 处理时区 │    │ 直接返回: datetime(year, month-1, │
│                  │    │            day)                      │
└──────────────────┘    └─────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  obj.tzinfo 是否为 None?                                          │
└─────────────────────────────────────────────────────────────────┘
         │                    │
         │ None (naive)       │ 有时区
         ▼                    ▼
┌──────────────────┐    ┌─────────────────────────────────────┐
│ fn_name =        │    │ tzinfo is dt.timezone.utc?          │
│ "datetime"       │    │ ⚠️ 使用 is 比较，不是 ==             │
└──────────────────┘    └─────────────────────────────────────┘
                              │                    │
                              │ Yes                │ No
                              ▼                    ▼
                       ┌──────────┐      ┌──────────────────────┐
                       │ fn_name =│      │ 抛出 TypeError        │
                       │ "utc"    │      │ 仅支持 UTC 或 naive  │
                       └──────────┘      └──────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  步骤 4: 提取时间参数                                             │
│          us = obj.microsecond                                    │
│          ms = us if us == 0 else us // 1_000  # 截断，不是四舍五入│
│          args = (year, month-1, day, hour, minute, second, ms) │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  步骤 5: 生成函数调用                                             │
│          FunctionExpression(fn_name, args)._to_expr()           │
│          → "fn_name(arg1, arg2, ...)"                           │
└─────────────────────────────────────────────────────────────────┘
```

### 3.5 常见问题与修正

#### 问题 1: 时区比较使用 `is` 而非 `==`

**之前的误解**：认为值为 UTC 偏移的时区都被支持。

**实际行为**：只有 `dt.timezone.utc` 单例被支持。

```python
# ✅ 支持
dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)

# ❌ 不支持（即使偏移为 0）
dt.datetime(2024, 1, 1, tzinfo=dt.timezone(dt.timedelta(hours=0), "UTC"))

# ❌ 不支持
dt.datetime(2024, 1, 1, tzinfo=dt.timezone(dt.timedelta(hours=8), "CST"))
```

**原因**：使用 `is` 进行身份比较：
```python
if tzinfo is dt.timezone.utc:  # 身份比较
```

#### 问题 2: 月份是 0-based 的

**容易出错的地方**：

```python
# 2000年1月1日
dt.date(2000, 1, 1)  # Python: 1 月 = 1
# 转换为: datetime(2000, 0, 1)  # Vega-Lite: 1 月 = 0
```

#### 问题 3: 微秒截断为毫秒

```python
# 2999 微秒
dt.datetime(2001, 1, 1, 9, 30, 0, 2999)
# 转换为: datetime(..., 2)  # 2999 // 1000 = 2（截断）
# 不是: datetime(..., 3)  # 不是四舍五入
```

---

## 4. 跨模块类型保持机制

### 4.1 _from_expr 方法的作用

`_from_expr` 方法是实现类型保持的核心机制：

```python
class OperatorMixin:
    def _from_expr(self, expr) -> Any:
        return expr  # 基类：直接返回表达式
```
<mcfile name="core.py" path="altair/expr/core.py"></mcfile>

### 4.2 不同类的 _from_expr 实现

| 类 | `_from_expr` 实现 | 返回类型 | 目的 |
|---|-------------------|----------|------|
| `OperatorMixin` | `return expr` | 原表达式类型 | 基类行为 |
| `Expression` | **未重写** | 原表达式类型 | 保持为 Expression |
| `Parameter` | `return ParameterExpression(expr=expr)` | `ParameterExpression` | 保持参数语义 |
| `ParameterExpression` | `return ParameterExpression(expr=expr)` | `ParameterExpression` | 保持类型 |
| `SelectionExpression` | `return SelectionExpression(expr=expr)` | `SelectionExpression` | 保持类型 |

### 4.3 类型保持的工作流程

让我们追踪 `Parameter` 参与运算时的类型转换：

```python
height_var = alt.param(name="height")  # Parameter
result = height_var / 2                 # 结果类型？
```

**执行流程**：

```
步骤 1: 调用 height_var.__truediv__(2)
        ↓
步骤 2: OperatorMixin.__truediv__ 被执行
        → 创建 BinaryExpression("/", height_var, 2)
        ↓
步骤 3: 调用 self._from_expr(BinaryExpression)
        ↓
步骤 4: Parameter._from_expr 被执行
        → return ParameterExpression(expr=BinaryExpression)
        ↓
步骤 5: 返回 ParameterExpression 对象
```

**代码实现**：

```python
class Parameter(_expr_core.OperatorMixin):
    def _from_expr(self, expr: IntoExpression) -> ParameterExpression:
        return ParameterExpression(expr=expr)
```
<mcfile name="api.py" path="altair/vegalite/v6/api.py"></mcfile>

```python
class ParameterExpression(_expr_core.OperatorMixin):
    def __init__(self, expr: IntoExpression) -> None:
        self.expr = expr

    def to_dict(self) -> dict[str, str]:
        return {"expr": repr(self.expr)}

    def _to_expr(self) -> str:
        return repr(self.expr)

    def _from_expr(self, expr: IntoExpression) -> ParameterExpression:
        return ParameterExpression(expr=expr)
```
<mcfile name="api.py" path="altair/vegalite/v6/api.py"></mcfile>

### 4.4 类型保持的意义

为什么需要 `ParameterExpression` 类型？

**序列化差异**：

```python
# Expression.to_dict() 返回字符串
class Expression:
    def to_dict(self, *args, **kwargs):
        return repr(self)  # 直接返回字符串

# ParameterExpression.to_dict() 返回字典
class ParameterExpression:
    def to_dict(self) -> dict[str, str]:
        return {"expr": repr(self.expr)}  # 返回字典
```

**Vega-Lite 规范差异**：

```json
// Expression 直接作为字符串
{
  "filter": "datum.price > 100"
}

// Parameter 相关表达式作为 {"expr": "..."}
{
  "size": {"expr": "(height / 2)"}
}
```

### 4.5 类型转换流程图

```
┌─────────────────────────────────────────────────────────────────┐
│  场景 1: Expression 参与运算                                       │
│  datum.price * 1.1                                                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  GetAttrExpression.__mul__(1.1)                                  │
│  → 创建 BinaryExpression("*", GetAttrExpression, 1.1)           │
│  → 调用 self._from_expr(BinaryExpression)                         │
│  → Expression._from_expr 未重写，返回 BinaryExpression            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  结果类型: BinaryExpression                                       │
│  to_dict() → "(datum.price * 1.1)"  (字符串)                     │
└─────────────────────────────────────────────────────────────────┘

───────────────────────────────────────────────────────────────────

┌─────────────────────────────────────────────────────────────────┐
│  场景 2: Parameter 参与运算                                        │
│  height_var / 2                                                   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Parameter.__truediv__(2)                                        │
│  → 创建 BinaryExpression("/", Parameter, 2)                       │
│  → 调用 self._from_expr(BinaryExpression)                         │
│  → Parameter._from_expr 返回 ParameterExpression                   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  结果类型: ParameterExpression                                    │
│  to_dict() → {"expr": "(height / 2)"}  (字典)                    │
└─────────────────────────────────────────────────────────────────┘

───────────────────────────────────────────────────────────────────

┌─────────────────────────────────────────────────────────────────┐
│  场景 3: ParameterExpression 参与运算                              │
│  (height_var / 2) + 10                                            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  ParameterExpression.__add__(10)                                 │
│  → 创建 BinaryExpression("+", ParameterExpression, 10)           │
│  → 调用 self._from_expr(BinaryExpression)                         │
│  → ParameterExpression._from_expr 返回 ParameterExpression         │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  结果类型: ParameterExpression (保持类型)                         │
│  to_dict() → {"expr": "((height / 2) + 10)"}                     │
└─────────────────────────────────────────────────────────────────┘
```

### 4.6 混合类型运算

当 `Expression` 和 `Parameter` 混合运算时会发生什么？

```python
# 场景 1: datum.price > threshold
# datum.price → GetAttrExpression (Expression 子类)
# threshold → Parameter

# 执行: datum.price.__gt__(threshold)
# → 创建 BinaryExpression(">", GetAttrExpression, Parameter)
# → 调用 self._from_expr(BinaryExpression)
# → Expression._from_expr 未重写，返回 BinaryExpression

# 结果类型: BinaryExpression
# to_dict() → "(datum.price > threshold)" (字符串)
```

**但是**，如果是 `threshold > datum.price`：

```python
# 场景 2: threshold > datum.price
# 执行: threshold.__gt__(datum.price)
# → 创建 BinaryExpression(">", threshold, GetAttrExpression)
# → 调用 self._from_expr(BinaryExpression)
# → Parameter._from_expr 返回 ParameterExpression

# 结果类型: ParameterExpression
# to_dict() → {"expr": "(threshold > datum.price)"} (字典)
```

**关键发现**：结果类型取决于**左操作数**的 `_from_expr` 实现。

---

## 5. 关键设计模式总结

### 5.1 混入类模式 (Mixin Pattern)

**应用场景**：`OperatorMixin` 为多个类提供运算符重载能力。

```
                    ┌──────────────────┐
                    │  OperatorMixin   │
                    │  (运算符能力)     │
                    └────────┬─────────┘
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
          ▼                  ▼                  ▼
   ┌────────────┐    ┌────────────┐    ┌────────────────┐
   │ Expression │    │ Parameter  │    │ ParameterExpr  │
   │ (SchemaBase)│    │            │    │                │
   └────────────┘    └────────────┘    └────────────────┘
```

**优点**：
- 代码复用
- 灵活组合
- 避免多重继承复杂性

### 5.2 解释器模式 (Interpreter Pattern)

**应用场景**：表达式对象树的构建和求值（字符串化）。

| 角色 | 类 | 职责 |
|------|-----|------|
| 抽象表达式 | `Expression` | 定义 `to_dict()` 和 `__repr__()` 接口 |
| 终结符表达式 | `GetAttrExpression`, `GetItemExpression`, `ConstExpression` | 表示基本数据访问和常量 |
| 非终结符表达式 | `UnaryExpression`, `BinaryExpression`, `FunctionExpression` | 表示复合运算 |
| 上下文 | 各种 Python 值 | 被操作的对象 |

**表达式求值**（字符串化）：
```python
# 递归求值
class BinaryExpression(Expression):
    def __repr__(self):
        return f"({_js_repr(self.lhs)} {self.op} {_js_repr(self.rhs)})"

def _js_repr(val) -> str:
    if isinstance(val, OperatorMixin):
        return val._to_expr()  # 递归调用
    # ... 其他处理
```

### 5.3 工厂方法模式 (Factory Method Pattern)

**应用场景**：`_from_expr` 方法根据对象类型创建合适的表达式包装。

```python
class Parameter(_expr_core.OperatorMixin):
    # 工厂方法
    def _from_expr(self, expr: IntoExpression) -> ParameterExpression:
        return ParameterExpression(expr=expr)  # 创建具体产品
```

**产品层次**：
- `Parameter._from_expr` → 创建 `ParameterExpression`
- `ParameterExpression._from_expr` → 创建 `ParameterExpression`（保持类型）
- `SelectionExpression._from_expr` → 创建 `SelectionExpression`

### 5.4 单例模式 (Singleton Pattern)

**应用场景**：`datum` 和 `Undefined` 对象。

```python
class DatumType:
    def __getattr__(self, attr) -> GetAttrExpression:
        return GetAttrExpression("datum", attr)

datum = DatumType()  # 单例实例
```

```python
class UndefinedType:
    __instance = None
    
    def __new__(cls, *args, **kwargs) -> Self:
        if not isinstance(cls.__instance, cls):
            cls.__instance = object.__new__(cls, *args, **kwargs)
        return cls.__instance

Undefined = UndefinedType()  # 单例实例
```
<mcfile name="schemapi.py" path="altair/utils/schemapi.py"></mcfile>

---

## 附录：完整转换速查表

### A.1 Python 表达式 → Vega-Lite 表达式

| Python 表达式 | Vega-Lite 表达式 |
|--------------|------------------|
| `datum.price` | `"datum.price"` |
| `datum["price"]` | `"datum['price']"` |
| `datum.price * 1.1` | `"(datum.price * 1.1)"` |
| `datum.price + datum.quantity` | `"(datum.price + datum.quantity)"` |
| `datum.price ** 2` | `"pow(datum.price,2)"` |
| `datum.price == 100` | `"(datum.price === 100)"` |
| `datum.price != 100` | `"(datum.price !== 100)"` |
| `datum.price > 100` | `"(datum.price > 100)"` |
| `datum.price <= 100` | `"(datum.price <= 100)"` |
| `-datum.price` | `"(-datum.price)"` |
| `+datum.price` | `"(+datum.price)"` |
| `not datum.active` | `"(!datum.active)"` |
| `abs(datum.value)` | `"abs(datum.value)"` |
| `datum.a and datum.b` | `"(datum.a && datum.b)"` |
| `datum.a or datum.b` | `"(datum.a \|\| datum.b)"` |

### A.2 函数调用转换

| Python 调用 | Vega-Lite 表达式 |
|------------|------------------|
| `alt.expr.sqrt(datum.x)` | `"sqrt(datum.x)"` |
| `alt.expr.if_(cond, a, b)` | `"if(cond,a,b)"` |
| `alt.expr.pow(a, b)` | `"pow(a,b)"` |
| `alt.expr.round(datum.x)` | `"round(datum.x)"` |
| `alt.expr.month(date)` | `"month(date)"` |
| `alt.expr.year(date)` | `"year(date)"` |
| `alt.expr.upper(s)` | `"upper(s)"` |
| `alt.expr.lower(s)` | `"lower(s)"` |

### A.3 日期时间转换

| Python 对象 | Vega-Lite 表达式 |
|-------------|------------------|
| `dt.date(2000, 1, 1)` | `"datetime(2000,0,1)"` |
| `dt.datetime(2000, 1, 1)` | `"datetime(2000,0,1,0,0,0,0)"` |
| `dt.datetime(2000, 1, 1, 9, 30)` | `"datetime(2000,0,1,9,30,0,0)"` |
| `dt.datetime(2000, 1, 1, 9, 30, 0, 2999)` | `"datetime(2000,0,1,9,30,0,2)"` |
| `dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)` | `"utc(2000,0,1,0,0,0,0)"` |

### A.4 参数表达式转换

| Python 代码 | 结果类型 | `to_dict()` 结果 |
|-------------|----------|------------------|
| `alt.param(name="h")` | `Parameter` | `{"expr": "h"}` |
| `alt.param(name="h") / 2` | `ParameterExpression` | `{"expr": "(h / 2)"}` |
| `(alt.param(name="h") / 2) + 10` | `ParameterExpression` | `{"expr": "((h / 2) + 10)"}` |

---

## 关键修正要点

### 1. 时区处理修正

**之前的误解**：认为值为 UTC 偏移的时区都被支持。

**实际行为**：只有 `dt.timezone.utc` 单例被支持，使用**身份比较**（`is`）而非**值比较**（`==`）。

```python
# ✅ 支持
dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)

# ❌ 不支持（即使偏移为 0）
dt.datetime(2024, 1, 1, tzinfo=dt.timezone(dt.timedelta(hours=0), "UTC"))
```

### 2. 混合类型运算修正

**关键发现**：当 `Expression` 和 `Parameter` 混合运算时，结果类型取决于**左操作数**的 `_from_expr` 实现。

```python
# 场景 1: Expression 在左侧
datum.price > threshold  # → BinaryExpression (to_dict 返回字符串)

# 场景 2: Parameter 在左侧
threshold > datum.price  # → ParameterExpression (to_dict 返回字典)
```

### 3. 微秒处理修正

**微秒转换为毫秒时使用**：**截断**（整数除法 `//`），**不是四舍五入**。

```python
us = obj.microsecond
ms = us if us == 0 else us // 1_000  # 2999 // 1000 = 2
```

---

## 参考文件

- `altair/expr/core.py` - 核心表达式类、运算符实现、日期时间处理
- `altair/expr/__init__.py` - `expr` 工具类和自动生成的函数方法
- `altair/vegalite/v6/api.py` - `Parameter`、`ParameterExpression`、`SelectionExpression` 实现
- `altair/utils/schemapi.py` - `SchemaBase`、`_todict()`、序列化机制
- `tests/expr/test_expr.py` - 表达式测试用例
- `tests/vegalite/v6/test_params.py` - 参数表达式测试用例
