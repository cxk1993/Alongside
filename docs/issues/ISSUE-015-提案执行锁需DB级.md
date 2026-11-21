# ISSUE-015 · 提案执行锁在「多 worker」部署下失效（需 DB 级原子抢位）

- **提出者**：bidiu（B1 提案层实现者）
- **影响谁**：bidiu（后续把服务从单 worker 扩到多 worker 的人）
- **状态**：open
- **优先级**：normal（v1 单 worker 已覆盖；扩 worker 前必须处理）
- **日期**：2026-11-22

---

## 我遇到的现象

`approve` / `undo` 是「读 → 检查 `status=pending` → 执行内部 HTTP → 写 `approved`」的序列。
两个并发请求都读到 `pending` ⇒ **都执行**——对 `delete` 语义就是**双删**（第二个请求 404/幂等还好，但业务副作用已经发生两次）。

已用 **进程内 per-id 锁**（`threading.Lock` + `@_serialized` 装饰器，commit `8706da3`）修掉单进程场景：

```python
lock = _exec_lock(pid)
if not lock.acquire(blocking=False):
    raise ValidationError("该提案正在处理中（并发请求），请稍后刷新")
```

测试：`test_concurrent_approve_only_one_wins`（Barrier 双线程 → 恰一个 200 一个 422）。

**但**：`uvicorn --workers 4` 或多副本部署时，进程内锁**跨进程无效**——两个 worker 各拿到锁，各执行一次。

## 为什么这会挡住我

v1 的 dev/单实例场景没事；但「生产可用」的定义里包含并发安全。这是安全类问题的**延迟炸弹**：
现在绿，扩 worker 那天红，而且红的表现是「数据被删两次」而不是「报错」。

## 建议改法（DB 原子抢位，SQLite 与 PG 通用思路）

```python
from sqlalchemy import update

res = self.db.exec(
    update(ChangeProposal)
    .where(ChangeProposal.id == pid, ChangeProposal.status == "pending")
    .values(status="executing")          # 临时态：抢到锁的请求改它
)
self.db.commit()
if res.rowcount == 0:
    raise ValidationError("提案已被其他请求处理（状态已变），请刷新")
# ... 执行 HTTP ...
# 成功 → status="approved"；失败 → status 回 "pending"（可重试）
```

配套三件：
1. **`executing` 态的启动对账**：进程崩溃留下的 `executing` 在启动时归回 `pending`（`core/app.py` 的 lifespan 里，与插件启动对账同处）。
2. **契约更新**：`proposal.schema.json` 的 status enum 加 `executing`（云昔的领地，需她改；前端要把它显示成「执行中」而不是「待签字」）。
3. **并发测试升级**：两进程/两 worker 同时 approve → 只执行一次（可以用 subprocess 起两个 `pytest`？更实际的做法：直接用 `sqlite3` 两个连接模拟两个 worker 的原子 UPDATE，断言 rowcount 一胜一败）。

## 验证方式

- 单进程：`test_concurrent_approve_only_one_wins`（已过）
- 多 worker：`pytest -k "multi_worker"`（待写）或手动 `uvicorn --workers 2` + 两次并发 curl，断言业务副作用只发生一次

## 相关

- `modules/proposals/service.py` 的 `_EXEC_LOCKS` / `_serialized`（当前实现，注释里已写明这条边界）
- 判据 17「发现问题记 issue，不当场扩修」——本轮只做了单进程锁，多 worker 的 DB 级方案留这张卡