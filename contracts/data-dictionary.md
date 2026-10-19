# Alongside 数据字典

> **契约文件**：字段与 `services/api/db/models/` 逐字段对应，由
> `python -m db.check_dictionary` 自动比对。**改模型必须同步改本文件**，反之亦然。
> 全表通用：`id` 为 UUID hex 字符串（或插件语义 id）；时间列一律 UTC
> （`UTCDateTime`：写入必须带时区、读出自动补 UTC）；`created_at` / `updated_at` 全表标配。

## 内核表（T04 维护）

| 表 | 字段 | 类型 | 说明 |
|:--|:--|:--|:--|
| audit_log | id | str pk | UUID hex |
| audit_log | created_at | datetime | UTC，索引 |
| audit_log | updated_at | datetime | UTC，update 自动刷新 |
| audit_log | actor | str(64) | 操作者（system / user / agent:<id>），索引 |
| audit_log | action | str(64) | 动作名，如 auth.login |
| audit_log | target | str(200) | 被操作对象 |
| audit_log | payload_hash | str(64) | 载荷哈希（不存原文，防敏感信息落库） |
| audit_log | at | datetime | 事件时刻，UTC，索引 |
| audit_log | ip | str(64) | 来源 IP |
| idempotency_key | id | str pk | UUID hex |
| idempotency_key | created_at | datetime | UTC，索引 |
| idempotency_key | updated_at | datetime | UTC |
| idempotency_key | key | str(128) | Idempotency-Key 头，唯一索引 |
| idempotency_key | method | str(8) | HTTP 方法 |
| idempotency_key | path | str(300) | 请求路径 |
| idempotency_key | request_hash | str(64) | 请求体指纹（同 key 不同 body = 冲突） |
| idempotency_key | response_status | int | 首次响应码（0 = 未完成） |
| idempotency_key | response_body | text | 首次响应体（重放用） |
| idempotency_key | expires_at | datetime | 过期时间（TTL 由 Settings 决定） |
| app_setting | id | str pk | UUID hex |
| app_setting | created_at | datetime | UTC，索引 |
| app_setting | updated_at | datetime | UTC |
| app_setting | key | str(128) | 设置键，唯一索引；兼作插件迁移记录（key = migration.<插件id>.<版本>） |
| app_setting | value_json | text | JSON 字符串 |
| plugin_state | id | str pk | = 插件 id（注册表与运行时分离，热插拔前提） |
| plugin_state | created_at | datetime | UTC，索引 |
| plugin_state | updated_at | datetime | UTC |
| plugin_state | version | str(32) | 已安装版本 |
| plugin_state | kind | str(16) | core / builtin / third-party |
| plugin_state | enabled | bool | 启用状态（禁用 = 系统必须优雅降级） |
| plugin_state | installed_at | datetime | 安装时刻 UTC |
| plugin_state | last_error | text | 最近一次错误（可空） |
| plugin_state | granted_permissions | text | 已授权权限 JSON 数组（对应 manifest.permissions） |
| plugin_setting | id | str pk | UUID hex |
| plugin_setting | created_at | datetime | UTC，索引 |
| plugin_setting | updated_at | datetime | UTC |
| plugin_setting | plugin_id | str(64) | 插件 id，索引 |
| plugin_setting | key | str(128) | 设置键 |
| plugin_setting | value_json | text | JSON 字符串（按该插件 settingsSchema 校验后存） |

> plugin_setting 约束：UNIQUE(plugin_id, key)。

## 业务表（各插件自建；此处仅登记表名与必备索引——T04 不建、不改）

| 表 | 归属插件 | 必备索引（理由） |
|:--|:--|:--|
| calendar_event | calendar | (start_at) 按周主查询；(parent_id) 事件树取子块 |
| todo_item | todo | (done, due_at) "今天该做什么"主查询 |
| note_lib | notes | 本机笔记夹登记 |
| note_index | notes | (lib_id, rel_path) 唯一定位一篇笔记 |
| habit / habit_log | habit | habit_log(habit_id, date) 打卡查询 |
| review_daily | review | (date) 唯一，日报按天取 |
| finance_snapshot | finance | (date) 唯一，每日快照 |
| finance_entry | finance | (occurred_at) 区间流水主查询；(category)(account)(direction) 过滤 |
| growth_axis / growth_item | growth | growth_item(axis_id) 罗盘装配 |
| agents_task / agents_agent / agents_dispatch | agents | agents_task(status, assignee)；agents_dispatch(task_id, status)；dispatch_id 唯一幂等键 |
| health_record | health | (occurred_at) 时间线主查询；(kind) 过滤；(followup_needed) 待跟进 |
| calendar_reminder_log | calendar | (event_id) 提醒去重；(fired_at) 投递审计 |
| catalog_entry | catalog | (catalog_id) 手动条目稳定对外 id；(name) 名称检索 |

**索引之外的建表规矩**（见 `db/base.py` 与 `docs/示例/calendar_event_示例.py`）：
表名 = 插件 id 前缀 + 名词；必须继承 `PkMixin + TimestampMixin`；外键写明
`ON DELETE CASCADE` 或 `SET NULL` 的选择理由；**禁止预留 user_id**（单人系统）。
