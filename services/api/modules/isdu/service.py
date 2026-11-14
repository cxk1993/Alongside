"""i山大 上游访问（只读）。

已确认的网关（2026-11-13 静态分析 + 实测）：

    BASE = https://i.sdu.edu.cn/isduapi
    公开：GET /v2/api/core/notice/latest
          → {"code":0,"data":[{noticeId,title,content,image,route,url,popup}]}
    公开：GET /v2/api/core/notice/carousel_image
    需登录：/v2/api/edu/{course_schedule,grades,exam_schedule,semester_history}
            → 未带 token 时返回 {"code":40101,"message":"登录状态已失效"}

★ 本模块只做 GET，不碰登录。登录（CAS / 学号+密码）的请求体经 RSA/AES 处理后
  才被服务端接受，静态分析拿不到密钥 —— 详见 README「登录链路」。
"""
from __future__ import annotations

import contextlib
import json
import re
from typing import Any

import httpx
from sqlmodel import Session, select

from core.deps import get_plugin_client
from core.errors import ServiceUnavailableError, ValidationError
from core.events import event_bus

from .map_courses import map_courses
from .models import IsduNotice
from .timetable import normalize_courses, plan_push

BASE = "https://i.sdu.edu.cn/isduapi"
TIMEOUT_S = 10.0


class UpstreamError(ServiceUnavailableError):
    """上游返回非 code=0 / 网络失败 —— 映射成 503，而不是 500。"""


def _client() -> httpx.Client:
    # 回环/校内地址都不该走系统代理；这里 trust_env=False 与内核口径一致。
    return httpx.Client(
        base_url=BASE,
        timeout=TIMEOUT_S,
        trust_env=False,
        headers={
            "Accept": "application/json",
            "User-Agent": "Alongside/isdu-plugin 0.1",
        },
    )


def _unwrap(path: str, payload: dict[str, Any]) -> Any:
    # ★ 少数接口（如 /v2/api/core/news?id=…）直接返回资源本体，没有 {code,data} 外壳。
    if "code" not in payload:
        return payload
    code = payload.get("code")
    if code == 40101:
        raise ValidationError("iSDU 登录态已失效，请重新登录（粘贴新的 CASTGC）。")
    if code != 0:
        raise UpstreamError(f"{path} 返回 code={code} message={payload.get('message')!r}")
    return payload.get("data")


def fetch_notices() -> list[dict[str, Any]]:
    """拉公开公告列表（不需要登录）。"""
    with _client() as client:
        try:
            r = client.get("/v2/api/core/notice/latest")
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise UpstreamError(f"公告接口请求失败（{type(exc).__name__}）") from exc
        try:
            body = r.json()
        except ValueError as exc:
            raise UpstreamError("公告接口返回非 JSON") from exc
    data = _unwrap("/v2/api/core/notice/latest", body) or []
    if not isinstance(data, list):
        raise UpstreamError("公告接口返回结构不是数组")
    return [d for d in data if isinstance(d, dict) and d.get("noticeId") is not None]


def list_notices(db: Session, limit: int = 30) -> list[IsduNotice]:
    return list(
        db.exec(select(IsduNotice).order_by(IsduNotice.notice_id.desc()).limit(limit)).all()
    )


def sync_notices(db: Session) -> int:
    """把上游公告落库（按 noticeId 幂等 upsert），返回新增/更新条数。"""
    items = fetch_notices()
    changed = 0
    for item in items:
        notice_id = int(item["noticeId"])
        row = db.exec(select(IsduNotice).where(IsduNotice.notice_id == notice_id)).first()
        if row is None:
            row = IsduNotice(notice_id=notice_id)
            db.add(row)
        row.title = str(item.get("title") or "")[:200]
        row.content = str(item.get("content") or "")
        row.image = str(item.get("image") or "")[:500]
        row.route = str(item.get("route") or "")[:200]
        row.url = str(item.get("url") or "")[:500]
        row.popup = int(item.get("popup") or 0)
        changed += 1
    db.commit()
    return changed


def require_login_error() -> None:
    """课表/成绩/一卡通统一用的明确错误（不伪装成空数据）。"""
    raise ValidationError(
        "i山大 未登录：请在插件「课表/成绩」页粘贴浏览器 CAS 登录后的 CASTGC（TGT-…）完成兑换。"
        "凭证只留在进程内存，不写库、不进 .env。"
    )


# ── 登录（CASTGC 兑换）────────────────────────────────────────────────────
# 实测（2026-11-13）：
#   POST /v2/api/auth/login/castgc   form: castgc=TGT-...
#   假 TGT → {"code":50001,"message":"castgc验证失败。"}
#   ⇒ 只要拿到浏览器里 CAS 登录后的 CASTGC，就能换到 App token，不需要密码。
_SESSION: dict[str, Any] = {"token": None, "has_castgc": False}

#: device 只是 CAS 设备指纹 cookie，合法字符集有限；挡住头注入（CRLF / ";"）
_DEVICE_RE = re.compile(r"^[A-Za-z0-9._~%=-]{1,128}$")


def session_state() -> dict[str, Any]:
    return {
        "logged_in": _SESSION["token"] is not None,
        "has_castgc": bool(_SESSION.get("has_castgc")),
    }


def clear_session() -> None:
    """退出登录：把内存里的凭据抹掉（列表/字符串不落地，抹引用即可）。"""
    _SESSION["token"] = None
    _SESSION["has_castgc"] = False


def request_is_local(
    client_host: str | None,
    forwarded_for: str | None,
    forwarded_headers: dict[str, str] | None = None,
) -> bool:
    """判断请求是不是「本机浏览器」发的 —— 决定允不允许导入 CASTGC。

    ★ 为什么要有这道闸：CASTGC 是 CAS 的会话票据，等价于「已登录的你」。
      只有「Alongside 跑在用户自己机器上、浏览器也从本机访问」时，
      这张票据才没有离开机器。一旦实例跑在服务器上（哪怕是那台香港演示机），
      用户粘贴的 CASTGC 就会明文过公网落到服务器 —— 那是不能接受的。

    判定口径（宁可错杀）：
      1. 有 X-Forwarded-For 且值不是回环 ⇒ 一定经过了 nginx/隧道 = 远端，拒；
      2. 直连客户端地址不是 127.0.0.1 / ::1 ⇒ 远端，拒。
    例外：显式设 ISDU_ALLOW_REMOTE_LOGIN=1（自担风险，比如给家人开的内网实例）。
    """
    import os

    if (os.environ.get("ISDU_ALLOW_REMOTE_LOGIN", "") or "").strip() in {"1", "true", "yes"}:
        return True
    if forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        if first and not (first.startswith("127.") or first in {"::1", "localhost"}):
            return False
    if forwarded_headers:
        for header in ("x-real-ip", "cf-connecting-ip", "x-client-ip"):
            value = (forwarded_headers.get(header) or "").strip()
            if value and not (value.startswith("127.") or value in {"::1", "localhost"}):
                return False
        forwarded = (forwarded_headers.get("forwarded") or "").strip()
        if forwarded:
            first = forwarded.split(";")[0].split("=")[-1].strip().strip('"')
            if first and not (first.startswith("127.") or first in {"::1", "localhost"}):
                return False
    return client_host in {"127.0.0.1", "::1", "localhost", None}


def exchange_castgc(castgc: str, device: str | None = None) -> dict[str, Any]:
    """用 CAS 的 CASTGC 换 App token（凭证只在内存里，不落库、不进 .env）。

    ★ 2026-11-14 实测：上游 `/login/castgc` 要的是**整条 cookie 串** `CASTGC=TGT-…`，
      不是裸 TGT。只发裸值会一直回 50001「castgc验证失败」（这是之前卡住的真正原因）。
    ★ device 是 CAS「信任此设备」写下的设备指纹 cookie，部分部署会校验；带上无害。
    """
    if not castgc.strip():
        raise ValidationError("castgc 不能为空")
    value = castgc.strip()
    if not value.upper().startswith("CASTGC="):
        value = "CASTGC=" + value
    headers: dict[str, str] = {}
    device_value = (device or "").strip()
    if device_value:
        if not _DEVICE_RE.fullmatch(device_value):
            raise ValidationError("device 只能是字母/数字/. _ ~ % = -（最长 128）")
        headers["Cookie"] = f"device={device_value}"
    with _client() as client:
        try:
            r = client.post(
                "/v2/api/auth/login/castgc",
                data={"castgc": value},
                headers=headers,
            )
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise UpstreamError(f"CASTGC 兑换请求失败（{type(exc).__name__}）") from exc
        try:
            body = r.json()
        except ValueError as exc:
            raise UpstreamError("CASTGC 兑换返回非 JSON") from exc
    code = body.get("code")
    if code != 0:
        raise ValidationError(f"CASTGC 兑换失败：code={code} message={body.get('message')!r}")
    data = body.get("data") or {}
    token = None
    if isinstance(data, dict):
        token = data.get("token") or data.get("accessToken") or data.get("access_token")
    if not token:
        # ★ 不打印 data 值：万一上游把 token 放在别的键里，会跟错误信息进日志/响应
        keys = sorted(data.keys())[:10] if isinstance(data, dict) else type(data).__name__
        raise ValidationError(f"兑换成功但没有拿到 token（data keys={keys}）")
    _SESSION["token"] = token
    # CASTGC 换完 token 即失效用途；只留“曾经拿到过”的布尔，别长期留票据
    _SESSION["has_castgc"] = True
    return {"logged_in": True, "token_preview": f"{str(token)[:8]}…"}


def authed_get(path: str, params: dict[str, Any] | None = None) -> Any:
    """带 token 打需要登录的只读接口（课表/成绩/考试/自习室/电量/校园卡…）。

    ★ 2026-11-14 实测：`/v2/api/*` 认的请求头是 **`token: <32位hex>`**，
      不是 `Authorization: Bearer`。用错头会一直回 40101「登录状态已失效」。
    """
    token = _SESSION["token"]
    if not token:
        require_login_error()
    with _client() as client:
        try:
            r = client.get(path, headers={"token": str(token)}, params=params or None)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise UpstreamError(f"上游请求失败：{path}（{type(exc).__name__}）") from exc
        try:
            body = r.json()
        except ValueError as exc:
            raise UpstreamError(f"上游返回非 JSON：{path}") from exc
    return _unwrap(path, body)


# ── 课表：iSDU JSON → 映射 → 差量 → 写 course ─────────────────────────────
def fetch_courses() -> Any:
    """上游课表原始 JSON（形状未定，映射在 map_courses 里做）。"""
    return authed_get("/v2/api/edu/course_schedule")


def import_courses(
    request: Any,
    *,
    payload: Any = None,
    apply: bool = False,
) -> dict[str, Any]:
    """把 iSDU 课表写进 `course`（幂等：同名+同星期+同节次视为同一门课）。

    · payload 留空 ⇒ 现场带 token 拉一次；
    · apply=False ⇒ 只算差量（先看会新增/更新几门，再决定写不写）；
    · 返回里带 `mapping` 诊断：rows / mapped / weekday_basis / unknown_keys，
      第一次对接时照它改 `map_courses.FIELD_ALIASES`。

    ★ 跨插件写入走内核的 `get_plugin_client`：能力必须写在
      manifest.optionalDependencies 里（ADR-0002「声明即授权」）。
    """
    raw = payload if payload is not None else fetch_courses()
    snapshot, mapping = map_courses(raw)
    if not snapshot.get("courses"):
        raise ValidationError(f"没能从 iSDU 课表映射出课程；诊断：{mapping}")

    normalized = normalize_courses(snapshot)
    client = get_plugin_client(request, ["course.item.write", "course.item.patch"])
    existing = client.get("/api/v1/course/items") or []
    plan = plan_push(normalized, existing)

    applied = {"create": 0, "update": 0}
    if apply:
        for item in plan["create"]:
            client.post("/api/v1/course/items", json=item)
            applied["create"] += 1
        for item in plan["update"]:
            item_id = item.get("id")
            body = {k: v for k, v in item.items() if k != "id" and not k.startswith("_")}
            if item_id:
                client.patch(f"/api/v1/course/items/{item_id}", json=body)
                applied["update"] += 1

    if apply:
        with contextlib.suppress(Exception):
            # 事件是副作用，不能反过来打断导入
            event_bus.publish(
                "isdu.timetable.synced",
                {
                    "created": applied["create"],
                    "updated": applied["update"],
                    "total": len(normalized),
                },
                source="isdu",
            )

    return {
        "normalized": len(normalized),
        "plan": {
            "create": len(plan["create"]),
            "update": len(plan["update"]),
            "skip": len(plan["skip"]),
            "sample_create": plan["create"][:5],
        },
        "applied": applied,
        "mapping": mapping,
    }

# ── 第一批扩展：自习室 / 宿舍电量 / 校园卡 / 志愿 ─────────────────────────────

#: 自习室接口的校区取值是**中文校区名**（2026-11-14 实测；济南校区返回空）。
STUDYROOM_CAMPUSES: tuple[str, ...] = (
    "中心校区",
    "洪家楼校区",
    "趵突泉校区",
    "千佛山校区",
    "兴隆山校区",
    "软件园校区",
    "青岛校区",
    "威海校区",
)


def fetch_studyroom_campuses() -> list[str]:
    """可选校区（iSDU 的上游没有列表接口，用实测有效的固定集合）。"""
    return list(STUDYROOM_CAMPUSES)


def fetch_studyroom_buildings(campus: str) -> Any:
    """某校区的教学楼列表：{buildings:[{name,buildingId}], campusId}。"""
    if not (campus or "").strip():
        raise ValidationError("campus 不能为空")
    return authed_get("/v2/api/edu/studyrooms/buildings", {"campus": campus.strip()})


def fetch_studyroom_rooms(campus: str, building: str, date: str) -> Any:
    """某楼某天的空闲教室（上游 data 可能是空对象，表示暂时没有可约教室）。"""
    if not campus.strip() or not building.strip() or not date.strip():
        raise ValidationError("campus / building / date 都不能为空")
    return authed_get(
        "/v2/api/edu/studyrooms/data",
        {"campusId": campus.strip(), "buildingId": building.strip(), "date": date.strip()},
    )


def fetch_dorm_areas() -> Any:
    """宿舍电量：校区列表 [{areaId, areaName}]。"""
    return authed_get("/v2/api/core/dorm-elec/areas")


def fetch_dorm_buildings(area_id: str) -> Any:
    """宿舍电量：某校区的楼栋 [{buildingCode, buildingName, areaId, areaName}]。"""
    if not (area_id or "").strip():
        raise ValidationError("area_id 不能为空")
    return authed_get("/v2/api/core/dorm-elec/buildings", {"areaId": area_id.strip()})


def fetch_dorm_electricity(area_id: str, building_code: str, room: str) -> Any:
    """宿舍电量：{roomName, surplus(度), recordTime, fullMessage}。"""
    if not area_id.strip() or not building_code.strip() or not room.strip():
        raise ValidationError("area_id / building_code / room 都不能为空")
    return authed_get(
        "/v2/api/core/dorm-elec/electricity",
        {
            "areaId": area_id.strip(),
            "buildingCode": building_code.strip(),
            "roomName": room.strip(),
        },
    )


def fetch_card_bill(start_date: str, end_date: str) -> Any:
    """校园卡：{account, lastBalance(余额), bill:[流水]}；日期格式 YYYY-MM-DD。"""
    if not start_date.strip() or not end_date.strip():
        raise ValidationError("start_date / end_date 都不能为空")
    return authed_get(
        "/v2/api/core/card/bill",
        {"startDate": start_date.strip(), "endDate": end_date.strip()},
    )


def fetch_volunteer_info() -> Any:
    """志愿：{totalHours, details:[{name, level, type, duration}]}。"""
    return authed_get("/v2/api/core/volunteer/get_volunteer_info")

# ── 第二批：校车 / 图书馆座位 ───────────────────────────────────────────────

#: 校车时刻表是独立 H5 系统（sd114），页面 JS 最终调这个 JSON 接口。
BUS_URL = "http://www.sd114.sdu.edu.cn:8080/web/bus/load"


def fetch_bus(rest_week: int = 0) -> Any:
    """校车时刻表：rest_week=0 工作日 / 1 双休日。

    返回 [{CAMPUS_DORMITORY, DEPARTURE_STATION, DEPARTURE_TIME, VIA_SITE,
           TERMINUS, IS_REST_WEEK, ...}]
    """
    params = {
        "schoolAddr": "",
        "startAddr": "",
        "startTime": "",
        "afterAddr": "",
        "arriveAddr": "",
        "type": str(1 if rest_week else 0),
    }
    with httpx.Client(timeout=20.0, follow_redirects=True, trust_env=False) as client:
        r = client.get(BUS_URL, params=params)
        r.raise_for_status()
        body = r.json()
    return body if isinstance(body, list) else []


def fetch_library_seats() -> Any:
    """图书馆座位预约记录：[{spaceName, seatName, startTime, endTime, status}]。"""
    return authed_get("/v2/api/library/seat/history")

# ── 第三批：空闲教室查询（xxbdeploy 的「空闲教室」app，公开 JSON 接口）──────

CLASSROOM_BASE = "https://xxbdeploy.sdu.edu.cn/prod-api/free/free-classrooms"


def _classroom_get(path: str, params: dict[str, Any] | None = None) -> Any:
    with httpx.Client(timeout=25.0, follow_redirects=True, trust_env=False) as client:
        try:
            r = client.get(CLASSROOM_BASE + path, params=params or None)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise UpstreamError(f"空闲教室接口请求失败（{type(exc).__name__}）") from exc
        try:
            body = r.json()
        except ValueError as exc:
            raise UpstreamError("空闲教室接口返回非 JSON") from exc
    if isinstance(body, dict):
        code = body.get("code")
        if code not in (None, 200, 0):
            raise UpstreamError(f"空闲教室接口返回 code={code} msg={body.get('msg')!r}")
        return body.get("data")
    return body


def fetch_classroom_buildings() -> Any:
    """教学楼列表：[{id, name, areaId}]（areaId 前缀 01=济南 02=威海 03=青岛）。"""
    return _classroom_get("/buildings")


def fetch_classroom_rooms(building_id: str) -> Any:
    """某教学楼的教室列表：[{buildingId, name}]。"""
    if not (building_id or "").strip():
        raise ValidationError("building_id 不能为空")
    return _classroom_get("/classrooms", {"buildingId": building_id.strip()})


def _section_number(raw: Any) -> int | None:
    """上游 section 是字符串：'06'→6；'0' 实测与 '9' 成对出现，按第 10 节处理。"""
    try:
        n = int(str(raw))
    except (TypeError, ValueError):
        return None
    if n == 0:
        return 10
    return n if 1 <= n <= 12 else None


def fetch_classroom_occupancy(date: str, building_id: str) -> dict[str, Any]:
    """教室占用表：{rooms:[{name, occupied:[节次]}], sections:[1..]; date, building_id}。

    ★ 上游只给「有课」的记录，没有课的时段即空闲 —— 不额外判断“有没有空教室”。
    """
    if not (date or "").strip() or not (building_id or "").strip():
        raise ValidationError("date / building_id 都不能为空")
    rooms = fetch_classroom_rooms(building_id) or []
    in_use = _classroom_get(
        "/classrooms-in-use", {"date": date.strip(), "buildingId": building_id.strip()}
    ) or []
    occupied: dict[str, set[int]] = {}
    sections: set[int] = set()
    for row in in_use:
        if not isinstance(row, dict):
            continue
        section = _section_number(row.get("section"))
        if section is None:
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        occupied.setdefault(name, set()).add(section)
        sections.add(section)
    table = []
    for room in rooms:
        if not isinstance(room, dict):
            continue
        name = str(room.get("name") or "")
        table.append({"name": name, "occupied": sorted(occupied.get(name, set()))})
    max_section = max(sections) if sections else 10
    return {
        "date": date.strip(),
        "building_id": building_id.strip(),
        "sections": list(range(1, max(max_section, 10) + 1)),
        "rooms": table,
    }

# ── 图书馆实时座位（room/list 的 library=图书馆全名；seat/list 的 roomId=叶子区域）──

#: 图书馆全名（2026-11-14 实测：只有全名能过 room/list 的参数校验）。
LIBRARIES: tuple[str, ...] = (
    "蒋震图书馆",
    "青岛校区图书馆",
    "威海校区图书馆",
    "趵突泉校区图书馆",
    "千佛山校区图书馆",
    "软件园校区图书馆",
    "兴隆山校区图书馆",
)


def fetch_library_list() -> list[str]:
    return list(LIBRARIES)


def fetch_library_rooms(library: str) -> Any:
    """楼层/区域树：每层带 freeSeatCount/totalSeatCount，children 为阅览区。"""
    if not (library or "").strip():
        raise ValidationError("library 不能为空")
    return authed_get("/v2/api/library/room/list", {"library": library.strip()})


def fetch_library_seat_map(room_id: int) -> Any:
    """某阅览区的实时座位：[{seatId, seatName, status, statusName}]。

    status：1=空闲，6=使用中，7=临时离开（实测样本）。
    """
    if not room_id:
        raise ValidationError("room_id 不能为空")
    return authed_get("/v2/api/library/seat/list", {"roomId": room_id})

# ── 第四批：校园资讯 / 馆藏查询 / 校内电话 ─────────────────────────────────

def fetch_news_list() -> Any:
    """校园资讯聚合（研究生院/青春山大/山大视点…）：[{id, site, block, title, date}]。"""
    return authed_get("/v2/api/core/news/list")


def fetch_news_detail(news_id: str) -> Any:
    """资讯详情：正文是富文本 HTML（前端只取纯文本渲染，避免注入）。"""
    if not (news_id or "").strip():
        raise ValidationError("news_id 不能为空")
    return authed_get("/v2/api/core/news", {"id": news_id.strip()})


def fetch_book_search(key: str) -> Any:
    """馆藏检索：按关键词查书目（最多 20 条）。"""
    if not (key or "").strip():
        raise ValidationError("key 不能为空")
    return authed_get("/v2/api/library/book/search", {"key": key.strip()})


def fetch_book_info(book_id: str) -> Any:
    """书目详情 + 馆藏。

    字段：{title, author, isbn, digest, freeCount,
           collectionBooks:[{callNo, location, status}]}
    """
    if not (book_id or "").strip():
        raise ValidationError("book_id 不能为空")
    return authed_get("/v2/api/library/book/info", {"bookId": book_id.strip()})


def fetch_phone_blocks() -> Any:
    """校内电话分组：[{title, data:[{name, param0, param1}]}]。"""
    return authed_get("/v2/api/core/phone/blocks")


def fetch_phone(param0: str, param1: str) -> Any:
    """某部门电话：[{title, tel}]。"""
    if not param0.strip() or not param1.strip():
        raise ValidationError("param0 / param1 都不能为空")
    return authed_get("/v2/api/core/phone", {"param0": param0.strip(), "param1": param1.strip()})

# ── 第五批：校历（/v2/api/edu/school_calendar 的 data 是一段 JSON 字符串）──────

def _calendar_from_raw(data: Any) -> dict[str, Any]:
    """把上游校历 JSON（对象）归一成前端可直接用的结构（纯函数，便于测试）。"""
    if not isinstance(data, dict):
        raise UpstreamError("校历接口返回结构不是对象")

    start = data.get("start") if isinstance(data.get("start"), list) else []

    def _int_at(index: int, default: int | None) -> int | None:
        try:
            return int(start[index])
        except (IndexError, TypeError, ValueError):
            return default

    start_year = _int_at(0, None)
    start_month = _int_at(1, 8) or 8
    start_day = _int_at(2, 1) or 1
    if start_year is not None and not (2000 <= start_year <= 2100):
        start_year = None

    months: list[dict[str, Any]] = []
    for key, value in data.items():
        if not str(key).isdigit() or not isinstance(value, list):
            continue
        month = int(key)
        if not 1 <= month <= 12:
            continue
        year = None
        if start_year is not None:
            year = start_year if month >= start_month else start_year + 1
        events = []
        for row in value:
            if not isinstance(row, list) or len(row) < 2:
                continue
            try:
                day = int(row[0])
            except (TypeError, ValueError):
                continue
            if not 1 <= day <= 31:
                continue
            week = row[2] if len(row) > 2 else None
            try:
                week = int(week) if week is not None else None
            except (TypeError, ValueError):
                week = None
            events.append(
                {
                    "day": day,
                    "name": str(row[1])[:200],
                    "week": week,
                    "holiday": bool(row[3]) if len(row) > 3 else False,
                }
            )
        months.append({"month": month, "year": year, "events": events})
    # 按「学年」顺序：8,9,10,11,12,1,2,… 而不是 1,2,…
    months.sort(key=lambda m: (m["month"] < start_month, m["month"]))
    comments = data.get("comments") or []
    try:
        weeks = int(data.get("weeks") or 0)
    except (TypeError, ValueError):
        weeks = 0

    return {
        "start_date": f"{start_year:04d}-{start_month:02d}-{start_day:02d}" if start_year else None,
        "weeks": max(0, weeks),
        "comments": [str(c)[:500] for c in comments if isinstance(c, str)][:20],
        "months": months,
    }


def fetch_school_calendar() -> dict[str, Any]:
    """校历：{start_date, weeks, comments, months:[{month, year, events}]}。

    上游 data 形如：
        {"8":[[28,"教工上班",0,0],[31,"学生上课",1,0]], ...,
         "comments":["..."], "start":[2026,8,31], "weeks":29}
    事件四元组 = [日, 名称, 第几周, 是否节假日(1/0)]。
    """
    raw = authed_get("/v2/api/edu/school_calendar")
    data = json.loads(raw) if isinstance(raw, str) else raw
    return _calendar_from_raw(data)
