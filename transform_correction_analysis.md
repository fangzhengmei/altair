# Altair 变换机制修正与补充分析报告

## 1. 概述

本文档是前三篇分析报告的修正与补充，重点解决以下问题：

1. **修正谓词组合输出格式的错误描述**：结构体谓词（字段范围、字段等值等）通过逻辑运算符组合后的输出格式
2. **对比两种组合机制**：结构体谓词组合 vs 选择参数组合的异同
3. **分析为什么组合谓词不需要 `test` 包装**：深入理解 `_predicate_to_condition()` 分发路径和序列化方式的差异
4. **分析类型收窄辅助设计**：`_top_schema_base` 函数存在的原因和多继承类型推断限制

---

## 2. 谓词组合输出格式的错误修正

### 2.1 错误描述与正确结果

**上一轮报告的错误描述**：
```json
{"filter": {"test": {"and": [...]}}}  // 错误！多了一层 test 包装
```

**实际正确的输出格式**：
```json
{"filter": {"and": [...]}}  // 正确！直接作为过滤器值
```

### 2.2 关键原因：`cond.get("test", cond)`

导致这个差异的关键代码在 `transform_filter` 方法的最后一步：

```python
# 文件: altair/vegalite/v6/api.py, 行 3228-3229
cond = _parse_when(predicate, *more_predicates, empty=empty, **constraints)
return self._add_transform(core.FilterTransform(filter=cond.get("test", cond)))
```

**`cond.get("test", cond)` 的作用**：

1. 如果 `cond = {"test": 值}`，则返回 **值本身**（提取 `"test"` 的值）
2. 如果 `cond` 没有 `"test"` 键，则返回 `cond` 本身

这意味着：
- `_predicate_to_condition()` 返回 `{"test": 谓词对象}` 或 `{"test": 字符串}`
- `cond.get("test", cond)` 提取出 **值本身**
- 值在序列化时根据类型不同有不同的行为

### 2.3 完整执行流程（以 `PredicateComposition` 为例）

以 `selector1 & selector2` 为例：

```python
selector1 = alt.selection_interval(name="s1")
selector2 = alt.selection_interval(name="s2")
chart = base.transform_filter(selector1 & selector2)
```

**执行流程**：

```
用户调用: chart.transform_filter(selector1 & selector2)
         │
         ▼
1. selector1 & selector2
   │
   └─→ Parameter.__and__(selector1, selector2) 被调用
       │
       ├─→ selector1.param_type == "selection"? → 是
       │
       ├─→ self_dict = {"param": "s1"}
       │   other_dict = {"param": "s2"}
       │
       └─→ 返回 core.PredicateComposition({"and": [self_dict, other_dict]})
           │
           └─→ 内部存储: {"and": [{"param": "s1"}, {"param": "s2"}]}
         │
         ▼
2. _parse_when(...)
   │
   └─→ 没有 *more_predicates 或 **constraints
       │
       └─→ composed = predicate (即 PredicateComposition 对象)
         │
         ▼
3. _predicate_to_condition(composed, empty=empty)
   │
   ├─→ 类型检查: isinstance(composed, Parameter)? → 否
   │
   ├─→ 类型检查: _is_test_predicate(composed)? → 是!
   │   │
   │   └─→ _TestPredicateType = str | Expression | PredicateComposition
   │       PredicateComposition 包含在内!
   │
   └─→ condition = {"test": composed}  # 注意：composed 是对象本身!
         │
         ▼
4. cond.get("test", cond)
   │
   └─→ cond = {"test": composed}
       cond.get("test", cond) → composed (PredicateComposition 对象)
         │
         ▼
5. FilterTransform(filter=composed)
   │
   └─→ composed 是 SchemaBase 子类（PredicateComposition → VegaLiteSchema → SchemaBase）
         │
         ▼
6. 序列化: composed.to_dict()
   │
   └─→ PredicateComposition.to_dict()
       │
       └─→ {"and": [{"param": "s1"}, {"param": "s2"}]}
         │
         ▼
最终输出:
{"filter": {"and": [{"param": "s1"}, {"param": "s2"}]}}
```

### 2.4 对比 `alt.when()` 中的行为

**为什么 `alt.when()` 中有 `test` 包装？**

从测试用例 `test_predicate_composition`：

```python
actual_when = (
    alt.when(field_one_of, field_range).then(alt.value(0)).otherwise(alt.value(1))
)
expected_when = {"condition": [{"test": fields_and, "value": 0}], "value": 1}
```

**原因**：`alt.when()` 不使用 `cond.get("test", cond)` 提取。

查看 `condition()` 函数：

```python
# 文件: altair/vegalite/v6/api.py, 行 1998-1999
condition = _predicate_to_condition(predicate, empty=empty)
return _condition_to_selection(condition, if_true, if_false, **kwargs)
```

这里直接使用 `condition`（即 `{"test": ...}`），没有提取。

**关键差异总结**：

| 上下文 | 处理方式 | 最终格式 |
|--------|---------|---------|
| `transform_filter` | `cond.get("test", cond)` | 提取值本身，无 `test` 包装 |
| `alt.when()` / `alt.condition()` | 直接使用 `condition` | 保留 `{"test": ...}` 包装 |

---

## 3. 两种谓词组合机制的对比分析

### 3.1 组合类型概览

Altair 中有三种主要的谓词组合场景：

| 组合类型 | 输入示例 | 组合结果类型 | 最终序列化格式 |
|---------|----------|-------------|---------------|
| **结构体谓词** | `FieldRangePredicate(...) & FieldEqualPredicate(...)` | `PredicateComposition` | `{"and": [...]}` |
| **选择参数** | `selection_interval() & selection_interval()` | `PredicateComposition` | `{"and": [{"param": "s1"}, {"param": "s2"}]}` |
| **表达式** | `datum.x > 0 & datum.y < 100` | `BinaryExpression` | 字符串 `"(...) && (...)"` |

### 3.2 结构体谓词的组合机制

**结构体谓词**包括：
- `FieldRangePredicate` - 字段范围谓词
- `FieldEqualPredicate` - 字段等值谓词
- `FieldOneOfPredicate` - 字段包含谓词
- `FieldGTPredicate` / `FieldLTPredicate` 等 - 比较谓词
- `ParameterPredicate` - 参数引用谓词

这些类都继承自 `SchemaBase`，且**运算符重载在 `PredicateComposition` 中定义**：

```python
# 文件: altair/vegalite/v6/schema/core.py, 行 16317-16324
class PredicateComposition(VegaLiteSchema):
    """PredicateComposition schema wrapper."""
    
    _schema = {"$ref": "#/definitions/PredicateComposition"}
    
    def __invert__(self) -> PredicateComposition:
        return PredicateComposition({"not": self.to_dict()})
    
    def __and__(self, other: SchemaBase) -> PredicateComposition:
        return PredicateComposition({"and": [self.to_dict(), other.to_dict()]})
    
    def __or__(self, other: SchemaBase) -> PredicateComposition:
        return PredicateComposition({"or": [self.to_dict(), other.to_dict()]})
```

**关键点**：
1. **立即序列化**：`self.to_dict()` 和 `other.to_dict()` 在组合时就被调用
2. **返回类型**：始终返回 `PredicateComposition`
3. **嵌套结构**：组合后的对象内部存储的是已序列化的字典

**执行示例**：

```python
field_range = alt.FieldRangePredicate(field="Year", range=[1900, 2000])
field_one_of = alt.FieldOneOfPredicate(field="Entity", oneOf=["A", "B"])

# 组合过程
combined = field_range & field_one_of
# 等价于:
# PredicateComposition.__and__(field_range, field_one_of)
# → PredicateComposition({
#     "and": [
#         field_range.to_dict(),   # {"field": "Year", "range": [1900, 2000]}
#         field_one_of.to_dict()   # {"field": "Entity", "oneOf": ["A", "B"]}
#     ]
# })

# combined.to_dict() → 
# {"and": [
#     {"field": "Year", "range": [1900, 2000]},
#     {"field": "Entity", "oneOf": ["A", "B"]}
# ]}
```

### 3.3 选择参数的组合机制

**选择参数**包括：
- `selection_interval()` - 区间选择
- `selection_point()` - 点选择
- `selection_single()` - 单选
- `selection_multi()` - 多选

**选择参数的组合在 `Parameter` 类中实现**：

```python
# 文件: altair/vegalite/v6/api.py, 行 462-497
class Parameter(_expr_core.OperatorMixin):
    # ... 其他方法 ...
    
    def __invert__(self) -> PredicateComposition | Any:
        if self.param_type == "selection":
            param_dict: dict[str, str | bool] = {"param": self.name}
            if isinstance(self.empty, bool):
                param_dict["empty"] = self.empty
            return core.PredicateComposition({"not": param_dict})
        else:
            return _expr_core.OperatorMixin.__invert__(self)

    def __and__(self, other: Any) -> PredicateComposition | Any:
        if self.param_type == "selection":
            self_dict: dict[str, str | bool] = {"param": self.name}
            if isinstance(self.empty, bool):
                self_dict["empty"] = self.empty
            if isinstance(other, Parameter):
                other_dict: dict[str, str | bool] = {"param": other.name}
                if isinstance(other.empty, bool):
                    other_dict["empty"] = other.empty
                other = other_dict
            return core.PredicateComposition({"and": [self_dict, other]})
        else:
            return _expr_core.OperatorMixin.__and__(self, other)

    def __or__(self, other: Any) -> PredicateComposition | Any:
        if self.param_type == "selection":
            self_dict: dict[str, str | bool] = {"param": self.name}
            if isinstance(self.empty, bool):
                self_dict["empty"] = self.empty
            if isinstance(other, Parameter):
                other_dict: dict[str, str | bool] = {"param": other.name}
                if isinstance(other.empty, bool):
                    other_dict["empty"] = other.empty
                other = other_dict
            return core.PredicateComposition({"or": [self_dict, other]})
        else:
            return _expr_core.OperatorMixin.__or__(self, other)
```

**关键发现**：
1. **条件分支**：只有当 `param_type == "selection"` 时，才使用 `PredicateComposition` 组合
2. **转换格式**：选择参数被转换为 `{"param": name}` 格式
3. **返回类型**：最终返回 `core.PredicateComposition`，与结构体谓词组合的返回类型**相同**

**执行示例**：

```python
selector1 = alt.selection_interval(name="s1")
selector2 = alt.selection_interval(name="s2")

# 组合过程
combined = selector1 & selector2
# 等价于:
# Parameter.__and__(selector1, selector2)
# 
# 步骤:
# 1. selector1.param_type == "selection"? → 是
# 2. self_dict = {"param": "s1"}
# 3. isinstance(selector2, Parameter)? → 是
# 4. other_dict = {"param": "s2"}
# 5. other = other_dict
# 6. 返回 core.PredicateComposition({"and": [self_dict, other]})
#    → PredicateComposition({"and": [{"param": "s1"}, {"param": "s2"}]})

# combined.to_dict() → 
# {"and": [{"param": "s1"}, {"param": "s2"}]}
```

### 3.4 两种组合机制的异同

| 对比项 | 结构体谓词组合 | 选择参数组合 |
|--------|---------------|-------------|
| **运算符重载位置** | `PredicateComposition.__and__` 等 | `Parameter.__and__` 等（仅 `param_type == "selection"`） |
| **组合时是否立即序列化** | 是（`self.to_dict()`） | 是（转换为 `{"param": "name"}` 字典） |
| **返回类型** | `PredicateComposition` | `PredicateComposition`（与结构体谓词相同！） |
| **元素格式** | `{"field": ..., "range": ...}` 等 | `{"param": "name"}` |
| **_is_test_predicate 检查** | 通过（继承自 `PredicateComposition`） | 通过（返回类型是 `PredicateComposition`） |
| **_predicate_to_condition 路径** | `{"test": 对象}` | 相同 |
| **transform_filter 最终输出** | 无 `test` 包装 | 相同 |

**核心共同点**：
1. **两种组合最终都返回 `PredicateComposition` 类型**
2. 都通过 `_is_test_predicate` 检查（因为 `PredicateComposition` 在 `_TestPredicateType` 中）
3. 都走 `{"test": 对象}` → `cond.get("test", cond)` → 提取对象 → 序列化
4. 最终输出都**没有 `test` 包装**

**关键差异**：
- 运算符重载的位置不同
- 组合时的转换方式略有不同（`to_dict()` vs 手动构建字典）
- 但**最终类型和分发路径完全相同**

### 3.5 与表达式组合的对比

**表达式组合**（`datum.x > 0 & datum.y < 100`）走的是不同的路径。

首先理解继承关系：

```
OperatorMixin (独立的 mixin 类，定义 __and__, __or__ 等运算符)
    │
    └── Expression (继承自 OperatorMixin 和 SchemaBase)
              │
              ├── UnaryExpression
              ├── BinaryExpression  ← datum.x > 0 的结果类型
              ├── FunctionExpression
              ├── ConstExpression
              ├── GetAttrExpression  ← datum.x 的类型
              └── GetItemExpression
```

**关键点**：
1. `Expression` 继承自 `OperatorMixin` **和** `SchemaBase`
2. `Expression.to_dict()` 被覆盖为返回 `repr(self)`（字符串），而非标准字典

```python
# 文件: altair/expr/core.py, 行 215-231
class Expression(OperatorMixin, SchemaBase):
    _schema = {"type": "string"}
    
    def to_dict(self, *args, **kwargs):
        return repr(self)  # 覆盖！返回字符串而非字典
```

**表达式组合的执行流程**：

```python
datum.x > 0  # 返回 BinaryExpression（继承自 Expression）
```

**`_predicate_to_condition()` 中的分发**：

```python
# 文件: altair/vegalite/v6/api.py, 行 624-659
_TestPredicateType: TypeAlias = str | _expr_core.Expression | core.PredicateComposition

def _is_test_predicate(obj: Any) -> TypeIs[_TestPredicateType]:
    return isinstance(obj, (str, _expr_core.Expression, core.PredicateComposition))

def _predicate_to_condition(predicate: _PredicateType, ...):
    if isinstance(predicate, Parameter):
        # ...
    elif _is_test_predicate(predicate):
        # 路径 A：str | Expression | PredicateComposition
        condition = {"test": predicate}
    elif isinstance(predicate, dict):
        # ...
    elif isinstance(predicate, _expr_core.OperatorMixin):
        # 路径 B：OperatorMixin
        condition = {"test": predicate._to_expr()}
    else:
        raise TypeError(...)
```

**关键分析**：

由于 `Expression` 继承自 `OperatorMixin`，`BinaryExpression` 对象会同时满足两个路径的条件。但由于 **`_is_test_predicate` 检查优先于 `OperatorMixin` 检查**，`Expression` 及其子类会走路径 A。

**路径 A 的处理**：
```python
condition = {"test": predicate}  # predicate 是 BinaryExpression 对象
```

**`cond.get("test", cond)` 提取**：
```python
# cond = {"test": BinaryExpression 对象}
# cond.get("test", cond) → BinaryExpression 对象
```

**序列化时的关键差异**：

`PredicateComposition.to_dict()` 返回字典：
```python
PredicateComposition({"and": [...]}).to_dict()
# → {"and": [...]} （字典）
```

`Expression.to_dict()` 被覆盖为返回字符串：
```python
BinaryExpression(...).to_dict()
# → "(datum.x > 0)" （字符串！）
```

**对比总结**：

| 对比项 | 表达式组合 | 结构体谓词/选择参数组合 |
|--------|-----------|------------------------|
| **返回类型** | `BinaryExpression` | `PredicateComposition` |
| **继承链** | `Expression` → `OperatorMixin` + `SchemaBase` | `PredicateComposition` → `SchemaBase` |
| **_is_test_predicate 检查** | 通过（`Expression` 包含在内） | 通过 |
| **组合时序列化** | 否（保留对象结构） | 是（立即 `to_dict()`） |
| **to_dict() 返回值** | **字符串**（被覆盖） | **字典**（标准实现） |
| **最终输出格式** | `{"filter": "(datum.x > 0)"}` | `{"filter": {"and": [...]}}` |

---

## 4. 为什么组合谓词不需要 `test` 包装？

### 4.1 核心问题澄清

用户的问题："为什么这类组合谓词的序列化结果能直接作为过滤器值而不需要 `test` 包装，而表达式类谓词却需要经过 `test` 包装？"

**这个问题存在一个常见的误解**：实际上，**所有谓词类型**在 `_predicate_to_condition()` 中都被包装为 `{"test": ...}` 格式。真正的差异在于：

1. **是否被提取**：`transform_filter` 使用 `cond.get("test", cond)` 提取值
2. **序列化方式**：不同类型的 `to_dict()` 行为不同

让我们深入分析。

### 4.2 `_predicate_to_condition()` 的分发路径

```python
# 文件: altair/vegalite/v6/api.py, 行 633-659
def _predicate_to_condition(
    predicate: _PredicateType, *, empty: Optional[bool] = Undefined
) -> _Condition:
    condition: _Condition
    
    # 路径 1: Parameter（交互式选择参数）
    if isinstance(predicate, Parameter):
        predicate_expr = _get_predicate_expr(predicate)
        if predicate.param_type == "selection" or utils.is_undefined(predicate_expr):
            # 输出: {"param": name, "empty": ...}
            condition = {"param": predicate.name}
            if isinstance(empty, bool):
                condition["empty"] = empty
            elif isinstance(predicate.empty, bool):
                condition["empty"] = predicate.empty
        else:
            # 输出: {"test": expr}
            condition = {"test": predicate_expr}
    
    # 路径 2: TestPredicateType（字符串、Expression、PredicateComposition）
    elif _is_test_predicate(predicate):
        # 输出: {"test": predicate}
        condition = {"test": predicate}
    
    # 路径 3: 字典（已格式化的条件）
    elif isinstance(predicate, dict):
        # 直接使用
        condition = predicate
    
    # 路径 4: OperatorMixin（表达式运算符对象，如 datum.x > 0）
    elif isinstance(predicate, _expr_core.OperatorMixin):
        # 输出: {"test": predicate._to_expr()}
        condition = {"test": predicate._to_expr()}
    
    else:
        raise TypeError(...)
    
    return condition
```

### 4.3 不同谓词类型的完整流程对比

#### 场景 A：组合谓词（`PredicateComposition`）

**输入**：`FieldRangePredicate(...) & FieldEqualPredicate(...)` → `PredicateComposition` 对象

```
1. _predicate_to_condition(composed)
   │
   ├─→ _is_test_predicate(composed)? → 是
   │   （PredicateComposition 在 _TestPredicateType 中）
   │
   └─→ condition = {"test": composed}  # composed 是 PredicateComposition 对象
         │
         ▼
2. cond.get("test", cond)
   │
   └─→ 提取出 PredicateComposition 对象本身
         │
         ▼
3. FilterTransform(filter=PredicateComposition 对象)
   │
   └─→ 序列化时调用 PredicateComposition.to_dict()
       │
       └─→ 返回 {"and": [...]} （字典！）
         │
         ▼
最终输出:
{"filter": {"and": [...]}}  ← 无 test 包装，是对象格式
```

#### 场景 B：单个选择参数（`Parameter`）

**输入**：`selection_interval(name="s1")` → `Parameter` 对象

```
1. _predicate_to_condition(selector)
   │
   ├─→ isinstance(selector, Parameter)? → 是
   │
   ├─→ selector.param_type == "selection"? → 是
   │
   └─→ condition = {"param": "s1", "empty": true}  ← 注意：没有 test 包装！
         │
         ▼
2. cond.get("test", cond)
   │
   └─→ cond 没有 "test" 键，返回 cond 本身
       → {"param": "s1", "empty": true}
         │
         ▼
3. FilterTransform(filter={"param": "s1", "empty": true})
   │
   └─→ 直接作为对象序列化
         │
         ▼
最终输出:
{"filter": {"param": "s1", "empty": true}}  ← 无 test 包装
```

#### 场景 C：表达式类谓词（`BinaryExpression`）

**输入**：`datum.x > 0` → `BinaryExpression` 对象（继承自 `Expression`）

```
1. _predicate_to_condition(binary_expr)
   │
   ├─→ _is_test_predicate(binary_expr)? → 是
   │   （Expression 在 _TestPredicateType 中，BinaryExpression 继承自 Expression）
   │
   └─→ condition = {"test": binary_expr}  # binary_expr 是 BinaryExpression 对象
         │
         ▼
2. cond.get("test", cond)
   │
   └─→ 提取出 BinaryExpression 对象本身
         │
         ▼
3. FilterTransform(filter=BinaryExpression 对象)
   │
   └─→ 序列化时调用 BinaryExpression.to_dict()
       │
       ├─→ Expression.to_dict() 被覆盖为 return repr(self)
       │
       └─→ 返回 "(datum.x > 0)" （字符串！）
         │
         ▼
最终输出:
{"filter": "(datum.x > 0)"}  ← 无 test 包装，是字符串格式
```

#### 场景 D：字符串谓词

**输入**：`"datum.x > 0"` → `str`

```
1. _predicate_to_condition("datum.x > 0")
   │
   ├─→ _is_test_predicate("datum.x > 0")? → 是
   │   （str 在 _TestPredicateType 中）
   │
   └─→ condition = {"test": "datum.x > 0"}
         │
         ▼
2. cond.get("test", cond)
   │
   └─→ 提取出字符串 "datum.x > 0"
         │
         ▼
3. FilterTransform(filter="datum.x > 0")
   │
   └─→ 直接作为字符串序列化
         │
         ▼
最终输出:
{"filter": "datum.x > 0"}  ← 无 test 包装
```

### 4.4 关键结论

**所有谓词类型在 `transform_filter` 中的最终输出都没有 `test` 包装！**

差异在于**序列化后的格式**，而不是是否有 `test` 包装：

| 谓词类型 | `_predicate_to_condition()` 返回 | `cond.get("test", cond)` 提取 | 序列化后的值 | 最终输出 |
|---------|---------------------------------|-------------------------------|-------------|----------|
| **组合谓词** | `{"test": PredicateComposition 对象}` | `PredicateComposition` 对象 | `{"and": [...]}` 字典 | `{"filter": {"and": [...]}}` |
| **单个选择参数** | `{"param": "s1", "empty": true}` | `{"param": "s1", "empty": true}` | 同左 | `{"filter": {"param": "s1", "empty": true}}` |
| **表达式对象** | `{"test": BinaryExpression 对象}` | `BinaryExpression` 对象 | `"(datum.x > 0)"` 字符串 | `{"filter": "(datum.x > 0)"}` |
| **字符串** | `{"test": "datum.x > 0"}` | `"datum.x > 0"` 字符串 | 同左 | `{"filter": "datum.x > 0"}` |

### 4.5 为什么用户会有"表达式类谓词需要 test 包装"的误解？

可能的原因：

1. **Vega-Lite 规范中的 `test` 字段**：
   - Vega-Lite 的 `FilterTransform` 可以接受字符串作为 `filter` 值
   - 但 Vega-Lite 的 `condition` 字段需要 `{"test": "...", "value": ...}` 格式
   - 这可能导致混淆

2. **`alt.when()` / `alt.condition()` 中的行为**：
   - 这些函数直接使用 `_predicate_to_condition()` 的返回值
   - 不调用 `cond.get("test", cond)` 提取
   - 所以在 `condition` 上下文中会看到 `{"test": ...}` 格式

3. **表达式字符串 vs 对象的混淆**：
   - 表达式类谓词的最终输出是**字符串格式**
   - 组合谓词的最终输出是**对象格式**
   - 这可能被误解为"一个有 test 包装，一个没有"

### 4.6 唯一有 `test` 包装的场景

**只有在 `alt.when()` / `alt.condition()` 中才会保留 `test` 包装**：

```python
# 文件: altair/vegalite/v6/api.py, 行 1998-1999
def condition(predicate, if_true, if_false, *, empty=Undefined, **kwargs):
    condition = _predicate_to_condition(predicate, empty=empty)
    return _condition_to_selection(condition, if_true, if_false, **kwargs)
    # 注意：直接使用 condition，没有 cond.get("test", cond)
```

**示例**：

```python
# transform_filter 中：无 test 包装
chart.transform_filter(field_one_of & field_range)
# 输出: {"filter": {"and": [...]}}

# alt.when() 中：有 test 包装
alt.when(field_one_of, field_range).then(alt.value(0)).otherwise(alt.value(1))
# 输出: {"condition": [{"test": {"and": [...]}, "value": 0}], "value": 1}
```

**原因**：这是 Vega-Lite 规范的要求。`FilterTransform` 可以直接接受谓词对象或字符串，而 `condition` 字段需要 `{"test": ...}` 格式来区分不同的条件类型。

---

## 5. 类型收窄辅助函数：`_top_schema_base`

### 5.1 函数定义与用途

```python
# 文件: altair/vegalite/v6/api.py, 行 2006-2035
def _top_schema_base(  # noqa: ANN202
    obj: Any, /
):  # -> <subclass of SchemaBase and TopLevelMixin>
    """
    Enforces an intersection type w/ `SchemaBase` & `TopLevelMixin` objects.

    Use for methods, called from `TopLevelMixin` that are defined in `SchemaBase`.

    Notes
    -----
    - The `super` sub-branch is not statically checked *here*.
        - It would widen the inferred intersection to:
            - `(<subclass of SchemaBase and TopLevelMixin> | super)`
        - Both dunder attributes are not in the `super` type stubs
            - Requiring 2x *# type: ignore[attr-defined]*
    - However it is required at runtime for any cases that use `super(..., copy)`.
    - The inferred type **is** used statically **outside** of this function.
    """
    if (isinstance(obj, SchemaBase) and isinstance(obj, TopLevelMixin)) or (
        not TYPE_CHECKING
        and (
            isinstance(obj, super)
            and issubclass(obj.__self_class__, SchemaBase)
            and obj.__thisclass__ is TopLevelMixin
        )
    ):
        return obj
    else:
        msg = f"{type(obj).__name__!r} does not derive from {SchemaBase.__name__!r}"
        raise TypeError(msg)
```

### 5.2 使用场景

`_top_schema_base` 在 `TopLevelMixin` 中被调用，主要用于两种场景：

**场景 1：调用继承自 `SchemaBase` 的方法**

```python
# 文件: altair/vegalite/v6/api.py, 行 2100
copy = _top_schema_base(self).copy(deep=False)
```

**场景 2：通过 `super()` 调用父类方法**

```python
# 文件: altair/vegalite/v6/api.py, 行 2114-2116
vegalite_spec: Any = _top_schema_base(super(TopLevelMixin, copy)).to_dict(
    validate=validate, ignore=ignore, context=dict(context, pre_transform=False)
)
```

### 5.3 设计原因分析

#### 5.3.1 多继承类型推断的限制

`Chart` 类的继承链：

```python
# 文件: altair/vegalite/v6/api.py, 行 4001-4002
class Chart(
    TopLevelMixin, _EncodingMixin, mixins.MarkMethodMixin, core.TopLevelUnitSpec
):
```

**继承关系**：
```
Chart
├── TopLevelMixin          # mixin，定义 to_dict() 等方法
├── _EncodingMixin         # mixin，定义 encode() 等方法
├── MarkMethodMixin        # mixin，定义 mark_*() 等方法
└── TopLevelUnitSpec       # schema 基类，继承自 SchemaBase
    └── SchemaBase         # 底层基类，定义 copy(), to_dict() 等
```

**问题**：
1. `TopLevelMixin` 重写了 `SchemaBase.to_dict()`
2. `TopLevelMixin.to_dict()` 需要调用 `super().to_dict()` 访问父类实现
3. 静态类型检查器（mypy/pyright）在多继承场景中难以正确推断类型
4. `TopLevelMixin` 本身不知道它会被哪些类继承

#### 5.3.2 静态类型检查的问题

`TopLevelMixin` 是一个 mixin，它的定义是：

```python
class TopLevelMixin(mixins.ConfigMethodMixin):
    """Mixin for top-level chart objects such as Chart, LayeredChart, etc."""
    
    def to_dict(self, ...):
        # self 的类型是什么？
        # 静态类型检查器只知道 self 是 TopLevelMixin
        # 但实际上 self 是 Chart（同时继承自 SchemaBase）
        
        # 问题：
        # 1. TopLevelMixin 没有定义 copy() 方法
        # 2. copy() 方法在 SchemaBase 中定义
        # 3. 静态类型检查器不知道 self 同时也是 SchemaBase 的子类
        
        # 所以这样的代码会有类型错误：
        copy = self.copy(deep=False)  # ❌ mypy: TopLevelMixin 没有 copy() 方法
        
        # 解决方案：使用 _top_schema_base 收窄类型
        copy = _top_schema_base(self).copy(deep=False)  # ✅ 类型安全
```

#### 5.3.3 `super()` 对象的特殊处理

`_top_schema_base` 还处理 `super()` 对象的情况：

```python
not TYPE_CHECKING
and (
    isinstance(obj, super)
    and issubclass(obj.__self_class__, SchemaBase)
    and obj.__thisclass__ is TopLevelMixin
)
```

**为什么需要处理 `super()` 对象？**

当调用 `super(TopLevelMixin, copy).to_dict()` 时：
- `super(TopLevelMixin, copy)` 返回一个 `super` 对象
- `super` 对象不是任何类的实例
- 静态类型检查器无法推断 `super` 对象的方法

**运行时检查**：
- `isinstance(obj, super)` - 检查是否是 `super` 对象
- `obj.__self_class__` - 获取 `super` 对象绑定的类（即 `Chart`）
- `issubclass(obj.__self_class__, SchemaBase)` - 检查是否继承自 `SchemaBase`
- `obj.__thisclass__ is TopLevelMixin` - 检查 `super()` 的第一个参数是否是 `TopLevelMixin`

#### 5.3.4 类型收窄的效果

使用 `_top_schema_base` 后：

```python
# 调用前：self 被推断为 TopLevelMixin 类型
# 静态检查器不知道 self 有 copy() 方法

# 调用后：
_top_schema_base(self)
# 返回类型被推断为: <subclass of SchemaBase and TopLevelMixin>
# 静态检查器知道返回值有 copy() 方法（来自 SchemaBase）

# 所以这样就类型安全了：
copy = _top_schema_base(self).copy(deep=False)
```

### 5.4 与其他跨混入类调用场景的对比

#### 5.4.1 什么时候需要类型收窄？

需要 `_top_schema_base` 这种模式的情况：

| 场景 | 是否需要类型收窄 | 原因 |
|------|-----------------|------|
| 调用继承自 `SchemaBase` 的方法（如 `copy()`） | 是 | Mixin 类本身不定义这些方法 |
| 通过 `super()` 调用父类方法 | 是 | `super` 对象类型难以推断 |
| 调用 mixin 自身定义的方法 | 否 | 类型已知 |
| 调用同层级其他 mixin 的方法 | 可能需要 | 取决于类型推断 |
| 访问 schema 定义的属性（如 `mark`, `encoding`） | 可能不需要 | 这些属性在类型定义中声明 |

#### 5.4.2 类型收窄的替代方案

为什么不使用其他方式？

**方案 A：使用 `cast()`**

```python
from typing import cast

copy = cast(SchemaBase, self).copy(deep=False)
```

问题：
- 失去了类型安全性（运行时不检查）
- 需要重复 cast
- 不如 `_top_schema_base` 表达意图清晰

**方案 B：使用类型绑定**

```python
from typing import TypeVar

T = TypeVar('T', bound='TopLevelMixin')

class TopLevelMixin(Generic[T]):
    # 复杂，且仍需要处理 super() 对象
```

**`_top_schema_base` 的优势**：
1. **运行时检查**：确保对象确实是 `SchemaBase` 和 `TopLevelMixin` 的子类
2. **静态类型收窄**：返回类型被推断为交集类型
3. **处理 `super()` 对象**：这是 `cast()` 无法做到的
4. **意图清晰**：函数名和文档字符串清楚地表达了用途

### 5.5 完整示例：`TopLevelMixin.to_dict()` 中的使用

```python
# 文件: altair/vegalite/v6/api.py, 行 2044-2150
class TopLevelMixin(mixins.ConfigMethodMixin):
    
    def to_dict(
        self,
        validate: bool = True,
        *,
        format: Literal["vega-lite", "vega"] = "vega-lite",
        ignore: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        
        # ... 省略参数处理 ...
        
        # 使用场景 1：调用 self.copy()
        # self 是 TopLevelMixin 类型，但 copy() 在 SchemaBase 中定义
        # 使用 _top_schema_base 收窄类型
        copy = _top_schema_base(self).copy(deep=False)
        
        # ... 省略数据处理 ...
        
        # 使用场景 2：通过 super() 调用父类的 to_dict()
        # super(TopLevelMixin, copy) 返回 super 对象
        # _top_schema_base 处理这种情况
        vegalite_spec: Any = _top_schema_base(super(TopLevelMixin, copy)).to_dict(
            validate=validate, ignore=ignore, context=dict(context, pre_transform=False)
        )
        
        # ... 省略后续处理 ...
        
        return vegalite_spec
```

**执行流程（场景 1）**：

```
调用: _top_schema_base(self).copy(deep=False)
      │
      ▼
1. _top_schema_base(self)
   │
   ├─→ isinstance(self, SchemaBase)? → 是（Chart → TopLevelUnitSpec → SchemaBase）
   │
   ├─→ isinstance(self, TopLevelMixin)? → 是
   │
   └─→ 返回 self（类型收窄为: SchemaBase & TopLevelMixin）
         │
         ▼
2. 调用 copy(deep=False)
   │
   └─→ 类型检查器现在知道返回值有 copy() 方法（来自 SchemaBase）
```

**执行流程（场景 2）**：

```
调用: _top_schema_base(super(TopLevelMixin, copy)).to_dict(...)
      │
      ▼
1. super(TopLevelMixin, copy)
   │
   └─→ 返回一个 super 对象，绑定到:
       - __thisclass__ = TopLevelMixin
       - __self_class__ = Chart（copy 的实际类型）
       - __self__ = copy
         │
         ▼
2. _top_schema_base(super 对象)
   │
   ├─→ isinstance(obj, SchemaBase)? → 否（super 不是 SchemaBase 实例）
   │
   ├─→ 检查 TYPE_CHECKING? → 运行时为 False
   │
   ├─→ isinstance(obj, super)? → 是
   │
   ├─→ issubclass(obj.__self_class__, SchemaBase)? → 是（Chart 继承自 SchemaBase）
   │
   ├─→ obj.__thisclass__ is TopLevelMixin? → 是
   │
   └─→ 返回 super 对象（类型检查器忽略此分支，但运行时有效）
         │
         ▼
3. 调用 to_dict(...)
   │
   └─→ super 对象的 to_dict() 调用 MRO 中的下一个类的方法
       即 TopLevelUnitSpec 或 SchemaBase 的 to_dict()
```

---

## 6. 代码位置汇总

| 功能 | 文件 | 行号 |
|------|------|------|
| `cond.get("test", cond)` 关键提取 | `altair/vegalite/v6/api.py` | 3229 |
| `_predicate_to_condition()` 分发函数 | `altair/vegalite/v6/api.py` | 633-659 |
| `PredicateComposition` 运算符重载 | `altair/vegalite/v6/schema/core.py` | 16317-16324 |
| `Parameter` 运算符重载（选择参数组合） | `altair/vegalite/v6/api.py` | 462-497 |
| `_is_test_predicate()` 类型检查 | `altair/vegalite/v6/api.py` | 624-625 |
| `Expression.to_dict()` 覆盖 | `altair/expr/core.py` | 226-227 |
| `_top_schema_base()` 类型收窄函数 | `altair/vegalite/v6/api.py` | 2006-2035 |
| `_top_schema_base()` 场景 1 使用 | `altair/vegalite/v6/api.py` | 2100 |
| `_top_schema_base()` 场景 2 使用 | `altair/vegalite/v6/api.py` | 2114 |
| 选择参数组合测试用例 | `tests/vegalite/v6/test_api.py` | 1267-1301 |
| 结构体谓词组合测试用例 | `tests/vegalite/v6/test_api.py` | 1304-1355 |
| 表达式组合测试用例 | `tests/vegalite/v6/test_api.py` | 1358-1362 |

---

## 7. 修正与补充总结

### 7.1 错误修正

**上一轮报告的错误**：
> 结构体谓词组合后的输出格式被错误地描述为 `{"filter": {"test": {"and": [...]}}}`

**正确的输出格式**：
> `{"filter": {"and": [...]}}`（无 `test` 包装）

**根本原因**：
- `_predicate_to_condition()` 返回 `{"test": 谓词对象}`
- 但 `transform_filter` 使用 `cond.get("test", cond)` 提取了谓词对象本身
- 谓词对象在序列化时直接调用 `to_dict()`

### 7.2 两种组合机制的对比

| 对比项 | 结构体谓词组合 | 选择参数组合 |
|--------|---------------|-------------|
| **运算符重载位置** | `PredicateComposition.__and__` 等 | `Parameter.__and__` 等 |
| **返回类型** | `PredicateComposition` | `PredicateComposition`（相同！） |
| **_is_test_predicate 检查** | 通过 | 通过 |
| **最终输出** | 无 `test` 包装 | 无 `test` 包装 |

**核心结论**：两种组合机制**最终返回相同类型**（`PredicateComposition`），走**相同的分发路径**，最终输出**都没有 `test` 包装**。

### 7.3 为什么组合谓词不需要 `test` 包装？

**关键发现**：

1. **所有谓词类型在 `_predicate_to_condition()` 中都被包装为 `{"test": ...}` 格式**
   - 组合谓词：`{"test": PredicateComposition 对象}`
   - 表达式对象：`{"test": BinaryExpression 对象}`
   - 字符串：`{"test": "datum.x > 0"}`
   - 单个选择参数：**例外**，直接返回 `{"param": ...}`

2. **`transform_filter` 使用 `cond.get("test", cond)` 提取值**
   - 提取出的是**值本身**，不是包装后的字典
   - 所以所有谓词类型的最终输出**都没有 `test` 包装**

3. **真正的差异在于序列化方式**
   - `PredicateComposition.to_dict()` → 返回 `{"and": [...]}` **字典**
   - `Expression.to_dict()` → 被覆盖为返回 `"(datum.x > 0)"` **字符串**

| 谓词类型 | 提取出的值 | 序列化后的值 | 最终输出 |
|---------|-----------|-------------|----------|
| 组合谓词 | `PredicateComposition` 对象 | `{"and": [...]}` 字典 | `{"filter": {"and": [...]}}` |
| 表达式对象 | `BinaryExpression` 对象 | `"(datum.x > 0)"` 字符串 | `{"filter": "(datum.x > 0)"}` |
| 字符串 | `"datum.x > 0"` 字符串 | 同左 | `{"filter": "datum.x > 0"}` |
| 单个选择参数 | `{"param": "s1"}` 字典 | 同左 | `{"filter": {"param": "s1"}}` |

### 7.4 唯一有 `test` 包装的场景

**只有在 `alt.when()` / `alt.condition()` 中才会保留 `test` 包装**：

| 上下文 | 处理方式 | 最终格式 |
|--------|---------|---------|
| `transform_filter` | `cond.get("test", cond)` | 提取值本身，无 `test` 包装 |
| `alt.when()` / `alt.condition()` | 直接使用 `condition` | 保留 `{"test": ...}` 包装 |

**原因**：这是 Vega-Lite 规范的要求。`FilterTransform` 可以直接接受谓词对象或字符串，而 `condition` 字段需要 `{"test": ...}` 格式。

### 7.5 `_top_schema_base` 设计原因

**存在的原因**：
1. **多继承类型推断限制**：静态类型检查器无法知道 `TopLevelMixin` 中的 `self` 实际上是 `Chart`（同时继承自 `SchemaBase`）
2. **`super()` 对象的特殊性**：`super` 对象不是任何类的实例，运行时需要特殊处理
3. **类型安全**：提供运行时检查，确保对象确实是预期类型

**解决的问题**：
- 调用继承自 `SchemaBase` 的方法（如 `copy()`）时的类型错误
- 通过 `super()` 调用父类方法时的类型推断问题

**相比 `cast()` 的优势**：
- 运行时类型检查（`cast()` 只在静态时生效）
- 支持 `super()` 对象（`cast()` 无法处理）
- 意图更清晰
