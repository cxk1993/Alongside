"""待办与周期清单路由（薄：只做参数校验 + 调 service + 发事件）。

HTTP 契约（全项目统一，不许自创）：
  成功 → 直接返回资源 JSON（200/201）
  失败 → 由内核统一转成 RFC7807 application/problem+json

★ 前缀由内核按 manifest.api.base 自动加，这里**不要写 prefix=**。
★ 不自己捕获异常包成自定义格式，内核统一处理。
★ 事件总线：写操作在 service 层 publish 到 event_bus，内核 SSE
  （/api/v1/events/subscribe）自动下推，无需在此手动发。
★ 幂等（Idempotency-Key）由内核 IdempotencyMiddleware 自动处理，这里不重复做。
★ 内部一律用包路径导入（from modules.todo.xxx import ...）。
★ 路径参数用 Annotated[str, FPath(...)]（不是 = FPath(...)）：
  后者是「带默认值的参数」，会逼后面的 body 也必须给默认值。
★ 本文件**不要**加 `from __future__ import annotations`：
  它会把 `-> None` 变成字符串 "None" → get_type_hints 得到 NoneType（truthy）
  → FastAPI 认为 204 带了响应体 → 整个后端起不来。
"""
import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi import Path as FPath
from sqlmodel import Session

from core.deps import get_current_user, get_db
from core.errors import NotFoundError, ValidationError
from core.mcp_writes import is_mcp_call
from core.security import User

from . import health_link as _health_link  # noqa: E402,F401  ISSUE-007
from .schema import (
    ExportIn,
    ExportOut,
    ImportIn,
    ImportOut,
    SummaryOut,
    TodoCreate,
    TodoItemOut,
    TodoListOut,
    TodoTagOut,  # 2026-09-28 标签汇总出参
    TodoUpdate,
    ToggleOut,
)
from .service import TodoService, parse_quick_line

router = APIRouter()


def _tags_from_raw(raw: str | None) -> list[str]:
    """从 raw 一行 markdown 里取出 `#标签`（MCP 强制校验的 raw 兜底用）。

    raw 是「语法糖」通路，标签藏在文本里（如「交作业 #学业/高数」）。
    解析失败一律按「没有标签」处理 —— 交给调用方抛那条写明怎么补的 422，
    **绝不静默放行**。
    """
    if not raw or not raw.strip():
        return []
    try:
        return list(parse_quick_line(raw).tags or [])
    except Exception:  # noqa: BLE001
        return []

_MANIFEST_PATH = Path(__file__).resolve().parent / "manifest.json"
with _MANIFEST_PATH.open(encoding="utf-8") as _f:
    _MANIFEST = json.load(_f)

# ★ 依赖别名**只做类型**，不要把 Depends 塞进 Annotated。
DbDep = Session
UserDep = User


@router.get("/health")
def health() -> dict[str, bool]:
    """每个插件都必须有 health —— 内核据此判断"该能力是否可用"。"""
    return {"ok": True}


@router.get("/manifest")
def manifest() -> dict:
    """模块清单（前端 / AI 发现能力用）。"""
    return _MANIFEST


@router.get("/items", response_model=TodoListOut)
def list_items(
    status: str | None = Query(
        None,
        description=(
            "done | todo | all | active | archived（默认 all）。"
            "active=没归档的一切；archived=打勾满 7 天的已完成项"
        ),
    ),
    due_before: str | None = Query(None, description="截止时间上界，带时区 ISO8601"),
    due_after: str | None = Query(None, description="截止时间下界，带时区 ISO8601"),
    tag: str | None = Query(None, description="按标签过滤"),
    source: str | None = Query(None, description="按来源文件路径过滤"),
    limit: int = Query(50, ge=1, le=200, description="返回条数上限（1–200，默认 50）"),
    cursor: str | None = Query(None, description="分页游标（opaque）"),
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> dict[str, Any]:
    """列出待办（分页）。

    - `status`：`todo`=只看未完成 · `done`=已完成但仍新鲜 · `all`/缺省=全部
      · `active`=**没归档的一切**（未完成 + 打勾未满归档期）
      · `archived`=**归档**（已打勾且完成时间已过 7 天）
    - `tag`：**层级筛选** —— 传 `学业` 会同时命中 `学业/高数`、`学业/化学原理`
      （不知道该传什么标签时，先调 `GET /tags` 看现有标签与计数）
    - `due_before` / `due_after`：按截止时间过滤，带时区 ISO8601
    - `source`：按来源文件路径过滤（从 Obsidian 导入的条目才有）

    ★ 2026-10-02（主人）：`done` 与 `archived` 以「打勾满 7 天」为界互斥 ——
      即已完成项满 7 天后**自动从「已完成」移到「归档」**，不需要人工干预，
      也没有单独的归档动作/接口（归档是时间的函数，不是一种状态字段）。
    """
    items, next_cursor = TodoService(db).list_items(
        status=status, due_before=due_before, due_after=due_after,
        tag=tag, source=source, limit=limit, cursor=cursor,
    )
    return {"items": items, "next_cursor": next_cursor}


@router.post("/items", response_model=TodoItemOut, status_code=status.HTTP_201_CREATED)
def create_item(
    body: TodoCreate,
    request: Request,
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> dict[str, Any]:
    """新建一条待办。两种用法二选一：

    - `raw`：一行 markdown，服务端解析语法糖（`@明天` / `!高` / `#标签`）
    - 结构化字段：显式给 `text` / `due_at` / `priority` / `recur_rule` / `tags`

    ★ 2026-10-02（主人「mcp 里创建待办的时候强制加上标签，防止忘加」）：
      **MCP / AI 来路**（内部请求头 `X-Alongside-Client: mcp`）创建时，
      `tags` **必填且不得为空** —— AI 忘了带标签会收到一条写明怎么补的 422，
      而不是静默建出一条无分类的待办。
      **人从网页/客户端来的请求不受此限**（`raw` 语法糖 / 手输文本照旧，
      QuickAdd 里 `#标签` 本就是可选项）。

    ★ 2026-10-10（主人「待办来源标记」）：同一枚来路标记顺带定 `origin` ——
      MCP/AI 来路 → `ai`；其余（网页/客户端）→ `human`。
      **答云昔 10-10 的疑问**：此前待办只记 `source_path`（笔记文件路径），
      不记「谁建的」；AI 建的与人建的分不清，回滚时不好定位。现在分得清了。
    """
    origin = "ai" if is_mcp_call(request) else "human"
    if origin == "ai":
        # 结构化 tags 优先；只给 raw 语法糖时从文本里取 #标签
        tags = body.tags or (_tags_from_raw(body.raw) if body.raw else [])
        if not tags:
            raise ValidationError(
                "MCP 创建待办必须带 tags（至少一个标签），以免产生无分类条目。"
                '例如：{"text":"交高数作业","tags":["学业","学业/高数"]}；'
                "若走 raw 语法糖，请在文本里写 #标签（如「交高数作业 #学业/高数」）。"
                "现有标签可用 todo_tag_read 查看，建议沿用既有层级标签。"
            )
    # 事件在 service 层发布（todo.item.created）
    return TodoService(db).create(body, origin=origin)


@router.get("/items/{item_id}", response_model=TodoItemOut)
def get_item(
    item_id: Annotated[str, FPath(description="待办 id")],
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> dict[str, Any]:
    """读单条待办详情（含标签、截止、周期规则、来源位置）。"""
    return TodoService(db).get(item_id)


@router.patch("/items/{item_id}", response_model=TodoItemOut)
def update_item(
    item_id: Annotated[str, FPath(description="待办 id")],
    body: TodoUpdate,
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> dict[str, Any]:
    """改一条待办：正文 / 完成状态 / 截止 / 优先级 / 周期 / 标签 / 排序。

    ⚠️ 两个易错点：`tags` 是**整组替换**（不是追加，要保留原标签请连原标签一起传）；
    `done=false` 会**清空** `done_at`（即"取消完成"）。
    """
    return TodoService(db).update(item_id, body)


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(
    item_id: Annotated[str, FPath(description="待办 id")],
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> None:
    """删除一条待办（直接删，不进回收站 —— 与 docs 的软删语义不同）。"""
    TodoService(db).delete(item_id)


@router.post("/items/{item_id}/toggle", response_model=ToggleOut)
def toggle_item(
    item_id: Annotated[str, FPath(description="待办 id")],
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> ToggleOut:
    """勾上 / 勾掉完成状态（无需传参，切换即可）。

    周期任务勾完会**自动生成下一次实例**并保留历史。
    """
    # 完成 → 写 done_at；若是周期任务 → 生成下一条实例并保留历史。
    # 事件在 service 层发布（todo.item.completed / todo.item.updated / todo.item.created）。
    return TodoService(db).toggle(item_id)


@router.post("/import", response_model=ImportOut)
def import_markdown(
    body: ImportIn,
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> dict[str, Any]:
    """从 Obsidian 文件批量导入，保留原行位置（记录 source_path + 行号）。

    两种来源二选一：content（直接给 markdown 文本）或 path（本机文件路径）。
    """
    if body.content is not None:
        content, source_path = body.content, body.path
    elif body.path is not None:
        p = Path(body.path)
        if not p.is_file():
            raise NotFoundError(f"导入源文件不存在：{body.path}")
        content, source_path = p.read_text(encoding="utf-8"), body.path
    else:
        raise ValidationError("import 需要 content 或 path 至少一个")
    imported, lines, items = TodoService(db).import_markdown(content, source_path)
    return {"imported": imported, "lines": lines, "items": items}


@router.post("/export", response_model=ExportOut)
def export_markdown(
    body: ExportIn | None = None,
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> ExportOut:
    """导出回 markdown 文本（供前端 diff 预览，v0.1 不自动写回 Obsidian）。"""
    body = body or ExportIn()
    markdown = TodoService(db).export_markdown(status=body.status, tag=body.tag)
    return ExportOut(markdown=markdown)


@router.get("/tags", response_model=list[TodoTagOut])
def list_tags(
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """★ 标签汇总（AI 一览分类 / 前端标签条取数）。

    返回库内**实际存在**的标签及计数，按未完成数降序。
    scope 仍是 `todo:read` —— 纯只读，无需任何新权限。
    """
    return TodoService(db).tag_summary()


@router.get("/summary", response_model=SummaryOut)
def summary(
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> SummaryOut:
    """概览统计：今日待办 / 逾期 / 本周完成。"""
    return TodoService(db).summary()


@router.get("/today-summary")
def today_summary(
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> dict[str, Any]:
    """U2 今日摘要（today-summary 规范 v1：{title, items<=5:[{text,state,count?}], link}）。

    内核 BFF /api/v1/summary/today 的转发目标（派工令 62）。
    """
    return TodoService(db).today_summary()


# ═══════ ★（主人 2026-09-25「做」）：待办到期提醒 ═══════
# 链路：due_scheduler 扫到期 → publish("todo.item.due") → push.link → web push
@router.post("/due/tick")
def post_due_tick(
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> dict:
    """手动触发一轮待办到期扫描（验收/排障用；不依赖定时是否开启）。"""
    from .due_scheduler import run_due_tick

    return {"ok": True, "count": run_due_tick(db)}


@router.get("/due/scheduler")
def get_due_scheduler_status(
    _user: UserDep = Depends(get_current_user),
) -> dict:
    """待办到期提醒调度状态（enabled/running/poll/window）。"""
    from .due_scheduler import scheduler_status

    return scheduler_status()


try:
    from .due_scheduler import start_scheduler as _start_due

    _start_due()
except Exception as _due_exc:  # noqa: BLE001
    import logging

    logging.getLogger("todo.due").warning("待办到期提醒调度启动失败: %s", _due_exc)
