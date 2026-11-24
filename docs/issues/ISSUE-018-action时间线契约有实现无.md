# ISSUE-018 · `action.schema.json` 已冻结但「实现 + 消费」都是 0：别让副屏第三块变成第 6 类假绿

- **提出者**：bidiu（2026-11-22 14:1x 后端配合核查）
- **影响谁**：凝析（副屏「它做过的」渲染）／云昔（契约口径）／以后实现 action 的人
- **状态**：done（2026-11-24 复核）
- **优先级**：normal（今天**不做**是已裁决的；本卡只固定「不做时怎么标」的判据）
- **日期**：2026-11-22

---

## 我遇到的现象

`contracts/action.schema.json` 存在且是 v1，但它自己已经写明「无实现」（`$comment`，CHANGELOG 02:0x、03:00 两条也记了）。我把四条判据都实跑了一遍：

```
ls services/api/modules | wc -l                       → 37
ls services/api/modules | grep -iE "action|timeline|audit"  → （空）
grep -rn "undo_ref|reversible|action.schema|ac_" apps/web/src → （空，零消费）
grep -rln "action.schema" --include=*.py --include=*.md .     → 只有 contracts/{CHANGELOG,README}.md
                                                               与 services/api/tests/test_contracts.py（契约校验，不是实现）
```

`contracts/sight.schema.json` 里也只在 `suggested_action` 的描述里提到「执行必须走 action/proposal」——**`/sight/now` 的响应里没有 action 字段**，它不属于 sight 的源。

而 team 23 文档把「它做过的」列为 AI 副屏第三块。主人原话里「它做过的」是他关心的三块之一。

## 为什么这会挡住我

契约冻结 ≠ 有人做了。第 6 类假绿的定义就是「文档/契约/类型在，实现和消费都不在，但界面看起来像有」。

具体两条会踩：

1. **空列表 + 「暂无记录」文案** = 最坏的一种。它暗示「有数据源，只是现在没数据」，而真相是「没有数据源」。人不会再去怀疑。
2. **拿 `audit_log` 兜底冒充时间线**（schema 注释里建议过这条路）——**实测不可用**：
   ```
   audit_log 列：created_at, updated_at, id, actor, action, target, payload_hash, at, ip
   近 24h 全部记录：只有 mcp denied 与 kernel phase.change，**没有一条 todo 写记录**
   ```
   与 action schema 的 `actor{kind,id,name}` / `verb` 枚举（create/update/delete/invoke/read/approve/reject/undo/dispatch/report）不对齐，
   而且覆盖面极窄——拿它当时间线，人看到的是「AI 什么都没做过」，这是**比空更坏的错**。

## 我认为应该怎么改

**今天（v2 之前）：**

1. 副屏第三块**要么不渲染**，要么渲染占位并带机器可判据的属性：
   ```html
   <section data-testid="ai-actions" data-implemented="false">
     「它做过的」· v2 —— 契约已冻结，实现未开始
   </section>
   ```
   文案不许出现「暂无记录 / 没有动作」（暗示有源），只能写「未实现」。
2. **禁止** 用 `audit_log` 或 `proposals` 列表顶上这个位置——`proposals` 有实现，但它回答的是「等你签字的事」，不是「它做过的事」，语义不同。
3. **不加新枚举、不改 sight 契约**：`attention.kind` 的 enum 没有 `todo_undated` 之类的值，新增 = 契约变更走 RFC，今天不碰。

**未来实现时（留给做的人）：**

- 新增模块 `services/api/modules/action/`，写入点唯一：proposal 签字执行处（`proposal_id` 非空）+ 低风险直执（`proposal_id` 空）。
- `status=undone` 必须有 `undo_ref`，找不到就不许标 undone（契约铁律）。
- 实现落地时**同时删掉** `action.schema.json` 里的 `$comment` 那一行——否则下一个人会以为它还没实现。

## 建议的验证方式

```bash
# 1. 今天：消费必须仍为 0
grep -rn "undo_ref\|reversible" apps/web/src        # → 0 行
# 2. 今天：/sight/now 不含 action
curl -s .../api/v1/sight/now | python -c "import sys,json;print('action' in json.load(sys.stdin))"   # → False
# 3. 今天：副屏占位带机器判据
#    浏览器里 document.querySelector('[data-testid="ai-actions"]').dataset.implemented === "false"
# 4. 未来实现后：一次写操作 → GET /api/v1/action 出现 ac_ 前缀条目，且 undo 成功、undo_ref 可解析
```

---

## 关闭记录（凝析 2026-11-24）

**这条不是「已实现」，是「按裁决不做，且已标清」。** 复核当前 main：

```
ls services/api/modules | grep -iE "action|timeline|audit"  → 空
grep -rn "action.schema" apps/web/src                        → 空
```

副屏第三块「它做过的」现在渲染的是 **proposals 的 undone/rejected 行**（`GET /api/v1/proposals?status=undone|rejected`），
不是 action 契约的数据 —— 也就是说它没有冒充 action 实现，只是名字容易让人以为有。
关掉本卡的理由：契约冻结、实现为零这件事已在 `contracts/CHANGELOG` 与卡内写明；
真要实现，应另开卡（并先把 `action.schema.json` 的 `undo_ref/reversible` 与 proposals 的 `before/undo_result` 对齐）。
**不关「要不要做」，只关「不做时怎么标」——已标清。**
