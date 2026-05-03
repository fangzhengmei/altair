# Altair 表达式 DSL 分析报告

## 1. 概述

Altair 的表达式 DSL（领域特定语言）是一个精心设计的系统，允许用户使用 Python 语法构建 Vega-Lite 表达式。该系统通过运算符重载、方法链和函数调用的组合，将 Python 代码转换为 Vega-Lite 可执行的 JavaScript 表达式字符串。

## 2. 核心架构

### 2.1 表达式类层次结构

表达式系统的核心类定义在 `altair/expr/core.py` 中，主要类层次结构如下：

```
OperatorMixin (运算符混入类)
    └── Expression (表达式基类)
            ├── UnaryExpression (一元表达式)
            ├── BinaryExpression (二元表达式)
            ├── FunctionExpression (函数调用表达式)
            ├── ConstExpression (常量表达式)
            ├── GetAttrExpression (属性访问表达式)
            └── GetItemExpression (索引访问表达式)
```

### 2.2 核心组件职责

| 类名 | 职责 | 关键方法 |
|------|------|----------|
| `OperatorMixin` | 提供所有运算符重载能力 | `__add__`, `__sub__`, `__mul__` 等 |
| `Expression` | 表达式基类，继承 SchemaBase | `to_dict()`, `__getitem__` |
| `UnaryExpression` | 处理一元运算符（`-`, `+`, `!`） | `__repr__()` |
| `BinaryExpression` | 处理二元运算符（`+`, `-`, `*`, `/` 等） | `__repr__()` |
| `FunctionExpression` | 处理函数调用 | `__repr__()` |
| `ConstExpression` | 表示 JavaScript 常量（NaN, PI 等） | `__repr__()` |
| `GetAttrExpression` | 处理属性访问（`datum.field`） | `__repr__()` |
| `GetItemExpression` | 处理索引访问（`datum["field"]`） | `__repr__()` |

## 3. 运算符重载机制

### 3.1 OperatorMixin 核心实现

`OperatorMixin` 是整个表达式系统的基石，它通过重载 Python 的特殊方法来捕获运算符操作：

```python
class OperatorMixin:
    def _to_expr(self) -> str:
        return repr(self)

    def _from_expr(self, expr) -> Any:
        return expr

    # 算术运算符
    def __add__(self, other):
        comp_value = BinaryExpression("+", self, other)
        return self._from_expr(comp_value)

    def __radd__(self, other):
        comp_value = BinaryExpression("+", other, self)
        return self._from_expr(comp_value)

    def __sub__(self, other):
        comp_value = BinaryExpression("-", self, other)
        return self._from_expr(comp_value)

    # ... 更多运算符 ...

    # 比较运算符
    def __eq__(self, other):
        comp_value = BinaryExpression("===", self, other)
        return self._from_expr(comp_value)

    def __ne__(self, other):
        comp_value = BinaryExpression("!==", self, other)
        return self._from_expr(comp_value)

    # 逻辑运算符
    def __and__(self, other):
        comp_value = BinaryExpression("&&", self, other)
        return self._from_expr(comp_value)

    def __or__(self, other):
        comp_value = BinaryExpression("||", self, other)
        return self._from_expr(comp_value)

    def __invert__(self):
        comp_value = UnaryExpression("!", self)
        return self._from_expr(comp_value)
```
<mcfile name="core.py" path="altair/expr/core.py"></mcfile>

### 3.2 特殊运算符处理

**幂运算符（`**`）的特殊处理**：

```python
def __pow__(self, other):
    # "**" Javascript operator is not supported in all browsers
    comp_value = FunctionExpression("pow", (self, other))
    return self._from_expr(comp_value)
```
<mcfile name="core.py" path="altair/expr/core.py"></mcfile>

注意：幂运算符 `**` 不直接使用 JavaScript 的 `**` 运算符，而是转换为 `pow()` 函数调用，以确保跨浏览器兼容性。

**绝对值运算符（`abs()`）**：

```python
def __abs__(self):
    comp_value = FunctionExpression("abs", (self,))
    return self._from_expr(comp_value)
```
<mcfile name="core.py" path="altair/expr/core.py"></mcfile>

### 3.3 运算符映射表

| Python 运算符 | JavaScript 运算符 | 表达式类型 |
|--------------|-------------------|-----------|
| `+` | `+` | BinaryExpression |
| `-` | `-` | BinaryExpression |
| `*` | `*` | BinaryExpression |
| `/` | `/` | BinaryExpression |
| `%` | `%` | BinaryExpression |
| `**` | `pow()` | FunctionExpression |
| `-x` | `-x` | UnaryExpression |
| `+x` | `+x` | UnaryExpression |
| `==` | `===` | BinaryExpression |
| `!=` | `!==` | BinaryExpression |
| `>` | `>` | BinaryExpression |
| `<` | `<` | BinaryExpression |
| `>=` | `>=` | BinaryExpression |
| `<=` | `<=` | BinaryExpression |
| `and` | `&&` | BinaryExpression |
| `or` | `\|\|` | BinaryExpression |
| `not x` | `!x` | UnaryExpression |
| `abs(x)` | `abs()` | FunctionExpression |

## 4. 数据访问机制

### 4.1 DatumType 单例

`datum` 是一个特殊的单例对象，用于访问数据字段：

```python
class DatumType:
    """An object to assist in building Vega-Lite Expressions."""

    def __repr__(self) -> str:
        return "datum"

    def __getattr__(self, attr) -> GetAttrExpression:
        if attr.startswith("__") and attr.endswith("__"):
            raise AttributeError(attr)
        return GetAttrExpression("datum", attr)

    def __getitem__(self, attr) -> GetItemExpression:
        return GetItemExpression("datum", attr)

    def __call__(self, datum, **kwargs) -> dict[str, Any]:
        """Specify a datum for use in an encoding."""
        return dict(datum=datum, **kwargs)

datum = DatumType()
```
<mcfile name="core.py" path="altair/expr/core.py"></mcfile>

### 4.2 属性访问表达式

**GetAttrExpression**：

```python
class GetAttrExpression(Expression):
    def __init__(self, group, name) -> None:
        super().__init__(group=group, name=name)

    def __repr__(self):
        return f"{self.group}.{self.name}"
```
<mcfile name="core.py" path="altair/expr/core.py"></mcfile>

**GetItemExpression**：

```python
class GetItemExpression(Expression):
    def __init__(self, group, name) -> None:
        super().__init__(group=group, name=name)

    def __repr__(self) -> str:
        return f"{self.group}[{self.name!r}]"
```
<mcfile name="core.py" path="altair/expr/core.py"></mcfile>

### 4.3 使用示例

```python
# 属性访问
datum.price  # 生成 "datum.price"

# 索引访问（适用于包含特殊字符的字段名）
datum["sales data"]  # 生成 "datum['sales data']"

# 链式访问
datum.product.category  # 生成 "datum.product.category"
```

## 5. 函数调用机制

### 5.1 表达式函数类结构

函数调用通过 `FunctionExpression` 类处理：

```python
class FunctionExpression(Expression):
    def __init__(self, name, args) -> None:
        super().__init__(name=name, args=args)

    def __repr__(self):
        args = ",".join(_js_repr(arg) for arg in self.args)
        return f"{self.name}({args})"
```
<mcfile name="core.py" path="altair/expr/core.py"></mcfile>

### 5.2 expr 工具类

`expr` 类是用户调用 Vega 表达式函数的主要接口，定义在 `altair/expr/__init__.py` 中。这个类使用元类 `_ExprMeta` 来提供 JavaScript 常量，并通过大量的类方法来封装 Vega 表达式函数。

**元类提供的常量**：

```python
class _ExprMeta(type):
    @property
    def NaN(cls) -> Expression:
        """Not a number (same as JavaScript literal NaN)."""
        return ConstExpression("NaN")

    @property
    def PI(cls) -> Expression:
        """The transcendental number pi (alias to Math.PI)."""
        return ConstExpression("PI")

    # ... 其他常量
```
<mcfile name="__init__.py" path="altair/expr/__init__.py"></mcfile>

**函数调用示例**（自动生成的代码）：

```python
@classmethod
def if_(
    cls,
    test: IntoExpression,
    thenValue: IntoExpression,
    elseValue: IntoExpression,
    /,
) -> Expression:
    """
    If ``test`` is truthy, returns ``thenValue``.
    Otherwise, returns ``elseValue``.
    """
    return FunctionExpression("if", (test, thenValue, elseValue))

@classmethod
def pow(cls, value: IntoExpression, exponent: IntoExpression, /) -> Expression:
    return FunctionExpression("pow", (value, exponent))
```
<mcfile name="__init__.py" path="altair/expr/__init__.py"></mcfile>

### 5.3 自动代码生成

`expr` 类的方法是通过 `tools/vega_expr.py` 脚本自动从 Vega 官方文档生成的：

```python
def write_expr_module(version: str, output: Path, *, header: str) -> None:
    """Parse an ``expressions.md`` into a ``.py`` module."""
    version = version if version.startswith("v") else f"v{version}"
    url = EXPRESSIONS_URL_TEMPLATE.format(version=version)
    
    # 解析表达式文档
    expr_defs = parse_expressions(url)
    
    # 生成代码
    contents = chain(
        content,
        (expr_def.render() for expr_def in parse_expressions(url)),
        [MODULE_POST],
    )
    ruff.write_lint_format(output, contents)
```
<mcfile name="vega_expr.py" path="tools/vega_expr.py"></mcfile>

### 5.4 函数分类

Vega 表达式函数按功能可分为以下几类：

| 类别 | 函数示例 | 用途 |
|------|----------|------|
| **类型检查** | `isArray`, `isBoolean`, `isNumber`, `isString` | 检查值的类型 |
| **类型转换** | `toBoolean`, `toNumber`, `toString`, `toDate` | 类型转换 |
| **数学函数** | `abs`, `ceil`, `floor`, `round`, `sqrt`, `pow` | 数学运算 |
| **三角函数** | `sin`, `cos`, `tan`, `asin`, `acos`, `atan` | 三角函数运算 |
| **统计函数** | `max`, `min`, `clamp`, `extent` | 统计和范围操作 |
| **日期时间** | `datetime`, `year`, `month`, `date`, `hours` | 日期时间处理 |
| **字符串** | `lower`, `upper`, `trim`, `replace`, `split` | 字符串操作 |
| **数组** | `length`, `indexof`, `slice`, `join`, `reverse` | 数组操作 |
| **颜色** | `rgb`, `hsl`, `lab`, `hcl`, `luminance` | 颜色处理 |
| **逻辑** | `if_`, `isValid`, `isDefined` | 逻辑和条件判断 |

## 6. 表达式字符串化

### 6.1 _js_repr 核心函数

`_js_repr` 函数负责将 Python 值转换为 JavaScript 安全的字符串表示：

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
        return val._to_expr()
    elif isinstance(val, dt.date):
        return _from_date_datetime(val)
    elif _is_numpy_generic(val):
        return repr(val.item())
    else:
        return repr(val)
```
<mcfile name="core.py" path="altair/expr/core.py"></mcfile>

### 6.2 日期时间处理

Python 的 `datetime` 对象会被转换为 Vega 的日期时间表达式：

```python
def _from_date_datetime(obj: dt.date | dt.datetime, /) -> str:
    """Parse native `datetime.(date|datetime)` into a `datetime expression`_ string."""
    fn_name: Literal["datetime", "utc"] = "datetime"
    args: tuple[int, ...] = obj.year, obj.month - 1, obj.day
    
    if isinstance(obj, dt.datetime):
        if tzinfo := obj.tzinfo:
            if tzinfo is dt.timezone.utc:
                fn_name = "utc"
            else:
                raise TypeError("Only UTC or naive datetimes are permitted.")
        
        us = obj.microsecond
        ms = us if us == 0 else us // 1_000
        args = *args, obj.hour, obj.minute, obj.second, ms
    
    return FunctionExpression(fn_name, args)._to_expr()
```
<mcfile name="core.py" path="altair/expr/core.py"></mcfile>

**注意**：
- 月份是 0 基的（1 月 = 0）
- 支持 UTC 和本地时区的 datetime
- 微秒会被转换为毫秒

### 6.3 各类表达式的字符串化

**UnaryExpression**：
```python
def __repr__(self):
    return f"({self.op}{_js_repr(self.val)})"
```
示例：`-datum.price` → `(-datum.price)`

**BinaryExpression**：
```python
def __repr__(self):
    return f"({_js_repr(self.lhs)} {self.op} {_js_repr(self.rhs)})"
```
示例：`datum.price * 1.1` → `(datum.price * 1.1)`

**FunctionExpression**：
```python
def __repr__(self):
    args = ",".join(_js_repr(arg) for arg in self.args)
    return f"{self.name}({args})"
```
示例：`expr.if_(datum.price > 100, "high", "low")` → `if((datum.price > 100),'high','low')`

### 6.4 表达式序列化（to_dict）

`Expression` 类重写了 `to_dict` 方法，使其在 Vega-Lite 规范中直接序列化为字符串：

```python
class Expression(OperatorMixin, SchemaBase):
    _schema = {"type": "string"}

    def to_dict(self, *args, **kwargs):
        return repr(self)
```
<mcfile name="core.py" path="altair/expr/core.py"></mcfile>

## 7. 跨模块表达式构建

### 7.1 Parameter 类集成

`Parameter` 类（定义在 `altair/vegalite/v6/api.py`）继承自 `OperatorMixin`，使其能够直接参与表达式构建：

```python
class Parameter(_expr_core.OperatorMixin):
    _schema: t.ClassVar[_TypeMap[Literal["object"]]] = {"type": "object"}

    def __init__(
        self,
        name: str | None = None,
        empty: Optional[bool] = Undefined,
        param: Optional[
            VariableParameter | TopLevelSelectionParameter | SelectionParameter
        ] = Undefined,
        param_type: Optional[Literal["variable", "selection"]] = Undefined,
    ) -> None:
        # ... 初始化代码 ...

    def to_dict(self) -> dict[str, str | dict[str, Any]]:
        if self.param_type == "variable":
            return {"expr": self.name}
        elif self.param_type == "selection":
            nm: Any = self.name
            return {"param": nm.to_dict() if hasattr(nm, "to_dict") else nm}
        else:
            raise ValueError(f"Unrecognized parameter type: {self.param_type}")

    def _from_expr(self, expr: IntoExpression) -> ParameterExpression:
        return ParameterExpression(expr=expr)
```
<mcfile name="api.py" path="altair/vegalite/v6/api.py"></mcfile>

### 7.2 类型保持机制（_from_expr）

`OperatorMixin` 中的 `_from_expr` 方法是实现类型保持的关键：

```python
class OperatorMixin:
    def _from_expr(self, expr) -> Any:
        return expr
```
<mcfile name="core.py" path="altair/expr/core.py"></mcfile>

子类可以重写此方法以保持自身类型：

**ParameterExpression**：
```python
class ParameterExpression(_expr_core.OperatorMixin):
    _schema: t.ClassVar[_TypeMap[Literal["object"]]] = {"type": "object"}

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

**SelectionExpression**：
```python
class SelectionExpression(_expr_core.OperatorMixin):
    _schema: t.ClassVar[_TypeMap[Literal["object"]]] = {"type": "object"}

    def __init__(self, expr: IntoExpression) -> None:
        self.expr = expr

    def to_dict(self) -> dict[str, str]:
        return {"expr": repr(self.expr)}

    def _to_expr(self) -> str:
        return repr(self.expr)

    def _from_expr(self, expr: IntoExpression) -> SelectionExpression:
        return SelectionExpression(expr=expr)
```
<mcfile name="api.py" path="altair/vegalite/v6/api.py"></mcfile>

### 7.3 字段访问机制

`Parameter` 类还支持字段访问，用于选择参数：

```python
def __getattr__(self, field_name: str) -> GetAttrExpression | SelectionExpression:
    if field_name.startswith("__") and field_name.endswith("__"):
        raise AttributeError(field_name)
    _attrexpr = _expr_core.GetAttrExpression(self.name, field_name)
    
    # 如果是 SelectionParameter 且字段在 fields 或 encodings 列表中
    if check_fields_and_encodings(self, field_name):
        return SelectionExpression(_attrexpr)
    else:
        return _attrexpr

def __getitem__(self, field_name: str) -> GetItemExpression:
    return _expr_core.GetItemExpression(self.name, field_name)
```
<mcfile name="api.py" path="altair/vegalite/v6/api.py"></mcfile>

### 7.4 类型层次关系图

```
OperatorMixin (核心混入类)
│
├── Expression (表达式基类)
│   ├── UnaryExpression
│   ├── BinaryExpression
│   ├── FunctionExpression
│   ├── ConstExpression
│   ├── GetAttrExpression
│   └── GetItemExpression
│
├── Parameter (参数类 - api.py)
│   └── _from_expr → ParameterExpression
│
├── ParameterExpression (参数表达式 - api.py)
│   └── _from_expr → ParameterExpression
│
└── SelectionExpression (选择表达式 - api.py)
    └── _from_expr → SelectionExpression
```

### 7.5 跨模块交互示例

```python
import altair as alt

# 创建参数
param_width = alt.param(bind=alt.binding_range(min=100, max=300), name="param_width")

# 参数参与表达式运算（保持 ParameterExpression 类型）
expr_result = param_width < 200  # 类型: ParameterExpression
print(type(expr_result))  # <class 'altair.vegalite.v6.api.ParameterExpression'>

# 嵌套函数调用
color_expr = alt.expr.if_(param_width < 200, "red", "black")
print(color_expr)  # if((param_width < 200),'red','black')

# 选择参数字段访问
brush = alt.selection_interval(name="brush")
field_expr = brush.price  # 访问选择中的 price 字段
print(type(field_expr))  # <class 'altair.vegalite.v6.api.SelectionExpression'>
```

## 8. 完整工作流程示例

### 8.1 示例：复杂表达式构建

让我们通过一个完整的示例来追踪表达式从 Python 代码到 Vega-Lite 字符串的转换过程：

```python
import altair as alt
from datetime import datetime

# 1. 基础数据访问
price = alt.datum.price
quantity = alt.datum.quantity

# 2. 算术运算
total = price * quantity
discounted = total * 0.9

# 3. 条件判断
is_high_value = discounted > 1000
category = alt.expr.if_(is_high_value, "high", "low")

# 4. 日期处理
current_date = datetime(2024, 3, 15)
date_expr = alt.expr.month(current_date)

# 5. 参数交互
threshold = alt.param(value=100, name="threshold")
adjusted = discounted - threshold

# 6. 组合表达式
final_expr = alt.expr.if_(
    adjusted > 0,
    alt.expr.round(adjusted),
    0
)
```

### 8.2 表达式转换追踪

让我们追踪 `final_expr` 的构建过程：

**步骤 1：`price * quantity`**
- 调用 `OperatorMixin.__mul__(price, quantity)`
- 创建 `BinaryExpression("*", GetAttrExpression("datum", "price"), GetAttrExpression("datum", "quantity"))`
- 字符串化结果：`(datum.price * datum.quantity)`

**步骤 2：`total * 0.9`**
- 调用 `OperatorMixin.__mul__(BinaryExpression(...), 0.9)`
- 创建 `BinaryExpression("*", BinaryExpression(...), 0.9)`
- 字符串化结果：`((datum.price * datum.quantity) * 0.9)`

**步骤 3：`discounted > 1000`**
- 调用 `OperatorMixin.__gt__(BinaryExpression(...), 1000)`
- 创建 `BinaryExpression(">", BinaryExpression(...), 1000)`
- 字符串化结果：`(((datum.price * datum.quantity) * 0.9) > 1000)`

**步骤 4：`alt.expr.if_(is_high_value, "high", "low")`**
- 调用 `expr.if_` 类方法
- 创建 `FunctionExpression("if", (BinaryExpression(...), "high", "low"))`
- 字符串化结果：`if((((datum.price * datum.quantity) * 0.9) > 1000),'high','low')`

### 8.3 参数表达式转换

当涉及 `Parameter` 时，类型会被保持：

```python
threshold = alt.param(value=100, name="threshold")  # 类型: Parameter
adjusted = discounted - threshold  # 类型: ParameterExpression

# 追踪过程：
# 1. discounted.__sub__(threshold) 被调用
# 2. 创建 BinaryExpression("-", discounted, threshold)
# 3. 调用 discounted._from_expr(BinaryExpression(...))
# 4. 但 threshold 是 Parameter，它的 _from_expr 返回 ParameterExpression
# 实际上是：
#   threshold.__rsub__(discounted) 被调用（因为 discounted 是 Expression）
#   或者更准确地说：
#   当执行 `discounted - threshold` 时：
#   - 首先尝试 discounted.__sub__(threshold)
#   - BinaryExpression 创建
#   - 由于 discounted 是 Expression，其 _from_expr 只返回 expr
#   - 但 threshold 作为 Parameter，当它参与运算时：
#   - threshold.__rsub__(discounted) 会被调用吗？
#   - 不，Python 先调用左操作数的 __sub__
#   - 实际上：
#     让我们看实际的实现...
```

实际上，让我们更仔细地看 `OperatorMixin` 的设计：

```python
def __sub__(self, other):
    comp_value = BinaryExpression("-", self, other)
    return self._from_expr(comp_value)

def __rsub__(self, other):
    comp_value = BinaryExpression("-", other, self)
    return self._from_expr(comp_value)
```
<mcfile name="core.py" path="altair/expr/core.py"></mcfile>

所以当执行 `discounted - threshold` 时：
1. `discounted` 是 `Expression`，调用 `discounted.__sub__(threshold)`
2. 创建 `BinaryExpression("-", discounted, threshold)`
3. 调用 `discounted._from_expr(BinaryExpression(...))`
4. `Expression` 没有重写 `_from_expr`，所以使用基类的实现，直接返回 `BinaryExpression`

但当执行 `threshold - discounted` 时：
1. `threshold` 是 `Parameter`，调用 `threshold.__sub__(discounted)`
2. 创建 `BinaryExpression("-", threshold, discounted)`
3. 调用 `threshold._from_expr(BinaryExpression(...))`
4. `Parameter._from_expr` 返回 `ParameterExpression(expr=BinaryExpression(...))`

### 8.4 实际行为验证

让我们通过代码来验证：

```python
import altair as alt

# 情况 1: Expression - Parameter
price = alt.datum.price  # Expression
threshold = alt.param(value=100, name="threshold")  # Parameter

result1 = price - threshold
print(f"类型: {type(result1)}")  
# 实际是: <class 'altair.expr.core.BinaryExpression'>
# 因为 price.__sub__ 被调用，其 _from_expr 返回 expr

# 情况 2: Parameter - Expression
result2 = threshold - price
print(f"类型: {type(result2)}")  
# 实际是: <class 'altair.vegalite.v6.api.ParameterExpression'>
# 因为 threshold.__sub__ 被调用，其 _from_expr 返回 ParameterExpression

# 情况 3: 两个 Parameter 运算
threshold2 = alt.param(value=200, name="threshold2")
result3 = threshold - threshold2
print(f"类型: {type(result3)}")  
# 实际是: <class 'altair.vegalite.v6.api.ParameterExpression'>
```

## 9. 关键设计模式

### 9.1 混入类模式（Mixin Pattern）

`OperatorMixin` 使用混入类模式，为多个类提供运算符重载能力：

- **优点**：代码复用，灵活性高
- **应用场景**：`Expression`, `Parameter`, `ParameterExpression`, `SelectionExpression` 都需要运算符能力

### 9.2 解释器模式（Interpreter Pattern）

表达式系统本质上是一个小型解释器：

- **抽象表达式**：`Expression` 基类
- **终结符表达式**：`ConstExpression`, `GetAttrExpression`, `GetItemExpression`
- **非终结符表达式**：`UnaryExpression`, `BinaryExpression`, `FunctionExpression`
- **解释操作**：`__repr__()` 和 `_to_expr()` 方法执行"解释"

### 9.3 工厂方法模式（Factory Method Pattern）

`_from_expr` 方法本质上是一个工厂方法：

- **创建产品**：表达式对象
- **子类定制**：不同子类重写以返回不同类型
- **保持类型**：确保运算结果保持正确的语义类型

### 9.4 单例模式（Singleton Pattern）

`datum` 是一个单例对象：

```python
class DatumType:
    # ... 实现 ...

datum = DatumType()  # 单例实例
```

**原因**：
- 不需要多个 `datum` 实例
- 全局唯一入口点，便于使用

## 10. 表达式转换规则速查表

### 10.1 运算符转换

| Python 表达式 | JavaScript 表达式 |
|--------------|-------------------|
| `a + b` | `(a + b)` |
| `a - b` | `(a - b)` |
| `a * b` | `(a * b)` |
| `a / b` | `(a / b)` |
| `a % b` | `(a % b)` |
| `a ** b` | `pow(a,b)` |
| `-a` | `(-a)` |
| `+a` | `(+a)` |
| `a == b` | `(a === b)` |
| `a != b` | `(a !== b)` |
| `a > b` | `(a > b)` |
| `a < b` | `(a < b)` |
| `a >= b` | `(a >= b)` |
| `a <= b` | `(a <= b)` |
| `a & b` | `(a && b)` |
| `a \| b` | `(a \|\| b)` |
| `~a` | `(!a)` |
| `abs(a)` | `abs(a)` |

### 10.2 值类型转换

| Python 值 | JavaScript 表达式 |
|-----------|-------------------|
| `True` | `true` |
| `False` | `false` |
| `None` | `null` |
| `42` | `42` |
| `3.14` | `3.14` |
| `"hello"` | `'hello'` |
| `datetime(2024, 3, 15)` | `datetime(2024,2,15,0,0,0,0)` |
| `datetime(2024, 3, 15, tzinfo=timezone.utc)` | `utc(2024,2,15,0,0,0,0)` |

### 10.3 函数调用转换

| Python 调用 | JavaScript 表达式 |
|------------|-------------------|
| `alt.expr.if_(a, b, c)` | `if(a,b,c)` |
| `alt.expr.pow(a, b)` | `pow(a,b)` |
| `alt.expr.round(a)` | `round(a)` |
| `alt.expr.isNumber(a)` | `isNumber(a)` |
| `alt.expr.month(date)` | `month(date)` |
| `alt.expr.upper(s)` | `upper(s)` |

## 11. 边界情况和注意事项

### 11.1 关键字冲突处理

`if` 是 Python 关键字，所以函数被重命名为 `if_`：

```python
NAME_MAP = {"if": "if_"}
```
<mcfile name="funcs.py" path="altair/expr/funcs.py"></mcfile>

在 `expr` 类中：
```python
@classmethod
def if_(cls, test, thenValue, elseValue, /) -> Expression:
    return FunctionExpression("if", (test, thenValue, elseValue))
```
<mcfile name="__init__.py" path="altair/expr/__init__.py"></mcfile>

### 11.2 时区限制

只有 UTC 时区和 naive datetime 被支持：

```python
if tzinfo := obj.tzinfo:
    if tzinfo is dt.timezone.utc:
        fn_name = "utc"
    else:
        msg = (
            f"Unsupported timezone {tzinfo!r}.\n"
            "Only `'UTC'` or naive (local) datetimes are permitted."
        )
        raise TypeError(msg)
```
<mcfile name="core.py" path="altair/expr/core.py"></mcfile>

### 11.3 双下划线属性保护

`DatumType` 和 `Parameter` 都保护了双下划线属性：

```python
def __getattr__(self, attr):
    if attr.startswith("__") and attr.endswith("__"):
        raise AttributeError(attr)
    # ... 正常处理 ...
```
<mcfile name="core.py" path="altair/expr/core.py"></mcfile>

**原因**：防止 Python 特殊方法（如 `__getitem__`, `__len__` 等）被意外拦截。

### 11.4 运算符优先级

表达式系统不处理运算符优先级，所有二元运算都被括号包围：

```python
def __repr__(self):
    return f"({_js_repr(self.lhs)} {self.op} {_js_repr(self.rhs)})"
```
<mcfile name="core.py" path="altair/expr/core.py"></mcfile>

**结果**：
- Python: `a + b * c` → JavaScript: `(a + (b * c))`
- 实际上，Python 先计算 `b * c`，然后计算 `a + (b * c)`
- 但由于每个运算都被括号包围，最终的 JavaScript 表达式会有很多括号

**示例**：
```python
result = datum.a + datum.b * datum.c - datum.d
# 生成的 JavaScript:
# (((datum.a + (datum.b * datum.c)) - datum.d))
```

虽然括号很多，但这确保了语义正确。

## 12. 架构扩展点

### 12.1 自定义表达式类型

通过继承 `OperatorMixin` 可以创建自定义表达式类型：

```python
class MyCustomExpression(_expr_core.OperatorMixin):
    def __init__(self, expr):
        self.expr = expr
    
    def _to_expr(self) -> str:
        return repr(self.expr)
    
    def _from_expr(self, expr):
        return MyCustomExpression(expr)
    
    def to_dict(self):
        return {"custom_expr": repr(self.expr)}
```

### 12.2 自定义值类型转换

通过重写 `_js_repr` 的行为（虽然当前是模块级函数），或者通过实现 `OperatorMixin` 的 `_to_expr` 方法：

```python
class MyCustomType(_expr_core.OperatorMixin):
    def __init__(self, value):
        self.value = value
    
    def _to_expr(self) -> str:
        # 自定义字符串化逻辑
        return f"customConvert({self.value!r})"
```

### 12.3 自定义函数

虽然 `expr` 类的方法是自动生成的，但用户可以通过 `FunctionExpression` 直接调用任意函数：

```python
from altair.expr.core import FunctionExpression

# 调用自定义函数
custom_expr = FunctionExpression("myCustomFunction", (arg1, arg2))
```

## 13. 总结

Altair 的表达式 DSL 是一个精心设计的系统，其核心特点包括：

1. **运算符重载**：通过 `OperatorMixin` 混入类提供完整的 Python 运算符支持
2. **表达式类型**：使用解释器模式，将每种表达式类型建模为独立的类
3. **类型保持**：通过 `_from_expr` 工厂方法，确保跨模块运算时类型语义正确
4. **自动代码生成**：`expr` 类的函数方法从 Vega 官方文档自动生成，确保与规范同步
5. **值转换**：`_js_repr` 函数处理各种 Python 类型到 JavaScript 表示的安全转换
6. **灵活扩展**：架构设计允许通过继承 `OperatorMixin` 自定义表达式行为

该系统成功地将 Python 的表达力与 Vega-Lite 的可视化能力结合起来，让用户能够用熟悉的 Python 语法构建复杂的可视化交互逻辑。

## 参考文件

- `altair/expr/core.py` - 核心表达式类和运算符实现
- `altair/expr/__init__.py` - `expr` 工具类和自动生成的函数方法
- `altair/expr/funcs.py` - 函数列表和名称映射
- `altair/expr/consts.py` - 常量定义
- `altair/vegalite/v6/api.py` - `Parameter` 类和表达式集成
- `tools/vega_expr.py` - 表达式模块自动生成脚本
