"""测试 Altair 表达式 DSL 的逻辑运算符行为"""

import altair as alt

print("=" * 60)
print("测试位运算符 & | ~")
print("=" * 60)

# 测试位运算符 &
try:
    result1 = alt.datum.active & alt.datum.enabled
    print(f"datum.active & datum.enabled = {repr(result1)}")
except Exception as e:
    print(f"& 失败: {e}")

# 测试位运算符 |
try:
    result2 = alt.datum.active | alt.datum.enabled
    print(f"datum.active | datum.enabled = {repr(result2)}")
except Exception as e:
    print(f"| 失败: {e}")

# 测试位运算符 ~
try:
    result3 = ~alt.datum.active
    print(f"~datum.active = {repr(result3)}")
except Exception as e:
    print(f"~ 失败: {e}")

print()
print("=" * 60)
print("测试 Python 关键字 and or not")
print("=" * 60)

# 测试 and 关键字
try:
    result4 = alt.datum.active and alt.datum.enabled
    print(f"datum.active and datum.enabled = {repr(result4)}")
    print(f"类型: {type(result4)}")
except Exception as e:
    print(f"and 失败: {e}")

# 测试 or 关键字
try:
    result5 = alt.datum.active or alt.datum.enabled
    print(f"datum.active or datum.enabled = {repr(result5)}")
    print(f"类型: {type(result5)}")
except Exception as e:
    print(f"or 失败: {e}")

# 测试 not 关键字
try:
    result6 = not alt.datum.active
    print(f"not datum.active = {repr(result6)}")
    print(f"类型: {type(result6)}")
except Exception as e:
    print(f"not 失败: {e}")

print()
print("=" * 60)
print("测试短路行为")
print("=" * 60)

# 测试 and 的短路行为
# and 的短路行为：如果第一个为"假"，返回第一个
# 对于 Expression 对象，bool() 会返回什么？

try:
    print(f"bool(alt.datum.active) = {bool(alt.datum.active)}")
except Exception as e:
    print(f"bool(datum.active) 失败: {e}")

try:
    print(f"bool(alt.datum.active & alt.datum.enabled) = {bool(alt.datum.active & alt.datum.enabled)}")
except Exception as e:
    print(f"bool(...) 失败: {e}")

# 测试实际的短路行为
try:
    result7 = alt.datum.false_val and alt.datum.true_val
    print(f"datum.false_val and datum.true_val = {repr(result7)}")
    print(f"类型: {type(result7)}")
except Exception as e:
    print(f"and 短路测试失败: {e}")

try:
    result8 = alt.datum.true_val or alt.datum.false_val
    print(f"datum.true_val or datum.false_val = {repr(result8)}")
    print(f"类型: {type(result8)}")
except Exception as e:
    print(f"or 短路测试失败: {e}")

print()
print("=" * 60)
print("复杂表达式测试")
print("=" * 60)

# 测试复杂表达式
try:
    # (active & enabled) | ~disabled
    result9 = (alt.datum.active & alt.datum.enabled) | (~alt.datum.disabled)
    print(f"(active & enabled) | ~disabled = {repr(result9)}")
except Exception as e:
    print(f"复杂表达式失败: {e}")

try:
    # 比较运算符 & 位运算符
    result10 = (alt.datum.price > 100) & (alt.datum.quantity > 5)
    print(f"(price > 100) & (quantity > 5) = {repr(result10)}")
except Exception as e:
    print(f"比较运算符 & 失败: {e}")

try:
    # 比较运算符 | 位运算符
    result11 = (alt.datum.price > 100) | (alt.datum.quantity > 5)
    print(f"(price > 100) | (quantity > 5) = {repr(result11)}")
except Exception as e:
    print(f"比较运算符 | 失败: {e}")

print()
print("=" * 60)
print("总结")
print("=" * 60)
print()
print("关键发现：")
print("1. Python 的 and, or, not 是关键字，不能被重载")
print("2. Altair 实际重载的是位运算符 &, |, ~")
print("3. 这些位运算符被映射到 JavaScript 的逻辑运算符 &&, ||, !")
print("4. 使用 and/or/not 关键字会触发 Python 的短路求值，返回布尔值或第一个操作数")
