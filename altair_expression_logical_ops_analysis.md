# Altair 表达式 DSL 逻辑运算语义分析报告

## 核心问题澄清

**关键发现**：Python 的 `and`、`or`、`not` 是**关键字**，**不能被重载**。Altair 实际重载的是**位运算符** `&`、`|`、`~`，这些位运算符被映射到 JavaScript 的**逻辑运算符** `&&`、`||`、`!`。

---

## 1. Python 运算符重载机制

### 1.1 可重载运算符 vs 不可重载关键字

Python 中，运算符和关键字有本质区别：

| 类型 | 示例 | 可重载 | 特殊方法 |
|------|------|--------|----------|
| **位运算符** | `&`、`\|`、`~` | ✅ 可重载 | `__and__`、`__or__`、`__invert__` |
| **比较运算符** | `==`、`!=`、`>`、`<` | ✅ 可重载 | `__eq__`、`__ne__`、`__gt__`、`__lt__` |
| **算术运算符** | `+`、`-`、`*`、`/` | ✅ 可重载 | `__add__`、`__sub__`、`__mul__`、`__truediv__` |
| **关键字** | `and`、`or`、`not` | ❌ **不可重载** | 无（语言内置，无法修改） |

### 1.2 关键概念区分

**位运算符 `&`、`|`、`~`**：
- 原本用于整数的位操作（按位与、按位或、按位取反）
- 可以通过 `__and__`、`__or__`、`__invert__` 方法重载
- 运算符优先级：`~` > `&` > `|`

**逻辑关键字 `and`、`or`、`not`**：
- Python 语言内置关键字
- 实现短路求值（short-circuit evaluation）
- **无法通过任何方法重载**
- 依赖 `bool()` 函数判断真值
- 运算符优先级：`not` > `and` > `or`

---

## 2. Altair 实际实现分析

### 2.1 代码中的实际重载

查看 `altair/expr/core.py` 中的 `OperatorMixin` 类：

```python
# logical operators  <- 注意：注释说是"逻辑运算符"，但实际是位运算符方法

def __and__(self, other):
    comp_value = BinaryExpression("&&", self, other)  # 映射到 JavaScript 的 &&
    return self._from_expr(comp_value)

def __rand__(self, other):
    comp_value = BinaryExpression("&&", other, self)
    return self._from_expr(comp_value)

def __or__(self, other):
    comp_value = BinaryExpression("||", self, other)  # 映射到 JavaScript 的 ||
    return self._from_expr(comp_value)

def __ror__(self, other):
    comp_value = BinaryExpression("||", other, self)
    return self._from_expr(comp_value)

def __invert__(self):
    comp_value = UnaryExpression("!", self)  # 映射到 JavaScript 的 !
    return self._from_expr(comp_value)
```
<mcfile name="core.py" path="altair/expr/core.py"></mcfile>

### 2.2 映射关系

| Python 位运算符 | 特殊方法 | 映射到 JavaScript | 说明 |
|----------------|----------|-------------------|------|
| `&` | `__and__` / `__rand__` | `&&` | 逻辑与 |
| `\|` | `__or__` / `__ror__` | `\|\|` | 逻辑或 |
| `~` | `__invert__` | `!` | 逻辑非 |

### 2.3 代码注释的误导性

代码中的注释 `# logical operators` 可能造成误解：

```python
    # logical operators  <- 这个注释容易误导
    # 实际上这些是位运算符的方法，只是被映射到 JavaScript 的逻辑运算符

    def __and__(self, other):
        comp_value = BinaryExpression("&&", self, other)
        return self._from_expr(comp_value)
```

**更准确的理解**：
- 这些方法重载的是**位运算符** `&`、`|`、`~`
- 但这些位运算符被**映射**到 JavaScript 的**逻辑运算符** `&&`、`||`、`!`
- 这是一个设计选择：用位运算符的语法实现逻辑运算的语义

---

## 3. 行为对比分析

### 3.1 使用位运算符 `&`、`|`、`~`

**正确用法**：

```python
import altair as alt

# 使用位运算符 &
result1 = alt.datum.active & alt.datum.enabled
# 内部: BinaryExpression("&&", datum.active, datum.enabled)
# 结果: "(datum.active && datum.enabled)"

# 使用位运算符 |
result2 = alt.datum.active | alt.datum.enabled
# 内部: BinaryExpression("||", datum.active, datum.enabled)
# 结果: "(datum.active || datum.enabled)"

# 使用位运算符 ~
result3 = ~alt.datum.active
# 内部: UnaryExpression("!", datum.active)
# 结果: "(!datum.active)"
```

**与比较运算符组合**：

```python
# (price > 100) & (quantity > 5)
result4 = (alt.datum.price > 100) & (alt.datum.quantity > 5)
# 结果: "((datum.price > 100) && (datum.quantity > 5))"

# (price > 100) | (quantity > 5)
result5 = (alt.datum.price > 100) | (alt.datum.quantity > 5)
# 结果: "((datum.price > 100) || (datum.quantity > 5))"

# ~(price > 100)
result6 = ~(alt.datum.price > 100)
# 结果: "(!(datum.price > 100))"
```

### 3.2 使用关键字 `and`、`or`、`not`

**错误用法（意外行为）**：

```python
import altair as alt

# 使用 and 关键字
result7 = alt.datum.active and alt.datum.enabled
# 这不会创建表达式对象！
# Python 会执行短路求值：
# 1. 调用 bool(alt.datum.active)
# 2. 如果为 True，返回 alt.datum.enabled
# 3. 如果为 False，返回 alt.datum.active

# 使用 or 关键字
result8 = alt.datum.active or alt.datum.enabled
# 同样执行短路求值

# 使用 not 关键字
result9 = not alt.datum.active
# 调用 bool(alt.datum.active)，然后取反，返回布尔值
```

### 3.3 关键行为差异对比

| 行为 | 位运算符 `&`/`\|`/`~` | 关键字 `and`/`or`/`not` |
|------|----------------------|-------------------------|
| **是否创建表达式对象** | ✅ 是 | ❌ 否 |
| **是否执行短路求值** | ❌ 否（不计算操作数） | ✅ 是（依赖 `bool()`） |
| **是否可重载** | ✅ 是 | ❌ 否 |
| **结果类型** | `Expression` 子类 | 操作数或 `bool` |
| **Vega-Lite 表达式** | 生成正确的 `&&`/`\|\|`/`!` | 不生成表达式 |

---

## 4. 可验证示例分析

### 4.1 预期行为

基于代码实现，以下是预期的行为：

```python
import altair as alt

# 示例 1: 基本位运算符
# datum.active & datum.enabled
# 预期结果: "(datum.active && datum.enabled)"

# 示例 2: 带括号的优先级
# (datum.price > 100) & (datum.quantity > 5)
# 预期结果: "((datum.price > 100) && (datum.quantity > 5))"

# 示例 3: 复杂表达式
# (datum.active & datum.enabled) | ~datum.disabled
# 预期结果: "((datum.active && datum.enabled) || (!datum.disabled))"

# 示例 4: 取反
# ~datum.active
# 预期结果: "(!datum.active)"

# 示例 5: 比较运算符组合
# ~(datum.price > 100) & (datum.quantity > 5)
# 预期结果: "((!(datum.price > 100)) && (datum.quantity > 5))"
```

### 4.2 运算符优先级

**位运算符优先级**（从高到低）：
1. `~`（按位取反）- 最高优先级
2. `&`（按位与）
3. `|`（按位或）- 最低优先级

**比较运算符优先级**：
- 高于位运算符

**示例**：
```python
# ~datum.price > 100
# 等价于: (~datum.price) > 100
# 结果: "((!datum.price) > 100)"

# datum.price > 100 & datum.quantity > 5
# 等价于: datum.price > (100 & datum.quantity) > 5
# ⚠️ 注意：这可能不是预期行为！

# 正确写法：使用括号明确优先级
# (datum.price > 100) & (datum.quantity > 5)
# 结果: "((datum.price > 100) && (datum.quantity > 5))"
```

---

## 5. 修正之前的错误结论

### 5.1 错误结论 vs 正确结论

| 之前的错误结论 | 正确结论 |
|---------------|---------|
| `datum.a and datum.b` → `"(datum.a && datum.b)"` | ❌ 错误！`and` 是关键字，不能被重载。应该使用 `&` |
| `datum.a or datum.b` → `"(datum.a \|\| datum.b)"` | ❌ 错误！`or` 是关键字，不能被重载。应该使用 `\|` |
| `not datum.active` → `"(!datum.active)"` | ❌ 错误！`not` 是关键字，不能被重载。应该使用 `~` |

### 5.2 正确的转换表

| Python 表达式 | Vega-Lite 表达式 | 说明 |
|--------------|------------------|------|
| `datum.a & datum.b` | `"(datum.a && datum.b)"` | ✅ 正确（位与 → 逻辑与） |
| `datum.a \| datum.b` | `"(datum.a \|\| datum.b)"` | ✅ 正确（位或 → 逻辑或） |
| `~datum.a` | `"(!datum.a)"` | ✅ 正确（位取反 → 逻辑非） |
| `datum.a and datum.b` | 不生成表达式 | ❌ 错误（`and` 是关键字） |
| `datum.a or datum.b` | 不生成表达式 | ❌ 错误（`or` 是关键字） |
| `not datum.a` | 返回 `bool` | ❌ 错误（`not` 是关键字） |

### 5.3 代码注释的问题

代码中的注释 `# logical operators` 存在误导性：

```python
    # logical operators  <- 这个注释容易让人以为是逻辑关键字
    # 实际上是位运算符的方法

    def __and__(self, other):
        comp_value = BinaryExpression("&&", self, other)  # 映射到 &&
        return self._from_expr(comp_value)
```

**更准确的理解**：
- 这些方法重载的是**位运算符** `&`、`|`、`~`
- 但这些运算符被**映射**到 JavaScript 的**逻辑运算符**
- 这是一个巧妙的设计：用位运算符的语法实现逻辑运算的语义

---

## 6. 完整速查表

### 6.1 位运算符到逻辑运算符的映射

| Python 语法 | 重载方法 | JavaScript 映射 | Vega-Lite 表达式 |
|------------|----------|-----------------|------------------|
| `a & b` | `__and__` / `__rand__` | `&&` | `"(a && b)"` |
| `a \| b` | `__or__` / `__ror__` | `\|\|` | `"(a \|\| b)"` |
| `~a` | `__invert__` | `!` | `"(!a)"` |

### 6.2 比较运算符

| Python 语法 | 重载方法 | JavaScript 映射 | Vega-Lite 表达式 |
|------------|----------|-----------------|------------------|
| `a == b` | `__eq__` | `===` | `"(a === b)"` |
| `a != b` | `__ne__` | `!==` | `"(a !== b)"` |
| `a > b` | `__gt__` | `>` | `"(a > b)"` |
| `a < b` | `__lt__` | `<` | `"(a < b)"` |
| `a >= b` | `__ge__` | `>=` | `"(a >= b)"` |
| `a <= b` | `__le__` | `<=` | `"(a <= b)"` |

### 6.3 算术运算符

| Python 语法 | 重载方法 | JavaScript 映射 | Vega-Lite 表达式 |
|------------|----------|-----------------|------------------|
| `a + b` | `__add__` / `__radd__` | `+` | `"(a + b)"` |
| `a - b` | `__sub__` / `__rsub__` | `-` | `"(a - b)"` |
| `a * b` | `__mul__` / `__rmul__` | `*` | `"(a * b)"` |
| `a / b` | `__truediv__` / `__rtruediv__` | `/` | `"(a / b)"` |
| `a % b` | `__mod__` / `__rmod__` | `%` | `"(a % b)"` |
| `a ** b` | `__pow__` / `__rpow__` | `pow()` | `"pow(a,b)"` |
| `-a` | `__neg__` | `-` | `"(-a)"` |
| `+a` | `__pos__` | `+` | `"(+a)"` |
| `abs(a)` | `__abs__` | `abs()` | `"abs(a)"` |

### 6.4 不能使用的关键字

| Python 关键字 | 行为 | 替代方案 |
|--------------|------|----------|
| `and` | 短路求值，返回操作数或 `bool` | 使用 `&` |
| `or` | 短路求值，返回操作数或 `bool` | 使用 `\|` |
| `not` | 调用 `bool()`，返回 `bool` | 使用 `~` |

---

## 7. 最佳实践建议

### 7.1 始终使用位运算符进行逻辑运算

```python
# ✅ 正确：使用位运算符
result = (alt.datum.price > 100) & (alt.datum.quantity > 5)

# ❌ 错误：使用关键字
result = (alt.datum.price > 100) and (alt.datum.quantity > 5)  # 不会创建表达式
```

### 7.2 注意运算符优先级

```python
# ⚠️ 危险：优先级可能导致意外行为
# datum.price > 100 & datum.quantity > 5
# 实际解析为: datum.price > (100 & datum.quantity) > 5

# ✅ 正确：使用括号明确优先级
result = (alt.datum.price > 100) & (alt.datum.quantity > 5)
```

### 7.3 复杂表达式示例

```python
# 复杂逻辑表达式
# (active & enabled) | ~disabled
result = (alt.datum.active & alt.datum.enabled) | (~alt.datum.disabled)
# 结果: "((datum.active && datum.enabled) || (!datum.disabled))"

# 带比较的复杂表达式
# ~(price > 100) & (quantity > 5) | (category == "A")
result = (~(alt.datum.price > 100) & (alt.datum.quantity > 5)) | (alt.datum.category == "A")
# 结果: "(((!(datum.price > 100)) && (datum.quantity > 5)) || (datum.category === 'A'))"
```

### 7.4 使用 expr 模块的逻辑函数

除了位运算符，还可以使用 `expr` 模块的函数：

```python
# 使用 expr.if_ 实现条件逻辑
result = alt.expr.if_(
    alt.datum.price > 100,
    "high",
    "low"
)
# 结果: "if((datum.price > 100),'high','low')"
```

---

## 8. 总结

### 8.1 核心要点

1. **Python 的 `and`、`or`、`not` 是关键字，不能被重载**
   - 它们实现短路求值
   - 依赖 `bool()` 函数判断真值
   - 无法通过任何方法修改其行为

2. **Altair 实际重载的是位运算符 `&`、`|`、`~`**
   - 这些运算符通过 `__and__`、`__or__`、`__invert__` 方法重载
   - 被映射到 JavaScript 的逻辑运算符 `&&`、`||`、`!`

3. **代码注释存在误导性**
   - `# logical operators` 注释容易让人误解
   - 实际上是位运算符的方法
   - 但被映射到逻辑运算符的语义

### 8.2 关键修正

| 之前的错误 | 修正后的正确理解 |
|-----------|-----------------|
| `and` 被重载为 `&&` | ❌ 错误，`and` 是关键字，不能被重载。使用 `&` |
| `or` 被重载为 `\|\|` | ❌ 错误，`or` 是关键字，不能被重载。使用 `\|` |
| `not` 被重载为 `!` | ❌ 错误，`not` 是关键字，不能被重载。使用 `~` |

### 8.3 实际使用建议

1. **始终使用位运算符** `&`、`|`、`~` 进行逻辑运算
2. **使用括号明确优先级**，避免意外行为
3. **不要使用关键字** `and`、`or`、`not`，它们不会创建表达式对象
4. **理解代码注释的误导性**，区分"位运算符方法"和"逻辑运算语义"

---

## 参考文件

- `altair/expr/core.py` - `OperatorMixin` 类中的运算符重载实现
- `tests/expr/test_expr.py` - 测试用例中使用 `operator.and_`、`operator.or_`（对应位运算符）

---

## 附录：可验证测试代码

以下是可用于验证行为的测试代码：

```python
"""测试 Altair 表达式 DSL 的逻辑运算符行为"""

import altair as alt
from altair.expr.core import Expression

print("=" * 60)
print("测试位运算符 & | ~")
print("=" * 60)

# 测试位运算符 &
try:
    result1 = alt.datum.active & alt.datum.enabled
    print(f"datum.active & datum.enabled = {repr(result1)}")
    assert isinstance(result1, Expression), "应该返回 Expression"
    assert "&&" in repr(result1), "应该包含 &&"
    print("  ✅ 通过")
except Exception as e:
    print(f"  ❌ 失败: {e}")

# 测试位运算符 |
try:
    result2 = alt.datum.active | alt.datum.enabled
    print(f"datum.active | datum.enabled = {repr(result2)}")
    assert isinstance(result2, Expression), "应该返回 Expression"
    assert "||" in repr(result2), "应该包含 ||"
    print("  ✅ 通过")
except Exception as e:
    print(f"  ❌ 失败: {e}")

# 测试位运算符 ~
try:
    result3 = ~alt.datum.active
    print(f"~datum.active = {repr(result3)}")
    assert isinstance(result3, Expression), "应该返回 Expression"
    assert "!" in repr(result3), "应该包含 !"
    print("  ✅ 通过")
except Exception as e:
    print(f"  ❌ 失败: {e}")

print()
print("=" * 60)
print("测试比较运算符 & 位运算符组合")
print("=" * 60)

# (price > 100) & (quantity > 5)
try:
    result4 = (alt.datum.price > 100) & (alt.datum.quantity > 5)
    print(f"(price > 100) & (quantity > 5) = {repr(result4)}")
    assert isinstance(result4, Expression), "应该返回 Expression"
    print("  ✅ 通过")
except Exception as e:
    print(f"  ❌ 失败: {e}")

# (price > 100) | (quantity > 5)
try:
    result5 = (alt.datum.price > 100) | (alt.datum.quantity > 5)
    print(f"(price > 100) | (quantity > 5) = {repr(result5)}")
    assert isinstance(result5, Expression), "应该返回 Expression"
    print("  ✅ 通过")
except Exception as e:
    print(f"  ❌ 失败: {e}")

# ~(price > 100)
try:
    result6 = ~(alt.datum.price > 100)
    print(f"~(price > 100) = {repr(result6)}")
    assert isinstance(result6, Expression), "应该返回 Expression"
    print("  ✅ 通过")
except Exception as e:
    print(f"  ❌ 失败: {e}")

print()
print("=" * 60)
print("总结")
print("=" * 60)
print()
print("关键发现：")
print("1. Python 的 and, or, not 是关键字，不能被重载")
print("2. Altair 实际重载的是位运算符 &, |, ~")
print("3. 这些位运算符被映射到 JavaScript 的逻辑运算符 &&, ||, !")
print("4. 使用 and/or/not 关键字会触发 Python 的短路求值，不会创建表达式对象")
```
