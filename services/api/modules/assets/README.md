# assets · 资产层（可沉淀的经验）

> 契约：`contracts/asset.schema.json`（云昔）｜ 实现：bidiu 2026-11-22 夜班
> 定位：项目「沉淀 → 复用 → 扩散」的物理载体。人只做三件事的第三件——**复用**——就发生在这里。

## 三条硬规则（来自契约，来自 MCP issue #3564 的真实教训 + 汐瑶 ADR）

1. **append-only + 显式 supersede** —— 更新 = 新建一条 + 旧条标 `superseded` 并反向指 `superseded_by`。
   旧条正文**永不改写**（last-writer-wins 会丢掉「之前决定过什么、为什么」）。
2. **provenance 必填** —— `{agent, at, inputs?, session_id?}`。跨 AI 共享记忆时一毒全毒，必须能追到源头。
3. **可单条硬删** —— 用户有权真正删除；被取代链上的反链一并清理（不留悬挂指针）。

## 数据形状（`assets_asset`）

| 字段 | 说明 |
|---|---|
| `id` | `as_` + 12 hex（契约 pattern `^as_[A-Za-z0-9_-]{6,}$`），永不复用 |
| `type` | note / skill / checklist / decision / procedure |
| `title` / `body` / `summary` | body=Markdown 无上限；summary ≤200（索引只读它） |
| `provenance` | JSON：agent + at（服务端补）+ inputs + session_id |
| `supersedes` / `superseded_by` | 取代关系的双向指针（互为镜像） |
| `status` | active / superseded / draft |
| `tier` | public / work / private / sealed —— **默认 private** |
| `scope` | private / shared —— v1 一律 private |
| `exportable` | 默认 **true**（只读约束的不是不能带走） |
| `tags` / `refs` | ≤12 个 tag；refs = [{kind: asset/module/url, id, label?}] |

## 端点（`/api/v1/assets`）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/assets` | 沉淀一条（带 `supersedes` 时同事务完成取代双向连线） |
| GET | `/assets?status=&type=&tier=&q=&limit=` | 列表（q 匹配 title/summary） |
| GET | `/assets/{id}` | 单条 |
| DELETE | `/assets/{id}` | 硬删（清理反链） |

**任务一键沉淀**（验收第④条的实现方式）：
```json
POST /api/v1/assets
{
  "type": "note",
  "title": "这次任务沉淀出的经验",
  "body": "……",
  "provenance": {"agent": "agent:bidiu", "inputs": ["<task_id>"]}
}
```

## MCP 工具面

`assets_asset_write` / `assets_asset_list` / `assets_asset_read` / `assets_asset_delete`
（`api.tools` 全部写完整路径——proposals 曾踩「相对路径被拼成双层」的坑）

## SSE

`assets.asset.created` / `assets.asset.superseded` / `assets.asset.deleted`（commit 后真发）

## 测试

`pytest tests/test_assets.py` —— 9 条：默认值 / provenance 必填 / 非法 type /
supersede 双向 / 不存在的 supersedes 拒 / 双重取代拒 / 过滤 / 硬删反链清理 / 事件三件套

## 已知边界（v1）

- `tier` / `scope` 目前是**数据字段**，服务端不做读过滤（单用户 v1 合理；多租户时要在查询层加 G13/G16 式边界）
- `assets.asset.write` 是写动词 → 缺省开模式下 MCP 通道写资产会被拦成提案（人签字确认 provenance）。若演示需要 AI 自动沉淀，需要给该工具加豁免（待主人裁）