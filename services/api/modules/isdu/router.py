"""i山大 插件路由（v0.2：公开公告已打通，课表/成绩/一卡通等登录态）。

★ 前缀由内核按 manifest.api.base 自动加，这里不要写 prefix=。
★ 本文件**不能**加 `from __future__ import annotations`（会让 `-> None` 变成字符串，
  FastAPI 误判 204 带响应体，整个后端起不来 —— 见 calendar/router.py 的教训）。
★ 不返回假数据：需要登录的端点明确报错，不拿空数组冒充「没有课」。
"""
import re
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from core.deps import get_current_user, get_db
from core.errors import ValidationError
from core.security import User

from .models import IsduNotice
from .schema import CastgcIn, LoginOut, NoticeOut, StatusOut, SyncOut
from .service import (
    authed_get,
    clear_session,
    exchange_castgc,
    fetch_book_info,
    fetch_book_search,
    fetch_bus,
    fetch_card_bill,
    fetch_classroom_buildings,
    fetch_classroom_occupancy,
    fetch_courses,
    fetch_dorm_areas,
    fetch_dorm_buildings,
    fetch_dorm_electricity,
    fetch_library_list,
    fetch_library_rooms,
    fetch_library_seat_map,
    fetch_library_seats,
    fetch_news_detail,
    fetch_news_list,
    fetch_notices,
    fetch_phone,
    fetch_phone_blocks,
    fetch_school_calendar,
    fetch_studyroom_buildings,
    fetch_studyroom_campuses,
    fetch_studyroom_rooms,
    fetch_volunteer_info,
    import_courses,
    list_notices,
    request_is_local,
    require_login_error,
    session_state,
    sync_notices,
)

router = APIRouter()


class ImportCoursesIn(BaseModel):
    """导入 iSDU 课表。"""

    payload: Any = Field(
        default=None,
        description="上游课表 JSON（/v2/api/edu/course_schedule 的 data）；留空则现场拉一次",
    )
    apply: bool = Field(default=False, description="true=真正写进 course；false=只算差量")

SUPPORTS = [
    "isdu.notice.read",
    "isdu.timetable.read",
    "isdu.grade.read",
    "isdu.exam.read",
    "isdu.card.read",
    "isdu.studyroom.read",
    "isdu.electricity.read",
    "isdu.volunteer.read",
    "isdu.bus.read",
    "isdu.library.read",
    "isdu.news.read",
    "isdu.book.read",
    "isdu.phone.read",
    "isdu.calendar.read",
]
MISSING: list[str] = []


def _last_sync(db: Session) -> datetime | None:
    row = db.exec(select(IsduNotice).order_by(IsduNotice.updated_at.desc()).limit(1)).first()
    return row.updated_at if row is not None else None


@router.get("/health")
def health(db: Annotated[Session, Depends(get_db)]) -> dict[str, Any]:
    """内核与「今日总览」据此判断能力是否可用。

    公开公告能拉通 ⇒ ok=True；课表等仍缺登录态，在 status 里如实列出。
    """
    count = len(db.exec(select(IsduNotice.id)).all())
    return {
        "ok": True,
        "adapter": "partial",
        "notice_count": count,
        "supports": SUPPORTS,
        "missing": MISSING,
        "upstream": "https://i.sdu.edu.cn/isduapi",
    }


@router.get("/status", response_model=StatusOut)
def status(db: Annotated[Session, Depends(get_db)]) -> StatusOut:
    count = len(db.exec(select(IsduNotice.id)).all())
    return StatusOut(
        adapter="partial",
        notice_count=count,
        last_sync_at=_last_sync(db),
        supports=list(SUPPORTS),
        missing=list(MISSING),
    )


@router.get("/notices", response_model=list[NoticeOut])
def notices(
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(30, ge=1, le=200),
) -> list[NoticeOut]:
    """本地已缓存的公告（不触发网络）。要刷新走 POST /sync。"""
    return [
        NoticeOut(
            notice_id=n.notice_id,
            title=n.title,
            content=n.content,
            image=n.image,
            route=n.route,
            url=n.url,
            popup=n.popup,
        )
        for n in list_notices(db, limit)
    ]


@router.post("/sync", response_model=SyncOut)
def sync(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> SyncOut:
    """从上游拉公告（公开接口，不需要 i山大 登录态）。"""
    synced = sync_notices(db)
    return SyncOut(synced=synced, last_sync_at=datetime.now(UTC))


@router.get("/preview-notices")
def preview_notices() -> list[dict[str, Any]]:
    """上游实时公告（不落库）—— 排障用：区分「上游没数据」与「本地没同步」。"""
    return fetch_notices()


# ── 登录：CASTGC 兑换 token（不碰密码，凭证只在进程内存）────────────────────


@router.post("/login/castgc", response_model=LoginOut)
def login_castgc(
    body: CastgcIn,
    request: Request,
    _user: Annotated[User, Depends(get_current_user)],
) -> LoginOut:
    """用浏览器 CAS 登录后的 CASTGC 换 App token。

    ★ 只允许**本机浏览器**导入（见 service.request_is_local）：
      票据不经过任何远端服务器，是这套方案能成立的前提。

    取法（1 分钟）：浏览器登录 https://pass.sdu.edu.cn/cas/login 后，
    开发者工具 → Application → Cookies → pass.sdu.edu.cn → 复制 `CASTGC` 的值。
    """
    if not request_is_local(
        request.client.host if request.client else None,
        request.headers.get("x-forwarded-for"),
        request.headers,
    ):
        raise ValidationError(
            "拒绝在远端实例上导入 CASTGC：该凭据是 CAS 会话票据，等价于「已登录的你」，"
            "不允许经过任何服务器。请把 Alongside 跑在你自己的电脑上（本项目即单人自托管）。"
            "确需内网共享时，显式设置 ISDU_ALLOW_REMOTE_LOGIN=1 自担风险。"
        )
    res = exchange_castgc(body.castgc, body.device)
    return LoginOut(
        logged_in=bool(res.get("logged_in")),
        token_preview=str(res.get("token_preview", "")),
    )


@router.get("/session")
def session_info(_user: Annotated[User, Depends(get_current_user)]) -> dict[str, Any]:
    return session_state()


@router.delete("/session")
def logout(_user: Annotated[User, Depends(get_current_user)]) -> dict[str, bool]:
    """退出登录：抹掉内存里的 token / CASTGC（不留任何落盘痕迹）。"""
    clear_session()
    return {"logged_in": False}


# ── 需要登录态的只读数据（原样返回上游 JSON，等摸清形状再做类型化视图）──────


@router.get("/timetable")
def timetable(_user: Annotated[User, Depends(get_current_user)]) -> Any:
    """课表（已映射成前端可直接画的形状；不写库）。

    每项：{name, teacher, location, weekday(0=周一), start_section, end_section,
           weeks, start_time}
    """
    from .map_courses import map_courses

    raw = fetch_courses()
    snapshot, report = map_courses(raw)
    return {"courses": snapshot.get("courses", []), "report": report}


@router.get("/courses")
def courses(_user: Annotated[User, Depends(get_current_user)]) -> Any:
    return authed_get("/v2/api/edu/course_schedule")


@router.get("/preview-courses")
def preview_courses(
    request: Request,
    _user: Annotated[User, Depends(get_current_user)],
) -> Any:
    """课表映射预览（不写库）：返回 mapping 诊断 + 差量计划。

    第一次对接时用它回答两个问题：「字段认出来了吗」「会新增/更新几门课」。
    """
    return import_courses(request, apply=False)


@router.post("/import/courses")
def import_courses_endpoint(
    body: ImportCoursesIn,
    request: Request,
    _user: Annotated[User, Depends(get_current_user)],
) -> Any:
    """把 iSDU 课表写进 `course`（幂等：同名+同星期+同节次视为同一门课）。"""
    return import_courses(request, payload=body.payload, apply=body.apply)


@router.get("/exams")
def exams(_user: Annotated[User, Depends(get_current_user)]) -> Any:
    return authed_get("/v2/api/edu/exam_schedule")


@router.get("/grades")
def grades(_user: Annotated[User, Depends(get_current_user)]) -> Any:
    return authed_get("/v2/api/edu/grades")


#: 只允许 edu 模块下的简单路径；`..`/`%`/`?`/`#`/`//` 一律拒绝
#  （httpx 会把 `/v2/api/edu/../core/card/bill` 归一化成 `/v2/api/core/...`，
#    仅靠 startswith 白名单能被绕过 —— 2026-11-14 实测）
_RAW_PATH_RE = re.compile(r"^/v2/api/edu/[A-Za-z0-9_\-]+(?:/[A-Za-z0-9_\-]+)*$")


@router.get("/raw")
def raw(
    path: Annotated[
        str,
        Query(max_length=300, description="上游只读路径，形如 /v2/api/edu/course_schedule"),
    ],
    _user: Annotated[User, Depends(get_current_user)],
) -> Any:
    """排障用：带 token 直取上游 edu 只读路径（严格白名单，防 SSRF/路径穿越）。"""
    if not _RAW_PATH_RE.fullmatch(path):
        raise ValidationError("只允许 /v2/api/edu/<字母数字/斜杠> 形式的只读路径")
    return authed_get(path)


@router.get("/card/bill")
def card_bill(
    _user: Annotated[User, Depends(get_current_user)],
    start_date: str = Query(max_length=32, description="YYYY-MM-DD"),
    end_date: str = Query(max_length=32, description="YYYY-MM-DD"),
) -> Any:
    """校园卡账单 + 余额（只读）。上游返回 {account, lastBalance, bill}。"""
    return fetch_card_bill(start_date, end_date)


@router.get("/electricity/areas")
def electricity_areas(_user: Annotated[User, Depends(get_current_user)]) -> Any:
    """宿舍电量：校区列表。"""
    return fetch_dorm_areas()


@router.get("/electricity/buildings")
def electricity_buildings(
    _user: Annotated[User, Depends(get_current_user)],
    area_id: str = Query(max_length=64, description="校区 ID（来自 /electricity/areas）"),
) -> Any:
    """宿舍电量：楼栋列表。"""
    return fetch_dorm_buildings(area_id)


@router.get("/electricity")
def electricity(
    _user: Annotated[User, Depends(get_current_user)],
    area_id: str = Query(max_length=64, description="校区 ID"),
    building_code: str = Query(
        max_length=32, description="楼栋 code（来自 /electricity/buildings）"
    ),
    room: str = Query(max_length=32, description="房间号，如 101"),
) -> Any:
    """宿舍电量：剩余度数（只读）。"""
    return fetch_dorm_electricity(area_id, building_code, room)


@router.get("/studyrooms/campuses")
def studyroom_campuses(_user: Annotated[User, Depends(get_current_user)]) -> Any:
    """自习室：可选校区。"""
    return {"campuses": fetch_studyroom_campuses()}


@router.get("/studyrooms/buildings")
def studyroom_buildings(
    _user: Annotated[User, Depends(get_current_user)],
    campus: str = Query(max_length=40, description="校区名，如 青岛校区"),
) -> Any:
    """自习室：某校区的教学楼列表。"""
    return fetch_studyroom_buildings(campus)


@router.get("/studyrooms/rooms")
def studyroom_rooms(
    _user: Annotated[User, Depends(get_current_user)],
    campus: str = Query(description="校区名"),
    building: str = Query(max_length=80, description="buildingId，如 K3楼"),
    date: str = Query(max_length=32, description="YYYY-MM-DD"),
) -> Any:
    """自习室：某楼某天的空闲教室（上游可能为空对象）。"""
    return fetch_studyroom_rooms(campus, building, date)


@router.get("/volunteer")
def volunteer(_user: Annotated[User, Depends(get_current_user)]) -> Any:
    """志愿：总时长 + 明细。"""
    return fetch_volunteer_info()


@router.get("/bus")
def bus(
    _user: Annotated[User, Depends(get_current_user)],
    rest_week: int = Query(0, ge=0, le=1, description="0=工作日 1=双休日"),
) -> Any:
    """校车时刻表（独立 H5 系统 sd114 的 JSON 接口）。"""
    return fetch_bus(rest_week)


@router.get("/library/seats")
def library_seats(_user: Annotated[User, Depends(get_current_user)]) -> Any:
    """图书馆座位预约记录（只读）。"""
    return fetch_library_seats()


@router.get("/library/libraries")
def library_libraries(_user: Annotated[User, Depends(get_current_user)]) -> Any:
    """图书馆：可选的图书馆全名列表。"""
    return {"libraries": fetch_library_list()}


@router.get("/library/rooms")
def library_rooms(
    _user: Annotated[User, Depends(get_current_user)],
    library: str = Query(max_length=64, description="图书馆全名，如 青岛校区图书馆"),
) -> Any:
    """图书馆：楼层/区域树（带实时空位数）。"""
    return fetch_library_rooms(library)


@router.get("/library/seat-map")
def library_seat_map(
    _user: Annotated[User, Depends(get_current_user)],
    room_id: int = Query(ge=1, le=10**9, description="阅览区 id（room/list 的叶子节点）"),
) -> Any:
    """图书馆：某阅览区实时座位状态（1=空闲 6=使用中 7=临时离开）。"""
    return fetch_library_seat_map(room_id)


@router.get("/news/list")
def news_list(_user: Annotated[User, Depends(get_current_user)]) -> Any:
    """校园资讯列表（只读）。"""
    return fetch_news_list()


@router.get("/news")
def news(
    _user: Annotated[User, Depends(get_current_user)],
    news_id: str = Query(max_length=64, description="资讯 id（来自 /news/list）"),
) -> Any:
    """资讯详情（正文 HTML，由前端转纯文本展示）。"""
    return fetch_news_detail(news_id)


@router.get("/library/book-search")
def library_book_search(
    _user: Annotated[User, Depends(get_current_user)],
    key: str = Query(max_length=120, description="书名/作者/主题关键词"),
) -> Any:
    """馆藏检索。"""
    return fetch_book_search(key)


@router.get("/library/book")
def library_book(
    _user: Annotated[User, Depends(get_current_user)],
    book_id: str = Query(max_length=64, description="bookId（来自检索结果）"),
) -> Any:
    """书目详情 + 馆藏位置。"""
    return fetch_book_info(book_id)


@router.get("/phone/blocks")
def phone_blocks(_user: Annotated[User, Depends(get_current_user)]) -> Any:
    """校内电话分组。"""
    return fetch_phone_blocks()


@router.get("/phone")
def phone(
    _user: Annotated[User, Depends(get_current_user)],
    param0: str = Query(max_length=64, description="分组 id（来自 /phone/blocks）"),
    param1: str = Query(max_length=64, description="部门 id（来自 /phone/blocks）"),
) -> Any:
    """部门电话。"""
    return fetch_phone(param0, param1)


@router.get("/calendar")
def school_calendar(_user: Annotated[User, Depends(get_current_user)]) -> Any:
    """校历：学期开始日 / 总周数 / 各月事件。"""
    return fetch_school_calendar()


@router.get("/classrooms/buildings")
def classroom_buildings(_user: Annotated[User, Depends(get_current_user)]) -> Any:
    """空闲教室：教学楼列表（公开接口，仍要求本插件已登录）。"""
    return fetch_classroom_buildings()


@router.get("/classrooms/occupancy")
def classroom_occupancy(
    _user: Annotated[User, Depends(get_current_user)],
    date: str = Query(max_length=32, description="YYYY-MM-DD"),
    building_id: str = Query(max_length=80, description="教学楼 id（来自 /classrooms/buildings）"),
) -> Any:
    """教室占用表：哪些节次有课（其余即空闲）。"""
    return fetch_classroom_occupancy(date, building_id)


@router.get("/card")
def card() -> None:
    require_login_error()
