"""插件自带迁移的执行器（总纲 §1.3 / 验收 #7）。

每个插件在自己的 api/migrations/ 下放 0001_init.py 等，文件必须导出
    def upgrade(engine) -> None
    def downgrade(engine) -> None
（用 SQLModel 的 metadata.create_all / drop_all 或原生 DDL 均可）。

★ 两处来源一视同仁：builtin 在 modules/<id>/api/migrations/，
  third-party 在 plugins/<id>/api/migrations/。
★ 已执行的迁移记录在 app_setting（key = migration.<插件id>.<版本>），
  重启/重跑不重复执行（只增不改）。

★ ISSUE-006（2026-09-20）：实盘 12 个 builtin 插件的迁移全放在
  modules/<id>/migrations/（无 api/ 前缀），内核原只扫 api/migrations/
  ⇒ 一条都不会跑。现改为**双目录探测**（契约位 api/migrations/ 优先、
  历史位 migrations/ 兜底），并提供**启动对账** reconcile_migrations()
  + lifespan 工厂 make_startup_lifespan()：服务真正开始服务前按台账
  补跑缺失迁移。第三方插件维持 install() 触发不变。
"""
from __future__ import annotations

import importlib.util
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlmodel import text

from core.errors import MigrationError
from core.logging import get_logger

log = get_logger("kernel.plugins.migrations")


def _declared_migration_dir(info: Any) -> str | None:
    """读 manifest 里声明的迁移目录（相对插件根）。

    ★ 为什么必须读 manifest（而不是猜路径）
    --------------------------------------
    协议 §4.1：「**manifest 是唯一契约**。所有需要被内核知道的信息都在
    `manifest.json` 里，内核不需要去读插件的代码来判断。」

    ⇒ 内核**按约定路径**去找（早先的 `api/migrations` 优先、`migrations` 兜底）
    意味着 manifest 的 `migrations` 字段**说了不算数**：
    作者写 `"db/migrations"`，内核照样去 `api/migrations` 找。
    ⇒ 那是「manifest 说的」与「约定说的」两个真相源，而前者是死的。

    ★ 但**未声明时**（null）仍走约定位兜底——
      「没声明」的语义是「没指定」，不是「禁止」；
      而 16 个内置插件的 manifest 恰好写的是 `"migrations"`（声明值 == 兜底位），
      所以对它们而言按声明解析与按约定解析结果一致。
    """
    manifest = getattr(info, "manifest", None)
    if not isinstance(manifest, dict):
        return None
    rel = manifest.get("migrations")
    if not rel or not isinstance(rel, str):
        return None
    return rel


def _migration_dir(info: Any) -> Path:
    """迁移目录解析：**先信 manifest 的声明**，再退回约定探测。

    ★ 解析顺序（与协议「manifest 是唯一契约」一致）：
      1. manifest 声明了 `migrations` ⇒ **按声明解析**（相对插件根）。
         声明的目录不存在 ⇒ **报错**，不许静默退回约定位——
         静默退回 = 「manifest 说的不算数」又活过来了。
      2. 未声明（null）⇒ 退回约定探测：
         `api/migrations/` 优先、`migrations/` 兜底（兼容既有第三方插件用法）。

    ★ 为什么「声明了却不存在」要报错而不是退回
    --------------------------------------------
    因为作者的意图已经写在 manifest 里了。若内核悄悄去别处跑了一堆别的迁移，
    他会以为自己的声明生效了——而实际上跑的不是他声明的那些。
    ⇒ 报错是唯一诚实的做法（也是协议 §11 写的失败行为：
      「启动失败（路径不存在）」）。

    ★ 与早先的「双位置探测」的关系
    ------------------------------
    早先只有约定位探测，因为那时**没人读 manifest 的这个字段**。
    现在是「声明优先 + 约定位兜底」——约定位那条路**仍然保留**，
    所以依赖旧布局的第三方插件不受影响。
    """
    declared = _declared_migration_dir(info)
    if declared is not None:
        path = Path(info.directory) / declared
        if not path.is_dir():
            raise MigrationError(
                f"插件「{getattr(info, 'id', '?')}」的 manifest.migrations 声明了 "
                f"{declared!r}，但该目录不存在（相对插件根解析）"
                "——声明了却不落地，等于这个声明从未生效。"
            )
        return path

    api_dir = Path(info.directory) / "api" / "migrations"
    if api_dir.is_dir():
        return api_dir
    legacy_dir = Path(info.directory) / "migrations"
    if legacy_dir.is_dir():
        return legacy_dir
    return api_dir


def _discover(info: Any) -> list[tuple[str, Path]]:
    """[(version, path)]，按文件名序。version = 文件名（去 .py）。"""
    d = _migration_dir(info)
    if not d.is_dir():
        return []
    out: list[tuple[str, Path]] = []
    for p in sorted(d.glob("*.py")):
        if p.name.startswith("__"):
            continue
        out.append((p.stem, p))
    return out


def _load(path: Path, info: Any) -> Any:
    name = f"plugin_migration_{info.id}_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载迁移文件：{path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    for fn in ("upgrade", "downgrade"):
        if not callable(getattr(mod, fn, None)):
            raise RuntimeError(f"迁移文件 {path} 缺少 {fn}() 函数")
    return mod


def _record_key(plugin_id: str, version: str) -> str:
    return f"migration.{plugin_id}.{version}"


def _recorded(engine: Any, plugin_id: str) -> set[str]:
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='app_setting'")
        ).fetchone()
        if not exists:
            return set()
        rows = conn.execute(
            text("SELECT key FROM app_setting WHERE key LIKE :pat"),
            {"pat": f"migration.{plugin_id}.%"},
        ).fetchall()
    return {r[0] for r in rows}


def _record(engine: Any, plugin_id: str, version: str) -> None:
    from db.base import utcnow

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app_setting (id, key, value_json, created_at, updated_at) "
                "VALUES (:id, :key, :v, :now, :now)"
            ),
            {
                "id": __import__("uuid").uuid4().hex,
                "key": _record_key(plugin_id, version),
                "v": f'"{utcnow().isoformat()}"',
                "now": utcnow().isoformat(),
            },
        )


def _unrecord(engine: Any, plugin_id: str, version: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM app_setting WHERE key = :key"),
            {"key": _record_key(plugin_id, version)},
        )


def run_migrations(engine: Any, info: Any) -> list[str]:
    """执行插件尚未执行过的迁移，返回本次执行的版本列表。

    ★ 新库首启时台账表可能不存在：先 ensure_record_table（ISSUE-006 同款），
      否则 _record INSERT app_setting 会直接 OperationalError。
    """
    ensure_record_table(engine)
    done = _recorded(engine, info.id)
    executed: list[str] = []
    for version, path in _discover(info):
        key = _record_key(info.id, version)
        if key in done:
            continue
        mod = _load(path, info)
        # ★ 迁移脚本抛什么内核管不了，但对外必须是**一个**应用级错误。
        #   否则调用方（启停流程、测试、用户）要区分「迁移写错了」和「内核坏了」，
        #   而后者根本不是。原始异常类型与信息都带在消息里，不丢诊断线索。
        try:
            mod.upgrade(engine)
        except Exception as exc:  # noqa: BLE001 — 迁移是插件自己的代码
            raise MigrationError(
                f"插件「{info.id}」迁移 {version} 执行失败"
                f"（{type(exc).__name__}: {exc}）"
            ) from exc
        _record(engine, info.id, version)
        executed.append(version)
    return executed


def rollback_migrations(engine: Any, info: Any) -> list[str]:
    """倒序回滚插件已记录的迁移（卸载时用）。"""
    done = _recorded(engine, info.id)
    rolled: list[str] = []
    for version, path in reversed(_discover(info)):
        key = _record_key(info.id, version)
        if key not in done:
            continue
        mod = _load(path, info)
        mod.downgrade(engine)
        _unrecord(engine, info.id, version)
        rolled.append(version)
    return rolled


# ───────────────── ADR-0005 · 可逆启用的差分补偿原语 ─────────────────
# 背景：run_migrations 自带台账（app_setting 的 migration.<id>.<ver>），
#   幂等且只增不改。因此「一次 enable 的迁移 effect」= 它**本次真正执行**的那
#   几个版本，而不是插件全部迁移。失败时若直接调 rollback_migrations（全量
#   倒序 downgrade），会把历史迁移一并 drop —— 删掉承载数据的表，属于比原
#   故障更严重的二次破坏。
#   对齐论文 Definition 8：逆只需在「该 effect 被应用的那个状态」上生效。


def recorded_versions(engine: Any, plugin_id: str) -> set[str]:
    """已执行迁移的**版本号**快照（供可逆启用做前后差分）。

    ⚠️ 返回的是裸版本号（如 ``0001_init``），**不是**台账 key
    （``migration.<id>.0001_init``）。内部 `_recorded()` 给的是 key，
    这里统一剥前缀 —— 否则调用方拿版本号去比 key 集合会**永远失配、
    静默跳过**（2026-10-03 踩过：回滚静默 no-op，表留在原地）。
    """
    prefix = f"migration.{plugin_id}."
    return {k[len(prefix) :] for k in _recorded(engine, plugin_id) if k.startswith(prefix)}


def rollback_versions(engine: Any, info: Any, versions: Sequence[str]) -> list[str]:
    """只回滚**指定的**若干版本（倒序），用于补偿一次 enable 的部分迁移。

    与 rollback_migrations 的区别：后者按台账全量倒序（卸载语义），本函数
    只碰调用方点名的版本（补偿语义）。不在台账里的版本会被跳过（幂等）。

    ⚠️ `versions` 是**裸版本号**（与 recorded_versions 同口径）。这里显式
    用 `_record_key` 构造 key 去比对，避免两种口径混用导致静默 no-op。
    """
    done_keys = _recorded(engine, info.id)
    by_name = dict(_discover(info))
    rolled: list[str] = []
    for version in reversed(list(versions)):
        if _record_key(info.id, version) not in done_keys or version not in by_name:
            continue
        mod = _load(by_name[version], info)
        mod.downgrade(engine)
        _unrecord(engine, info.id, version)
        rolled.append(version)
    return rolled



def has_migrations(info: Any) -> bool:
    return len(_discover(info)) > 0


# ───────────────────────── ISSUE-006 · 启动对账 ─────────────────────────


def ensure_record_table(engine: Any) -> None:
    """确保迁移台账表 app_setting 存在（ISSUE-006）。

    新库首启（删库自启 / 灾备重建 / 换机迁移）时内核表可能尚未由 alembic
    建；没有台账就无法记录已执行版本，对账会每次重复执行。按 T04 的
    AppSetting 模型 checkfirst 补建——只 import 使用，不改动 db/ 任何文件。
    """
    from db.models.system import AppSetting

    # SQLModel 模型的 __table__ 在运行期才挂上，mypy 桩不识别（attr-defined）
    AppSetting.__table__.create(bind=engine, checkfirst=True)  # type: ignore[attr-defined]


def _as_info(entry: tuple[str, Path] | tuple[str, Path, Any]) -> SimpleNamespace:
    """把模块条目规整成带 manifest 的 info。

    ★ 为什么要带 manifest
    -------------------
    迁移目录**按 manifest 声明解析**（见 `_migration_dir`），
    所以 info 上必须能拿到 manifest。
    早先这里只造 `SimpleNamespace(id, directory)` ⇒ 声明读不到，
    于是「按声明解析」在启动对账这条路径上是**假的**——
    而启动对账恰恰是**唯一**会跑内置插件迁移的路径。

    ★ 兼容两元组
    ------------
    接受 `(id, dir)`（无 manifest）⇒ 退化为约定位探测。
    这样第三方调用方与既有测试不必立刻改。

    ★ 为什么用 `SimpleNamespace` 而不是 `PluginInfo`
    -----------------------------------------------
    core 不 import 插件发现层（分层约定），
    而 `SimpleNamespace` 让调用方不必构造完整的信息对象。
    """
    if len(entry) == 3:
        plugin_id, directory, manifest = entry
        return SimpleNamespace(id=plugin_id, directory=directory, manifest=manifest)
    plugin_id, directory = entry
    return SimpleNamespace(id=plugin_id, directory=directory, manifest=None)


def reconcile_migrations(
    engine: Any, modules: Sequence[tuple[str, Path] | tuple[str, Path, Any]]
) -> dict[str, list[str]]:
    """启动对账：按模块条目逐个补跑未执行的迁移（ISSUE-006）。

    run_migrations 本身幂等（台账只增不改），这里补的是「启动时机」——
    builtin 插件以前只有 third-party install 会触发迁移，新库首启全部
    建表即 500。任一插件迁移失败即抛错（fail fast，不许静默带病启动）。

    ★ 条目可以是 `(id, 目录)` 或 `(id, 目录, manifest)`：
      带 manifest 时，迁移目录**按声明解析**（协议：「manifest 是唯一契约」）。

    返回 {插件id: 本次执行的版本列表}（未执行任何迁移的插件值为 []）。
    """
    ensure_record_table(engine)
    result: dict[str, list[str]] = {}
    for entry in modules:
        info = _as_info(entry)
        plugin_id = info.id
        try:
            result[plugin_id] = run_migrations(engine, info)
        except Exception as exc:
            raise RuntimeError(
                f"插件「{plugin_id}」启动迁移对账失败：{exc}"
            ) from exc
    return result


def make_startup_lifespan(
    modules: Sequence[tuple[str, Path] | tuple[str, Path, Any]],
    *,
    on_started: Any | None = None,
    on_stopping: Any | None = None,
) -> Any:
    """为 create_app 生成 lifespan：服务开始服务前对账补跑各插件迁移。

    引擎经 core.deps 注入链获取（Session.get_bind()）——core 不 import db
    的分层约定不破。阻塞发生在 uvicorn lifespan 阶段，此时还未对外服务，
    正是「表必须先于请求存在」的正确时机。

    on_started/on_stopping 为可选零参钩子（如事件监听的挂/摘），
    分别在对账完成后、服务退出时调用——保证对账时点不因激活器改变（硬约束 3）。
    """

    @asynccontextmanager
    async def lifespan(_app: Any) -> AsyncIterator[None]:
        from core.deps import db_session

        with db_session() as session:
            engine = session.get_bind()
        executed = reconcile_migrations(engine, modules)
        ran = {pid: v for pid, v in executed.items() if v}
        if ran:
            total = sum(len(v) for v in ran.values())
            log.info(
                "插件迁移对账完成：本次补跑 %d 个插件共 %d 个版本",
                len(ran),
                total,
                extra={"plugins": {pid: v for pid, v in ran.items()}},
            )
        if on_started is not None:
            on_started()
        try:
            yield
        finally:
            if on_stopping is not None:
                on_stopping()

    return lifespan
