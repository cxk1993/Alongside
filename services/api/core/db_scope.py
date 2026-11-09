"""数据访问的作用域与运行时边界（SQLite authorizer）。

## 这道边界是什么、不是什么

★ **是**：一道**纪律边界**——约束「走 ORM 的那条路」。
  插件在自己的作用域里，只能碰**自己前缀的表**（`{plugin_id}_*`）。
  读别人表、写别人表、删别人表、DROP 别人表，一律在**语句编译期**被拒。

★ **不是**：沙箱。
  插件与内核同进程同权，它只要 `import sqlite3; sqlite3.connect(path)`
  就能绕开这道边界。
  ⇒ 它防的是 **bug、无意越界、AI 顺手写错表名**，
    **不防恶意代码**。
  ⇒ 对外口径必须写死这一点——把「有边界」说成「有沙箱」是欺骗。

★ 这与插件协议原则七同源：「已安装插件在宿主进程内运行、不受工作区沙箱限制」。

## 为什么用 SQLite authorizer 而不是「每插件独立库」

| | 单库 + authorizer | 每插件独立 db |
|---|---|---|
| 跨插件聚合（周报、聚合卡） | 可走 SQL | 只能全走 API |
| 迁移台账 | 共用 `app_setting` | 要按库重建 |
| 备份 | 一条 `sqlite3 .backup` | 遍历 N 个库 |
| 隔离强度 | 弱（纪律边界） | 强 |

⇒ 选前者：隔离强度上我们**不承诺**更强的那一档，
  却要承担后者全部的复杂度。
  真正需要进程级隔离时（「引不可信插件前必须有进程级隔离」），另做。

## ★ 为什么作用域**不靠 contextvar**（实测结论）

原设计是「contextvar + 依赖注入」。**实测否决**（2026-10-28）：

```
async 端点 → scope_in_ep = None
sync  端点 → scope_in_ep = None
```

原因：anyio 在「依赖」与「端点」之间切换了 context，
contextvar 的值传不过去。而且同步端点的依赖 teardown 里
`reset(token)` 会抛 `ValueError: Token was created in a different Context`
——任何io 线程池都会踩到。

⇒ 改用「**作用域随 Session 对象走**」：
  `session.info["db_scope"]` 记作用域，
  SQLAlchemy 的 `after_begin` 事件在**每条语句执行前**把它挂到连接上。
  ★ 这个机制与线程无关，天然适配 anyio 的线程池。

## 挂点：after_begin（不是 connect）

★ SQLite 官方：`Only a single authorizer can be in place on a database
  connection at a time`，且 authorizer 是**连接级**的。
  我们的连接池被所有插件共用 ⇒ 在 `connect` 里挂 = 给这条物理连接
  **永久钉死**一个插件的规则，换个插件用就误拒。

⇒ 正确挂点：
  · `after_begin`（每次事务开始、语句执行之前）：按当前 Session 的作用域挂；
  · `checkin`（连接归还池时）：**卸下**（`set_authorizer(None)`），
    防下一个使用者继承上一个的规则。
  ★ 实测：commit 之后连接被归还、下条语句重新 begin ⇒ `after_begin` 会再挂一次，
    **约束不会因为 commit 而失效**。

## ★ SQLite authorizer 的两个实测约束

① **只在 `prepare()` 时触发**，且 **`SQLITE_INSERT` / `SQLITE_UPDATE` /
   `SQLITE_DELETE` / `SQLITE_DROP_TABLE` 等 action 的 arg1 是表名**
   （`SQLITE_READ` 是表名+列名）。
   ⇒ 读写都能按表名拦。★ 只拦 `READ`/`SELECT` 是**不够**的：
     实测那样「读别人表被拦、写别人表放行」——边界形同虚设。

② **`PRAGMA` 必须放行**：`PRAGMA table_info(...)` 被 SQLAlchemy
   与迁移代码使用，拒掉会让 ORM 自身挂掉。
   `sqlite_master` / `sqlite_sequence` 同样放行（SQLite 内部用）。
"""
from __future__ import annotations

import contextlib
import logging
import sqlite3
from collections.abc import Iterator
from typing import Any

log = logging.getLogger("kernel.db_scope")

# ─────────────────────── SQLite action codes ───────────────────────

SQLITE_CREATE_INDEX = 1
SQLITE_CREATE_TABLE = 2
SQLITE_CREATE_TEMP_INDEX = 3
SQLITE_CREATE_TEMP_TABLE = 4
SQLITE_CREATE_TEMP_TRIGGER = 5
SQLITE_CREATE_TEMP_VIEW = 6
SQLITE_CREATE_TRIGGER = 7
SQLITE_CREATE_VIEW = 8
SQLITE_DELETE = 9
SQLITE_DROP_INDEX = 10
SQLITE_DROP_TABLE = 11
SQLITE_DROP_TEMP_INDEX = 12
SQLITE_DROP_TEMP_TABLE = 13
SQLITE_DROP_TEMP_TRIGGER = 14
SQLITE_DROP_TEMP_VIEW = 15
SQLITE_DROP_TRIGGER = 16
SQLITE_DROP_VIEW = 17
SQLITE_INSERT = 18
SQLITE_PRAGMA = 19
SQLITE_READ = 20
SQLITE_SELECT = 21
SQLITE_TRANSACTION = 22
SQLITE_UPDATE = 23
SQLITE_ATTACH = 24
SQLITE_DETACH = 25
SQLITE_ALTER_TABLE = 26
SQLITE_REINDEX = 27
SQLITE_ANALYZE = 28

#: 这些 action 的 arg1 是**表名**（SQLite 官方 c_alter_table.html）。
#: ★ 只拦 READ/SELECT 是不够的——那样写操作会漏过去。
TABLE_SCOPED_ACTIONS = frozenset(
    {
        SQLITE_CREATE_INDEX,
        SQLITE_CREATE_TABLE,
        SQLITE_CREATE_TEMP_INDEX,
        SQLITE_CREATE_TEMP_TABLE,
        SQLITE_CREATE_TEMP_TRIGGER,
        SQLITE_CREATE_TEMP_VIEW,
        SQLITE_CREATE_TRIGGER,
        SQLITE_CREATE_VIEW,
        SQLITE_DELETE,
        SQLITE_DROP_INDEX,
        SQLITE_DROP_TABLE,
        SQLITE_DROP_TEMP_INDEX,
        SQLITE_DROP_TEMP_TABLE,
        SQLITE_DROP_TEMP_TRIGGER,
        SQLITE_DROP_TEMP_VIEW,
        SQLITE_DROP_TRIGGER,
        SQLITE_DROP_VIEW,
        SQLITE_INSERT,
        SQLITE_UPDATE,
        SQLITE_ALTER_TABLE,
        SQLITE_REINDEX,
        SQLITE_ANALYZE,
    }
)

#: 这些 action 的 arg1 是**表名**（READ）或 None（SELECT）。
READ_SCOPED_ACTIONS = frozenset({SQLITE_READ, SQLITE_SELECT})


# ─────────────────────── 作用域 ───────────────────────

#: Session 上记录作用域的键。★ 作用域随 Session 走，不靠 contextvar。
SCOPE_KEY = "db_scope"

#: ★ 存量豁免：插件 id → 已存在的不合规表名（**运行时白名单**）。
#:
#:   判据是「表名以 `<插件id>_` 开头」，但 `notes` 插件的两张存量表
#:   不符合（`note_lib` / `note_index`）——**它们不改名**：
#:   改名会丢用户数据（2026-10-26 记录，见 `scripts/check_table_prefix.py`）。
#:
#:   ⇒ 运行时的 authorizer 白名单必须认这张表，否则 `notes` 插件
#:   一访问自己的表就被拒（症状：「笔记功能全挂」，
#:   报错是 `access to note_lib.created_at is prohibited`）。
#:
#:   ★ 为什么这份登记放在内核而不是门禁里：
#:     门禁在 `scripts/`（不进发行包），运行时不能用它。
#:     ⇒ 唯一真相源放内核，门禁**反过来 import 它**——
#:     两处各写一份必然分叉（一边加了豁免、另一边没加）。
LEGACY_TABLE_EXEMPT: dict[str, dict[str, str]] = {
    # purity-ok: 存量表的插件 id 登记，不是业务逻辑——它只回答
    # 「这两张已上线的表归谁管」，改不了任何运行时行为。
    # 内核之所以要认它：这张表不带插件前缀（改名会丢用户数据），
    # 而运行时的表访问边界按「表名前缀 = 插件 id」判定归属。
    # purity-ok: 见上——存量表豁免登记，非业务逻辑
    "notes": {
        "note_lib": "存量表，已上线并持有用户数据；改名会丢数据（2026-10-26 记录）",
        "note_index": "存量表，已上线并持有用户数据；改名会丢数据（2026-10-26 记录）",
    },
}


def table_prefix(plugin_id: str) -> str:
    """插件的表名前缀。

    ★ 与既有表命名约定（`{plugin_id}_xxx`）完全一致——
      不需要另立一张「插件 → 表清单」映射表，也不会与存量表冲突。

    ★★ 但有一处**必须规范化**：插件 id 允许连字符
      （`plugin.schema.json` 的 id pattern 是 `^[a-z][a-z0-9-]*$`），
      而**表名不能直接用带连字符的 id**——
      `CREATE TABLE demo3p-abc_thing` 是 SQL 语法错误（`near "-": syntax error`），
      于是每处引用都得给表名加双引号。
      ⇒ 约定：表名前缀 = **插件 id 的连字符换成下划线** + `_`。
      `demo3p-abc123` → `demo3p_abc123_`。

      ★ 这个规范化必须与 G4 门禁（`scripts/check_table_prefix.py`）
        **用同一个函数** —— 两处各写一份必然分叉：
        门禁放行但运行时拒绝（或反过来）。
    """
    return f"{plugin_id.replace('-', '_')}_"


def allowed_tables(plugin_id: str) -> frozenset[str]:
    """该插件被允许访问的表：前缀通配的表 + 登记的存量豁免表。

    ★ 豁免表是**精确匹配**（不是前缀）：`note_lib` 放行，
      但 `note_library` 不放行——豁免不该被顺手扩大。
    """
    exempt = LEGACY_TABLE_EXEMPT.get(plugin_id, {})
    return frozenset(exempt)


# ─────────────────────── authorizer ───────────────────────


def make_authorizer(plugin_id: str) -> Any:
    """造一个只允许访问 `plugin_id` 自有表的 authorizer。

    ★ 白名单是**前缀匹配**：`{plugin_id}_*`。

    ★ `sqlite_*` 与 `PRAGMA` 放行：
      · `sqlite_master` / `sqlite_sequence` 是 SQLite 内部表；
      · `PRAGMA table_info(...)` 被 ORM 与迁移代码使用，拒掉会让它们挂掉。

    ★ 存量豁免表（`note_lib` 等）**精确匹配**放行。
    """
    prefix = table_prefix(plugin_id)
    exempt = allowed_tables(plugin_id)

    def _auth(
        action: int,
        arg1: str | None,
        arg2: str | None,
        dbname: str | None,
        trigger: str | None,
    ) -> int:
        # ATTACH / DETACH：一律拒绝。挂别的库进来就绕过了前缀白名单。
        if action in (SQLITE_ATTACH, SQLITE_DETACH):
            return sqlite3.SQLITE_DENY
        if action == SQLITE_PRAGMA:
            return sqlite3.SQLITE_OK
        if action in READ_SCOPED_ACTIONS or action in TABLE_SCOPED_ACTIONS:
            if arg1 is None:
                return sqlite3.SQLITE_OK
            table = str(arg1)
            if table.startswith("sqlite_"):
                return sqlite3.SQLITE_OK
            if table.startswith(prefix) or table in exempt:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    return _auth


def scope_session(session: Any, plugin_id: str | None) -> None:
    """给 Session 记上作用域（`plugin_id` 为 None = 内核，无边界）。"""
    session.info[SCOPE_KEY] = plugin_id


def session_scope(session: Any) -> str | None:
    """读 Session 的作用域。"""
    return session.info.get(SCOPE_KEY)


def apply_scope_on_begin(session: Any, transaction: Any, connection: Any) -> None:
    """SQLAlchemy `after_begin` 事件处理器：把作用域挂到这条连接上。

    ★ 事件签名是 `(session, transaction, connection)`（SQLAlchemy 2.x）。
      它在**每条语句执行前**触发 ⇒ 挂上 authorizer 时机正好。

    ★ 没有作用域 ⇒ 卸下 authorizer：
      内核自身（迁移、启动对账、后台任务）不受插件边界约束。
      这正是需要的——插件的迁移要写内核表 `app_setting`（迁移台账），
      而它不带插件前缀。
    """
    plugin_id = session.info.get(SCOPE_KEY)
    try:
        dbapi = connection.connection.dbapi_connection
    except Exception:  # noqa: BLE001 —— 取不到连接就跳过，不阻断业务
        return
    if plugin_id is None:
        with contextlib.suppress(Exception):
            dbapi.set_authorizer(None)
    else:
        dbapi.set_authorizer(make_authorizer(str(plugin_id)))


def clear_scope_on_connection(dbapi_connection: Any) -> None:
    """卸下 authorizer（连接归还池时调用，防下一个使用者继承规则）。"""
    with contextlib.suppress(Exception):
        dbapi_connection.set_authorizer(None)


def clear_scope_on_checkin(dbapi_connection: Any) -> None:
    """连接归还池时卸下 authorizer。

    ★ 为什么必须卸：连接池里的物理连接是**共享**的。
      上一位使用者（某个插件）挂的 authorizer 若留着，
      下一位（另一个插件）会先被旧规则误拒——
      症状是「换个插件就报 access to ... prohibited」，
      而报错指向的表根本不是那位插件的表。
      ⇒ 归还即清空，是「不串味」的最后一道保险。
    """
    clear_scope_on_connection(dbapi_connection)


@contextlib.contextmanager
def scoped_session(session: Any, plugin_id: str | None) -> Iterator[Any]:
    """在 `plugin_id` 作用域内使用一个已有的 Session（供非请求路径用）。

    ★ **后台任务 / 生命周期钩子该用它**，而不是裸 `db_session()`：
      那些路径没有请求上下文 ⇒ 没有作用域 ⇒ 不受边界约束。
      而它们同样属于某个插件，同样只该碰自己的表。

    用法：

    ```python
    with db_session() as db:
        with scoped_session(db, "finance"):
            db.execute(...)          # 只能碰 finance_* 表
    ```

    ★ 为什么不用 contextvar：实测它跨不过 anyio 的 context 切换
      （依赖里设的，端点里读不到），且线程池里 reset 会抛 ValueError。
      ⇒ 作用域必须**显式**跟着 Session 走。
    """
    previous = session.info.get(SCOPE_KEY)
    session.info[SCOPE_KEY] = plugin_id
    try:
        yield session
    finally:
        session.info[SCOPE_KEY] = previous
