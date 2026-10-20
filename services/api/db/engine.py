"""SQLite 引擎（T04 · 步骤 1）。

SQLite 正确配置（总纲雷区 #6）：
    PRAGMA journal_mode=WAL      # 读写并发，备份期间不阻塞
    PRAGMA busy_timeout=5000     # 写锁冲突时最多等 5 秒，不立刻抛 database is locked
    PRAGMA foreign_keys=ON       # SQLite 默认关闭外键，必须显式开
    PRAGMA synchronous=NORMAL    # WAL 模式推荐档位

★ 写并发策略（单用户系统的正确姿势）：
  1. SQLite 是单写者：同一时刻只允许一个写事务，WAL 让读不被写阻塞。
  2. busy_timeout=5000 把"撞锁"变成"排队"，10 并发以内实测够用（见 tests）。
  3. 业务层一律**短事务**：repo.py 的每个操作即开即收，禁止在请求中途
     长时间持有写事务（那是 database is locked 的真正来源）。
  4. 备份走 sqlite3 .backup API（在线备份），不锁主库。

注入方式（T03 约定）：core.deps.set_engine(工厂) —— core 不 import db，
由本模块在 init_engine() 里反向注册，入口只调 init_engine() 一行。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine, make_url
from sqlmodel import Session, create_engine

from core.deps import set_engine

logger = logging.getLogger(__name__)

_engine: Engine | None = None


def _apply_sqlite_pragmas(target: Engine) -> None:
    """每个新连接都执行四条 PRAGMA（连接级设置，必须 per-connection）。"""

    @event.listens_for(target, "connect")
    def _set_pragma(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


#: 改名前的库文件名（旧品牌期）。仅用于**兼容回退**，不参与新建。
LEGACY_DB_NAME = "lifos.db"


def _resolve_db_path(raw: str) -> Path:
    """相对路径相对 services/api 解析（uvicorn / pytest 都在这里跑）。

    ★ 库文件改名兼容（旧名 lifos.db → 新名 alongside.db）：
      默认路径换成新名后，已有实例的库还叫旧名 —— 若**新名文件不存在**而
      同目录旧名文件存在，则直接沿用旧库，保证改名不会让任何既有数据「凭空消失」。
      什么时候失效：一旦新名库被建出来（迁移/重装），回退自然不再触发。
      显式把 DB_PATH 指到旧名也照常工作（p.name == LEGACY_DB_NAME 时不做任何改写）。
    """
    p = Path(raw)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent / p  # services/api/<raw>
    legacy = p.with_name(LEGACY_DB_NAME)
    if p.name != LEGACY_DB_NAME and not p.exists() and legacy.exists():
        logger.warning(
            "库文件 %s 尚不存在，沿用旧名库 %s（改名兼容回退；迁移后此日志自动消失）",
            p.name,
            legacy,
        )
        p = legacy
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def make_engine(db_url: str | None = None) -> Engine:
    """创建一个配置齐全的 SQLite 引擎。

    db_url 缺省时取 core.config.Settings.db_path（.env 可覆盖）。
    测试可传 "sqlite:///<tmp>/x.db"，互不污染。
    """
    if db_url is None:
        from core.config import get_settings  # 延迟导入：避免无谓的必填校验

        path = _resolve_db_path(get_settings().db_path)
        db_url = f"sqlite:///{path.as_posix()}"
    make_url(db_url)  # 早点发现格式错误，别等到第一次连接
    engine = create_engine(
        db_url,
        echo=False,
        # SQLite + FastAPI 线程池：同一连接可能被不同线程先后使用
        connect_args={"check_same_thread": False},
        # ★ 根因兜底（2026-09-25）：池参数防"连接泄漏后耗尽"——
        #   pre_ping：取连接前探活，坏连接自动重建（不把 500 抛给用户）；
        #   recycle：连接最长存活 1h，超龄回收（防长命连接腐化）；
        #   size/overflow：显式给足余量（默认 5+10，139 处 Depends 并发下偏紧）。
        pool_pre_ping=True,
        pool_recycle=3600,
        pool_size=10,
        max_overflow=20,
    )
    _apply_sqlite_pragmas(engine)
    return engine


def session_factory() -> Session:
    """给 core.deps.set_engine 用的会话工厂（callable -> Session）。"""
    if _engine is None:
        raise RuntimeError("db.engine.init_engine() 尚未调用")
    return Session(_engine)


def get_engine() -> Engine:
    """已初始化的引擎；未初始化时报人话错误。"""
    if _engine is None:
        raise RuntimeError("数据库引擎未初始化：请先调用 db.engine.init_engine()")
    return _engine


def init_engine(db_url: str | None = None) -> Engine:
    """初始化引擎并注册到 core.deps。

    - `db_url=None`：幂等，已有引擎直接返回（生产入口姿势）。
    - **显式 `db_url`**：若与当前引擎不同则 **重建**（测试隔离需要：
      pytest hook 已按模块绑过库，个别测试再传独立 URL 时必须能切换）。
    """
    global _engine
    if _engine is not None:
        if db_url is None:
            return _engine
        try:
            current = _engine.url.render_as_string(hide_password=False)
        except Exception:  # noqa: BLE001
            current = str(_engine.url)
        target = db_url
        if current == target or current.replace("sqlite:///", "sqlite:///") == target:
            return _engine
        reset_engine()
    _engine = make_engine(db_url)
    set_engine(session_factory)
    logger.info("数据库引擎已就绪：%s", _engine.url)
    return _engine


def reset_engine() -> None:
    """丢弃当前全局引擎，使下次 init_engine() 按当时的 DB_PATH 重建。

    仅供**测试隔离**使用（conftest / pytest hook）：全量 pytest 时多个测试
    模块各自声明 DB_PATH，但 init_engine 幂等只认第一次——不 reset 就会
    全进程共用一个库，出现 slug 冲突类连挂。生产入口不要调用本函数。
    """
    global _engine
    if _engine is not None:
        try:
            _engine.dispose()
        except Exception:  # noqa: BLE001 — dispose 失败不阻断切换
            logger.warning("reset_engine: dispose 当前引擎失败", exc_info=True)
        _engine = None
    logger.info("数据库引擎已重置，等待 init_engine()")
