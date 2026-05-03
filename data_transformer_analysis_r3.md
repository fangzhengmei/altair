# Altair 数据转换器保存导出场景深度分析（修订版）

## 1. 核心问题澄清

### 1.1 两个独立的预转换入口点

**这是理解整个链路的关键！**

VegaFusion 的预转换存在两个完全独立的入口点：

| 入口点 | 位置 | 触发条件 | 受 `pre_transform` 控制？ |
|--------|------|---------|--------------------------|
| **入口 A** | `TopLevelMixin.to_dict()` 末尾 | `context.get("pre_transform", True)` **且** `using_vegafusion()` | **是** |
| **入口 B** | `spec_to_mimebundle()` 开头 | **仅** `using_vegafusion()` | **否** |

### 1.2 为什么 `pre_transform=False` 后仍会进入预转换路径？

```python
# save.py 中
def perform_save() -> None:
    spec = chart.to_dict(context={"pre_transform": False})
    # ↑ 这里只跳过了【入口 A】
    
    if format in {"html", "png", "svg", "pdf", "vega"}:
        _save_mimebundle_format(...)
        # ↓ 这里会进入 spec_to_mimebundle()
        #   其中【入口 B】不受 pre_transform 控制！
```

**答案**：
- `pre_transform=False` 只跳过 `TopLevelMixin.to_dict()` 中的预转换（入口 A）
- 但对于 `html/png/svg/pdf/vega` 格式，后续会调用 `spec_to_mimebundle()`
- 在 `spec_to_mimebundle()` 中，**只要 `using_vegafusion()` 为 True，就会触发预转换（入口 B）**
- 入口 B **完全不检查** `context["pre_transform"]`！

---

## 2. 完整执行链路总览

### 2.1 决策流程图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        chart.save() 入口                                   │
│                        (altair/utils/save.py)                             │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Step 1: 判断当前激活的数据转换器                                           │
│                                                                          │
│  if using_vegafusion():                                                  │
│      ├── 保持 vegafusion 转换器不变                                       │
│      └── 仅禁用 max_rows 检查（通过 disable_max_rows()）                  │
│  else:                                                                    │
│      ├── 强制切换到 default 转换器                                        │
│      │   (json/csv 转换器写入的本地文件无法被 vl-convert 访问)            │
│      └── 同时禁用 max_rows 检查                                           │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Step 2: perform_save() 内部执行                                          │
│                                                                          │
│  spec = chart.to_dict(context={"pre_transform": False})                 │
│         │                                                                │
│         └──► 调用 TopLevelMixin.to_dict()                                │
│              │                                                           │
│              ├──► _prepare_data() - 调用当前激活的数据转换器              │
│              │                                                           │
│              └──► 检查预转换条件（入口 A）：                                │
│                   if context.get("pre_transform", True)                  │
│                      and using_vegafusion():                             │
│                       └── 由于 pre_transform=False，这里被跳过！          │
│                                                           │               │
│                                                           ▼               │
│                                                    返回 Vega-Lite spec     │
│                                                    (数据可能是 URL 引用)   │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Step 3: 根据目标格式分支处理                                              │
│                                                                          │
│  ┌──────────────┐                                                        │
│  │ format="json"│                                                        │
│  └──────────────┘                                                        │
│         │                                                                │
│         ▼                                                                │
│  直接 json.dumps(spec) 写入文件                                          │
│  ────────────────────────                                                │
│  • 不经过 spec_to_mimebundle()                                           │
│  • 【入口 B】永远不会触发                                                  │
│  • spec 中的数据保持原样（URL 或内联）                                    │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ format in {"html", "png", "svg", "pdf", "vega"}                 │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│         │                                                                │
│         ▼                                                                │
│  调用 _save_mimebundle_format()                                         │
│         │                                                                │
│         └──► spec_to_mimebundle(spec, format=..., mode="vega-lite")   │
│              │                                                           │
│              └──► 【入口 B】检查：                                         │
│                   if using_vegafusion():                                 │
│                       spec = compile_with_vegafusion(spec)  ← 触发！  │
│                       internal_mode = "vega"                             │
│                                                           │               │
│                                                           ▼               │
│                                                    根据 format 继续处理    │
│                                                    (vl-convert 渲染)      │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 分场景详细执行链路

### 3.1 场景 A：启用 vegafusion 转换器 + 保存为 PNG

```python
alt.data_transformers.enable('vegafusion')
chart.save('chart.png')
```

#### 完整执行步骤

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Phase 1: save() 中的转换器策略选择                                        │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ using_vegafusion() → True (data_transformers.active == "vegafusion")    │
│                                                                          │
│ 因此执行:                                                                 │
│ with data_transformers.disable_max_rows():                              │
│     perform_save()                                                       │
│                                                                          │
│ 注意：disable_max_rows() 做了什么？                                       │
│ ├── 检查 self.active in {"default", "vegafusion"} → True               │
│ ├── options.copy() → options["max_rows"] = None                        │
│ └── return self.enable(**options)                                        │
│                                                                          │
│ 结果：                                                                    │
│ • 转换器仍为 "vegafusion"                                                 │
│ • 但 options = {"max_rows": None}                                        │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ Phase 2: perform_save() → chart.to_dict(context={"pre_transform": False})│
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ TopLevelMixin.to_dict() 内部执行                                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│ 2.1 初始化 context:                                                       │
│     context = {"pre_transform": False}  (用户传入)                       │
│     context.setdefault("datasets", {})                                   │
│     → context = {"pre_transform": False, "datasets": {}}                │
│                                                                          │
│ 2.2 调用 _prepare_data(data, context):                                    │
│     │                                                                    │
│     └──► data_transformers.get()                                         │
│          │                                                               │
│          ├── self._active = vegafusion_data_transformer                 │
│          └── self._options = {"max_rows": None}                         │
│          └── 因为 _options 非空，返回 partial(vegafusion_data_transformer,│
│                                              max_rows=None)              │
│          │                                                               │
│          └──► 调用 vegafusion_data_transformer(df, max_rows=None)        │
│               │                                                          │
│               ├── 检查 is_supported_by_vf(data) → True                  │
│               ├── 生成 UUID: table_123e4567_89ab...                    │
│               ├── 存储: extracted_inline_tables["table_xxx"] = df       │
│               └── 返回: {"url": "vegafusion+dataset://table_xxx"}       │
│                                                                          │
│ 2.3 递归调用父类 to_dict():                                               │
│     super().to_dict(..., context=dict(context, pre_transform=False))    │
│                                                                          │
│ 2.4 检查预转换条件（入口 A）：                                              │
│     if context.get("pre_transform", True) and _using_vegafusion():     │
│         ├── context["pre_transform"] = False                             │
│         └── 条件为 False → **跳过** _compile_with_vegafusion()          │
│                                                                          │
│ 2.5 返回的 spec:                                                          │
│     {                                                                     │
│       "$schema": "...",                                                   │
│       "data": {"url": "vegafusion+dataset://table_xxx"},  ← 特殊 URL    │
│       "mark": "point",                                                    │
│       ...                                                                 │
│     }                                                                     │
│     注意：数据仍是 URL 引用，**没有被预转换！**                              │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ Phase 3: 因为 format="png"，调用 _save_mimebundle_format()               │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 调用 spec_to_mimebundle(                                                  │
│     spec=spec,                      ← 包含 vegafusion+dataset:// URL     │
│     format="png",                                                        │
│     mode="vega-lite",                                                    │
│     ...                                                                   │
│ )                                                                         │
│ (altair/utils/mimebundle.py:65)                                          │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ spec_to_mimebundle() 内部执行                                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│ 3.1 检查 using_vegafusion() → True!                                      │
│                                                                          │
│ 3.2 ⚠️ 【入口 B】触发预转换！                                              │
│     注意：这里**完全不检查** context["pre_transform"]！                    │
│                                                                          │
│     if using_vegafusion():                                               │
│         spec = compile_with_vegafusion(spec)  ← 执行！                  │
│         internal_mode = "vega"                                           │
│                                                                          │
│ 3.3 compile_with_vegafusion() 详细过程：                                  │
│     │                                                                    │
│     ├── 步骤1: 编译 Vega-Lite → Vega                                      │
│     │    compiler = vegalite_compilers.get()  # vl-convert              │
│     │    vega_spec = compiler(vegalite_spec)                             │
│     │                                                                    │
│     ├── 步骤2: 提取内联表                                                  │
│     │    inline_tables = get_inline_tables(vega_spec)                   │
│     │    │                                                               │
│     │    ├── get_inline_table_names(vega_spec)                           │
│     │    │   └── 查找 URL 以 "vegafusion+dataset://" 开头的节点         │
│     │    │   └── 返回 {"table_xxx"}                                      │
│     │    │                                                               │
│     │    └── 从 WeakValueDictionary 取出并删除：                          │
│     │        return {k: extracted_inline_tables.pop(k) for k in table_names}│
│     │                                                                    │
│     ├── 步骤3: 获取行限制配置                                              │
│     │    row_limit = data_transformers.options.get("max_rows", None)    │
│     │    → options = {"max_rows": None} → row_limit = None              │
│     │                                                                    │
│     ├── 步骤4: 执行 VegaFusion 预转换                                      │
│     │    transformed_vega_spec, warnings = vf.runtime.pre_transform_spec(│
│     │        vega_spec,                                                   │
│     │        vf.get_local_tz(),                                           │
│     │        inline_datasets=inline_tables,  ← 原始 DataFrame            │
│     │        row_limit=row_limit,            ← None (无限制)             │
│     │    )                                                                 │
│     │                                                                    │
│     └── 步骤5: 检查警告（如果 row_limit 不是 None）                        │
│          handle_row_limit_exceeded(row_limit, warnings)                  │
│          → row_limit = None，不会抛出 MaxRowsError                       │
│                                                                          │
│ 3.4 返回的 spec：                                                          │
│     {                                                                     │
│       "$schema": "...",                                                   │
│       "data": {"values": [...]},  ← 数据已内联！预转换完成！              │
│       ...                                                                 │
│     }                                                                     │
│     注意：与 Phase 2 返回的 spec 不同！                                     │
│          这里的 data 已经是转换后的结果                                     │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ Phase 4: vl-convert 渲染为 PNG                                            │
│                                                                          │
│ 因为 format="png" 且 internal_mode="vega":                               │
│                                                                          │
│ png = vlc.vega_to_png(                                                   │
│     spec,                    ← 已预转换的 Vega spec                       │
│     scale=scale_factor,                                                  │
│     ppi=ppi,                                                             │
│     format_locale=...,                                                   │
│     time_format_locale=...,                                              │
│ )                                                                         │
│                                                                          │
│ 注意：vl-convert 不需要访问任何外部文件！                                   │
│      因为数据已经内联在 spec 中了                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 3.2 场景 B：启用 json 转换器 + 保存为 PNG

```python
alt.data_transformers.enable('json')
chart.save('chart.png')
```

#### 关键差异点

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Phase 1: save() 中的转换器策略选择                                        │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│ using_vegafusion() → False (data_transformers.active == "json")         │
│                                                                          │
│ 因此执行:                                                                 │
│ with data_transformers.enable("default"), \                             │
│      data_transformers.disable_max_rows():                              │
│     perform_save()                                                       │
│                                                                          │
│ ⚠️ 关键差异：                                                             │
│ • json 转换器被**强制切换**到 default！                                    │
│ • 原因：vl-convert 运行在独立进程中，无法访问 Python 脚本写入的本地 JSON 文件│
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ Phase 2: perform_save() → chart.to_dict()                                │
│                                                                          │
│ 此时激活的转换器是 "default"，不是 "json"！                                │
│                                                                          │
│ _prepare_data() 调用的是 default_data_transformer：                       │
│ ├── limit_rows(data, max_rows=None)  ← 不检查行数                        │
│ └── to_values(data)                                                      │
│     └── 返回 {"values": [...]}  ← 数据内联！                             │
│                                                                          │
│ 返回的 spec:                                                              │
│ {                                                                        │
│   "data": {"values": [...]},  ← 已内联                                   │
│   ...                                                                    │
│ }                                                                        │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ Phase 3: spec_to_mimebundle()                                            │
│                                                                          │
│ using_vegafusion() → False                                               │
│                                                                          │
│ 因此：                                                                    │
│ • 不会调用 compile_with_vegafusion()                                     │
│ • internal_mode 保持为 "vega-lite"                                       │
│                                                                          │
│ 直接调用 vl-convert:                                                      │
│ png = vlc.vegalite_to_png(                                              │
│     spec,                    ← Vega-Lite spec，数据已内联                │
│     vl_version=...,                                                      │
│     ...                                                                   │
│ )                                                                        │
└─────────────────────────────────────────────────────────────────────────┘
```

### 3.3 场景 C：启用 vegafusion + 保存为 JSON

```python
alt.data_transformers.enable('vegafusion')
chart.save('chart.json')
```

#### 关键差异：JSON 格式不经过 `spec_to_mimebundle()`

```
┌─────────────────────────────────────────────────────────────────────────┐
│ save() 中 format="json" 的分支：                                          │
│                                                                          │
│ if format == "json":                                                     │
│     json_spec = json.dumps(spec, **json_kwds)                          │
│     write_file_or_filename(fp, json_spec, ...)                          │
│     ↑                                                                    │
│     └── 直接保存，**不调用** spec_to_mimebundle()！                       │
│                                                                          │
│ 因此：                                                                    │
│ • 【入口 B】永远不会触发                                                  │
│ • 【入口 A】已被 pre_transform=False 跳过                                 │
│ • 结果：保存的 JSON 文件中包含 vegafusion+dataset:// URL！                │
│   这样的文件**无法独立使用**！                                             │
└─────────────────────────────────────────────────────────────────────────┘
```

**⚠️ 重要警告**：

```python
# 这样保存的 JSON 文件是无效的：
chart.save('chart.json')
# 文件内容：
# {
#   "data": {"url": "vegafusion+dataset://table_xxx"},  ← 无效！
#   ...
# }

# 如果想保存包含实际数据的 JSON，应该：
# 方法1: 使用 format="vega"
chart.save('chart.vega.json')  # 或 chart.to_dict(format="vega")

# 方法2: 切换到 default 转换器
with alt.data_transformers.enable('default'):
    chart.save('chart.json')
```

---

## 4. 四种策略在保存场景下的行为总结

### 4.1 行为对比表

| 转换器 | save 时是否被切换 | 数据在 spec 中的形式 | 预转换触发点 | 最终输出中的数据 |
|--------|------------------|---------------------|-------------|-----------------|
| **default** | 保持不变 | 内联 `{"values": [...]}` | 无 | 内联 |
| **json** | **强制切换到 default** | 内联（切换后的结果） | 无 | 内联 |
| **csv** | **强制切换到 default** | 内联（切换后的结果） | 无 | 内联 |
| **vegafusion** | 保持不变 | 特殊 URL `{"url": "vegafusion+dataset://..."}` | 入口 B (mimebundle) | **取决于格式** |

### 4.2 vegafusion + 不同保存格式的详细对比

| 保存格式 | 是否经过 spec_to_mimebundle() | 入口 B 是否触发 | 最终 spec 中的数据 | 输出文件可用性 |
|---------|------------------------------|-----------------|-------------------|---------------|
| **json** | ❌ 否 | ❌ 否 | `vegafusion+dataset://...` URL | ⚠️ **无效**（无法独立使用） |
| **vega** | ✅ 是 | ✅ 是 | 内联 `{"values": [...]}` | ✅ 有效 |
| **html** | ✅ 是 | ✅ 是 | 内联 `{"values": [...]}` | ✅ 有效 |
| **png** | ✅ 是 | ✅ 是 | 内联 `{"values": [...]}` | ✅ 有效 |
| **svg** | ✅ 是 | ✅ 是 | 内联 `{"values": [...]}` | ✅ 有效 |
| **pdf** | ✅ 是 | ✅ 是 | 内联 `{"values": [...]}` | ✅ 有效 |

---

## 5. 两个预转换入口点的详细对比

### 5.1 入口 A：TopLevelMixin.to_dict() 末尾

**位置**: `altair/vegalite/v6/api.py:2137-2155`

```python
# 代码片段
if context.get("pre_transform", True) and _using_vegafusion():
    if format == "vega-lite":
        raise ValueError(
            'When the "vegafusion" data transformer is enabled, '
            'must use format="vega"'
        )
    else:
        return _compile_with_vegafusion(vegalite_spec)
```

**触发条件分析**:

| 条件 | 来源 | 默认值 | 在 save() 中 |
|------|------|--------|-------------|
| `context.get("pre_transform", True)` | 用户传入的 context 或默认 | `True` | `False`（显式传入） |
| `_using_vegafusion()` | `data_transformers.active == "vegafusion"` | - | 取决于用户设置 |

**在 save() 中的行为**：
- 因为 `perform_save()` 显式传入 `context={"pre_transform": False}`
- 所以 `context.get("pre_transform", True)` 返回 `False`
- **入口 A 永远不会在 save() 中触发**

### 5.2 入口 B：spec_to_mimebundle() 开头

**位置**: `altair/utils/mimebundle.py:123-125`

```python
# 代码片段
if using_vegafusion():
    spec = compile_with_vegafusion(spec)
    internal_mode = "vega"
```

**触发条件分析**:

| 条件 | 来源 | 默认值 | 在 save() 中 |
|------|------|--------|-------------|
| `using_vegafusion()` | `data_transformers.active == "vegafusion"` | - | 取决于用户设置 |

**关键发现**：
- **完全不检查** `context["pre_transform"]`！
- 只要 `data_transformers.active == "vegafusion"`，就触发预转换
- **这就是为什么 pre_transform=False 后仍会进入预转换路径**

### 5.3 入口 A 与入口 B 的关系图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      用户直接调用 to_dict()                                │
│                      (不经过 save())                                       │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
                    ┌───────────────────────────────┐
                    │ TopLevelMixin.to_dict()       │
                    │                               │
                    │ context 参数由用户决定         │
                    │ 默认: context = None          │
                    │       → 视为 {}                │
                    └───────────────────────────────┘
                                    │
                                    ▼
                    ┌───────────────────────────────┐
                    │ 入口 A 检查条件：               │
                    │                               │
                    │ context.get("pre_transform",  │
                    │         True)                  │
                    │ AND using_vegafusion()        │
                    └───────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
           ┌──────────────┐                ┌──────────────┐
           │   条件满足   │                │   条件不满足 │
           │              │                │              │
           │ pre_transform│                │ pre_transform│
           │ 未显式传入   │                │ = False      │
           │ 且启用了 vf  │                │ 或未启用 vf  │
           └──────────────┘                └──────────────┘
                    │                               │
                    ▼                               │
           ┌──────────────────┐                    │
           │ 触发             │                    │
           │ _compile_with_   │                    │
           │ vegafusion()     │                    │
           └──────────────────┘                    │
                    │                               │
                    └───────────────┬───────────────┘
                                    ▼
                    ┌───────────────────────────────┐
                    │ 返回 Vega 或 Vega-Lite spec   │
                    │ (取决于是否预转换)             │
                    └───────────────────────────────┘
                                    │
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      后续处理（如渲染）                                    │
│                                                                          │
│ 如果用户继续调用：                                                         │
│ • chart.to_html()                                                        │
│ • chart.save('chart.png')                                                │
│ • Jupyter _repr_mimebundle_()                                            │
│                                                                          │
│ 这些都会经过：                                                             │
│ ├──► spec_to_mimebundle()                                                │
│      │                                                                   │
│      └──► 入口 B 检查条件：                                                │
│           仅检查 using_vegafusion()                                      │
│           完全不关心之前是否已经预转换过！                                   │
│                                                                          │
│ ⚠️ 这意味着：                                                             │
│ • 即使入口 A 已经执行了预转换                                              │
│ • 入口 B 可能再次执行！                                                    │
│ • 但实际上 compile_with_vegafusion() 是幂等的吗？                         │
│   答案：不是，因为 WeakValueDictionary 中的数据已被 pop 取出！              │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 6. 完整的调用链路时间线

### 6.1 时间线：vegafusion + save('chart.png')

```
时间轴 ───────────────────────────────────────────────────────────────────►

T0: 用户调用
    chart.save('chart.png')

T1: save() 入口 (altair/utils/save.py)
    │
    ├── 判断 format → "png"
    │
    ├── 判断 using_vegafusion() → True
    │
    └── 进入 with 上下文管理器：
        with data_transformers.disable_max_rows():
            perform_save()

T2: disable_max_rows() 内部
    │
    ├── 检查 self.active in {"default", "vegafusion"} → True
    │
    ├── options = self.options.copy() → {} （空）
    │
    ├── options["max_rows"] = None
    │
    └── 调用 self.enable(**options)
         │
         ├── _enable("vegafusion", max_rows=None)
         │
         └── 结果：
              • self._active_name = "vegafusion"
              • self._options = {"max_rows": None}

T3: perform_save() 内部
    │
    └── spec = chart.to_dict(context={"pre_transform": False})

T4: TopLevelMixin.to_dict() 执行
    │
    ├── context = {"pre_transform": False, "datasets": {}}
    │
    ├── _prepare_data(data, context)
    │    │
    │    ├── data_transformers.get()
    │    │    → partial(vegafusion_data_transformer, max_rows=None)
    │    │
    │    └── 调用转换器
    │         ├── 生成 UUID 表名
    │         ├── extracted_inline_tables["table_xxx"] = df
    │         └── 返回 {"url": "vegafusion+dataset://table_xxx"}
    │
    ├── 递归调用父类 to_dict()
    │
    └── 检查入口 A 条件：
         context.get("pre_transform", True) → False
         → 跳过 _compile_with_vegafusion()

T5: 返回 spec（数据为 URL 引用）
    spec = {
        "data": {"url": "vegafusion+dataset://table_xxx"},
        "mark": "point",
        ...
    }

T6: 因为 format="png"，调用 _save_mimebundle_format()
    │
    └── spec_to_mimebundle(spec, format="png", mode="vega-lite", ...)

T7: spec_to_mimebundle() 内部 (altair/utils/mimebundle.py)
    │
    ├── 判断 using_vegafusion() → True
    │
    └── ⚠️ 入口 B 触发！
         spec = compile_with_vegafusion(spec)
         │
         ├── 编译 Vega-Lite → Vega (vl-convert)
         │
         ├── get_inline_tables(vega_spec)
         │    └── 从 extracted_inline_tables 中 pop 取出 df
         │
         ├── row_limit = options.get("max_rows", None) → None
         │
         └── vf.runtime.pre_transform_spec(...)
              └── 执行预转换，数据内联

T8: 返回预转换后的 spec
    spec = {
        "data": {"values": [...]},  ← 数据已内联！
        ...
    }

T9: 继续处理 PNG 渲染
    │
    └── vlc.vega_to_png(spec, ...)
         └── 返回 PNG 二进制数据

T10: 写入文件
     write_file_or_filename(fp, png_data, mode="wb")
```

---

## 7. 关键代码位置速查表

| 功能 | 文件位置 | 关键函数/代码 |
|------|---------|--------------|
| save() 主函数 | `altair/utils/save.py:171` | `def save(...)` |
| 转换器切换逻辑 | `altair/utils/save.py:271-284` | `if using_vegafusion(): ... else: ...` |
| perform_save 内部 | `altair/utils/save.py:240-269` | `def perform_save(): ... chart.to_dict(context={"pre_transform": False})` |
| 入口 A（to_dict 内） | `altair/vegalite/v6/api.py:2137` | `if context.get("pre_transform", True) and _using_vegafusion():` |
| 入口 B（mimebundle） | `altair/utils/mimebundle.py:123` | `if using_vegafusion(): spec = compile_with_vegafusion(spec)` |
| disable_max_rows | `altair/vegalite/data.py:47-54` | `def disable_max_rows(self): ...` |
| vegafusion 数据转换 | `altair/utils/_vegafusion_data.py:90` | `def vegafusion_data_transformer(...)` |
| 预编译（核心） | `altair/utils/_vegafusion_data.py:235` | `def compile_with_vegafusion(...)` |
| using_vegafusion 判断 | `altair/utils/_vegafusion_data.py:299` | `return data_transformers.active == "vegafusion"` |

---

## 8. 常见问题与最佳实践

### 8.1 问题：为什么 pre_transform=False 后仍会预转换？

**答案**：
- `pre_transform` 只控制 `TopLevelMixin.to_dict()` 中的入口 A
- 对于 `html/png/svg/pdf/vega` 格式，后续会经过 `spec_to_mimebundle()`
- 在 `spec_to_mimebundle()` 中，入口 B 只检查 `using_vegafusion()`，**完全不检查** `pre_transform`

### 8.2 问题：启用 json 转换器后 save('chart.png') 为什么数据被内联了？

**答案**：
- `save()` 中，如果 `using_vegafusion()` 为 False，会强制切换到 default 转换器
- 原因：json/csv 转换器写入的本地文件无法被 vl-convert（独立进程）访问
- 因此，不管用户启用的是 json 还是 csv，save 时都会切换到 default

### 8.3 问题：启用 vegafusion 后 save('chart.json') 得到的文件为什么无法独立使用？

**答案**：
- `format="json"` 不会经过 `spec_to_mimebundle()`
- 入口 A 被 `pre_transform=False` 跳过
- 入口 B 不会触发
- 结果：JSON 文件中包含 `vegafusion+dataset://` URL，这是无效的外部引用

**解决方案**：
```python
# 方法1: 使用 format="vega"
chart.save('chart.vega.json')

# 方法2: 切换到 default 转换器
with alt.data_transformers.enable('default'):
    chart.save('chart.json')

# 方法3: 使用 to_dict(format="vega") 然后自己序列化
import json
spec = chart.to_dict(format="vega")
with open('chart.json', 'w') as f:
    json.dump(spec, f)
```

### 8.4 最佳实践：不同场景的转换器选择

| 场景 | 推荐转换器 | 原因 |
|------|-----------|------|
| 小数据集 (< 5000 行) | `default` | 最简单，无外部依赖 |
| 中等数据集 + Jupyter Notebook | `json` | 减少 notebook 体积，加快保存 |
| 中等数据集 + 需要保存为 PNG/HTML | `default` 或 `vegafusion` | json/csv 会被强制切换，不如直接用 default |
| 大数据集 + 聚合图表 | `vegafusion` | 预转换后数据量大幅减少 |
| 大数据集 + 交互式探索 | `vegafusion` + `JupyterChart` 或 `renderers.enable('jupyter')` | 支持服务端动态计算 |
| 需要保存包含完整数据的 JSON | `default` 或 `to_dict(format="vega")` | vegafusion + format="json" 会产生无效文件 |

### 8.5 最佳实践：保存场景的检查清单

保存图表前，建议确认以下几点：

```python
import altair as alt

# 检查当前激活的转换器
print(f"当前转换器: {alt.data_transformers.active}")
print(f"当前 options: {alt.data_transformers.options}")

# 场景1: 保存为 JSON（非 Vega 格式）
# ⚠️ 如果使用 vegafusion，结果会包含无效的 URL！
if alt.data_transformers.active == "vegafusion":
    # 推荐使用 format="vega"
    chart.save('chart.vega.json')
    # 或手动切换
    with alt.data_transformers.enable('default'):
        chart.save('chart.json')
else:
    chart.save('chart.json')

# 场景2: 保存为 PNG/SVG/HTML
# 对于 vegafusion：会在 spec_to_mimebundle 中预转换，数据内联
# 对于 json/csv：会被强制切换到 default
chart.save('chart.png')  # 无论哪种转换器，结果都是正确的

# 场景3: 只想获取预转换后的 spec
# 必须使用 format="vega"
spec = chart.to_dict(format="vega")  # 包含内联的、预转换后的数据
```

---

## 9. 修正总结（对比 R2 版）

### 9.1 R2 版中的不准确之处

| 问题 | R2 版描述 | 实际情况 |
|------|----------|---------|
| **pre_transform 的作用范围** | 描述为"跳过预转换" | 只跳过入口 A，不影响入口 B |
| **save() 中 vegafusion 的行为** | 描述为"不执行预转换" | 实际上会在入口 B 执行预转换 |
| **json/csv 转换器的切换** | 描述为"强制切换"（正确） | 原因是 vl-convert 无法访问本地文件 |
| **JSON 格式保存** | 未提及特殊情况 | format="json" 不经过 mimebundle，入口 B 不会触发 |

### 9.2 核心修正点

1. **两个独立的预转换入口**：
   - 入口 A：`TopLevelMixin.to_dict()` 末尾，受 `pre_transform` 控制
   - 入口 B：`spec_to_mimebundle()` 开头，**不受** `pre_transform` 控制

2. **save() 中的实际行为**：
   - `pre_transform=False` 只跳过入口 A
   - 对于 `html/png/svg/pdf/vega` 格式，入口 B 仍会触发

3. **JSON 格式的特殊情况**：
   - `format="json"` 不经过 `spec_to_mimebundle()`
   - 因此入口 B 永远不会触发
   - 如果使用 vegafusion，保存的 JSON 文件包含无效的 URL
