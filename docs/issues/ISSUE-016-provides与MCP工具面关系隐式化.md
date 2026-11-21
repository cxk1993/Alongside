# ISSUE-016 · 「provides 声明」与「MCP 工具面」的关系是隐式的（AI 可及范围没有显式策略）

- **提出者**：bidiu（2026-11-22 夜班工具面审计）
- **影响谁**：所有往 manifest 里加 `provides` 的插件作者；以及决定「哪些能力给 AI」的人
- **状态**：open
- **优先级**：normal（当前结果安全，但**靠巧合**）
- **日期**：2026-11-22

---

## 我遇到的现象

夜班工具面审计（静态扫 43 个 manifest 的 119 条 `provides`）发现：

| verb | 条数 | 在 MCP 工具面吗 | 在提案拦截表吗 |
|---|---|---|---|
| `read` / `list` / `today` / `peek` / `search` … | 45 | ✅ 在 | —（只读，无需） |
| `write` / `create` / `update` / `patch` / `delete` / `remove` | 77 | ✅ 在 | ✅ 拦（缺省开） |
| `dispatch` / `report` | 2 | ✅ 在 | ❌ 豁免（编排动作，共识） |
| `approve` / `reject` / `undo` | 3 | ❌ **已从工具面移除** | ❌ 豁免（人类专属，403） |
| **`install` / `uninstall` / `enable` / `disable` / `register` / `unregister` / `assemble` / `manage`** | **8** | ❌ **不在** | ❌ **不在** |

最后一行是真栈实测：`tools/list` 91 个工具里，插件/包相关的只有 `plugins_settings_patch`（verb=`patch` → 被提案拦截 ✅）。
**`plugins_plugin_install` / `plugin.enable` / `pack.assemble` 这些工具根本不存在。**

## 为什么这是个问题（即使现在结果安全）

**「AI 不能装卸插件」是对的**——但当前不是因为它被拦，而是因为 `derive_tool` **不认识这些 verb**（没有 method 映射）→ 静默丢弃。

也就是说：
- 哪天有人给 `derive_tool` 的 verb→method 表加上 `install: POST`（比如为了给 REST 用统一映射），**这 8 个能力会一次性全部暴露给 AI**，而没有任何拦截（它们不在 `_PROPOSABLE_VERBS`，也不在 `_HUMAN_ONLY_VERBS`）。
- 这是「**安全靠巧合**」——与 S-A（AI 自签）同一类病：当时也是因为「approve 不在 `_PROPOSABLE_VERBS`」而直接通。

判据 5 的延伸：**声明（provides）与可及性（工具面）之间必须有一条明说的规则**，不能靠「表里没这个 verb 所以丢弃」。

## 建议改法（两选一，需先拍板策略）

**方案 A（推荐）· 显式暴露白名单**
manifest 增加可选字段：
```json
"mcp": { "expose": ["todo.item.read", "todo.item.write"], "deny": ["plugin.install"] }
```
或更简单：`provides` 里给不想暴露的条目加前缀/后缀标记。`derive_tool` 只生成白名单内的。

**方案 B · verb 分类表集中化**
在 `registry_adapter.py` 维护三张显式集合，并在**门禁里拦住未分类的新 verb**：
```python
_READ_VERBS = {...}
_WRITE_VERBS = {...}          # 落审计
_PROPOSABLE_VERBS = {...}     # 走提案
_HUMAN_ONLY_VERBS = {...}     # 403
_NEVER_EXPOSE_VERBS = {"install","uninstall","enable","disable","register","unregister","assemble","manage"}
```
新插件引入未归类的 verb → 门禁报错（强制作者/评审做一次决定）。

**无论选哪个**：`install` 类 8 个 verb 应进 `_HUMAN_ONLY_VERBS` 或 `_NEVER_EXPOSE_VERBS`——**显式地**，不是靠丢弃。

## 验证方式

- 现在：`tools/list` 内 `plugins_*` 只有 `plugins_settings_patch`（已实测）
- 改后：新增门禁测试——给假 manifest 加一条 `plugin.install` provides → 断言它**不出现在工具面**，且断言**现有 91 工具数不变**
- 回归：`test_mcp.py` 全绿 + 工具数快照

## 相关

- S-A（`75908d1`）：AI 自签——同类「不在拦截表就直通」的病
- 待决 #6（凝析）：proposal 机制两处用（装插件/改布局的审批面）——**这张卡就是它的第一块砖**
- `registry_adapter.py` 的 `derive_tool` / `WRITE_VERBS`
- 判据 41（新增）：**安全不能靠巧合**——「它没被暴露」必须是显式决定，不是映射表里恰好没有