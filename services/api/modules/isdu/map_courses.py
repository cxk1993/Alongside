"""iSDU 课表 JSON → 本插件中间格式。

★ 2026-11-14 用真实响应校准过（`GET /v2/api/edu/course_schedule`，字段已脱敏）：
    {"courseIndex":"1","courseTeacher":"示例教师","courseWeeks":"[1,2,3,4]",
     "courseName":"示例课程","courseWeekday":2,"courseLocation":"示例教学楼A101",
     "userCourse":false,"courseOrder":1,"startTime":"08:00","id":1,"credit":3.0,
     "courseId":"sd00000000","addition":null}

  字段口径（实测）：
    · courseWeekday：1=周一 … 7=周日
    · courseOrder：第几大节（1..5），对应 08:00 / 10:10 / 14:00 / 16:10 / 19:00
      ⇒ 换算成 course 的小节号：start = 2*order-1，end = 2*order
    · courseWeeks：JSON 字符串数组，如 "[1,2,3,4]" ⇒ 归一成 "1-4"
    · courseLocation 为 "无" 时视为没有地点

  其它学校/字段变体走下面的别名兜底，并如实上报 unknown_keys。
"""

from __future__ import annotations

import json
from typing import Any

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("courseName", "course_name", "name", "course", "title"),
    "teacher": ("courseTeacher", "teacher", "teacherName", "teacher_name"),
    "location": ("courseLocation", "location", "classroom", "classRoom", "place", "room"),
    "weekday": ("courseWeekday", "weekday", "weekDay", "dayOfWeek", "day"),
    "start_section": ("courseOrder", "startSection", "start_section", "sectionStart"),
    "weeks": ("courseWeeks", "weeks", "weekList", "weekRange", "weekStr"),
    "start_time": ("startTime", "start_time", "beginTime"),
    "end_time": ("endTime", "end_time", "finishTime"),
}

LIST_KEYS = ("courses", "courseList", "course_list", "list", "items", "schedule", "data")
TERM_KEYS = ("termStart", "term_start", "startDate", "semesterStart")

#: 大节 → 小节号（course 里一节 = 45 分钟；一大节 = 两小节）
_PERIOD_TO_SECTION = {1: (1, 2), 2: (3, 4), 3: (5, 6), 4: (7, 8), 5: (9, 10)}


def _pick(row: dict[str, Any], field: str) -> Any:
    for alias in FIELD_ALIASES[field]:
        if alias in row and row[alias] not in (None, "", []):
            return row[alias]
    return None


def find_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in LIST_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def _weekday_basis(rows: list[dict[str, Any]]) -> int:
    values: set[int] = set()
    for row in rows:
        try:
            values.add(int(_pick(row, "weekday")))
        except (TypeError, ValueError):
            continue
    if 7 in values:
        return 1
    if 0 in values:
        return 0
    return 1


def _weekday(value: Any, basis: int = 1) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if basis == 1:
        return number - 1 if 1 <= number <= 7 else None
    return number if 0 <= number <= 6 else None


def _weeks_expr(value: Any) -> str | None:
    """把周次归一成 `1-16` / `1,3,5-16`；支持 JSON 字符串数组与列表。"""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                return _weeks_expr(json.loads(text))
            except Exception:
                return text or None
        return text or None
    if isinstance(value, (list, tuple)):
        numbers: list[int] = []
        for item in value:
            try:
                numbers.append(int(item))
            except (TypeError, ValueError):
                continue
        if not numbers:
            return None
        numbers = sorted(set(numbers))
        ranges: list[str] = []
        start = prev = numbers[0]
        for number in numbers[1:]:
            if number == prev + 1:
                prev = number
                continue
            ranges.append(f"{start}-{prev}" if prev > start else str(start))
            start = prev = number
        ranges.append(f"{start}-{prev}" if prev > start else str(start))
        return ",".join(ranges)
    return None


def _section_range(row: dict[str, Any]) -> tuple[int | None, int | None]:
    """把 courseOrder（大节）换算成小节范围；没有就把 1..20 的 section 原样用。"""
    order = _pick(row, "start_section")
    try:
        order_int = int(order) if order is not None else None
    except (TypeError, ValueError):
        order_int = None
    if order_int in _PERIOD_TO_SECTION:
        return _PERIOD_TO_SECTION[order_int]
    direct = _pick(row, "start_section")
    try:
        return (int(direct), None) if direct is not None else (None, None)
    except (TypeError, ValueError):
        return (None, None)


def map_courses(payload: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """返回（中间格式快照, 诊断报告）。"""
    rows = find_rows(payload)
    basis = _weekday_basis(rows) if rows else 1

    term_start = None
    if isinstance(payload, dict):
        for key in TERM_KEYS:
            if isinstance(payload.get(key), str) and len(payload[key]) == 10:
                term_start = payload[key]
                break

    courses: list[dict[str, Any]] = []
    unknown: set[str] = set()
    missing = 0
    for row in rows:
        name = _pick(row, "name")
        weekday = _weekday(_pick(row, "weekday"), basis)
        if not isinstance(name, str) or not name.strip() or weekday is None:
            missing += 1
            unknown.update(row.keys())
            continue

        start_section, end_section = _section_range(row)
        location = _pick(row, "location")
        if isinstance(location, str) and location.strip() in {"无", "none", "-"}:
            location = None
        entry: dict[str, Any] = {
            "name": name.strip(),
            "weekday": weekday,
            "start_section": start_section,
            "end_section": end_section,
            "weeks": _weeks_expr(_pick(row, "weeks")),
        }
        if location:
            entry["location"] = location
        for field in ("teacher", "start_time", "end_time"):
            value = _pick(row, field)
            if isinstance(value, str) and value.strip():
                entry[field] = value.strip()

        known = {alias for aliases in FIELD_ALIASES.values() for alias in aliases}
        unknown.update(key for key in row if key not in known)
        courses.append(entry)

    snapshot = {"term_start": term_start, "courses": courses}
    report = {
        "rows": len(rows),
        "mapped": len(courses),
        "missing_name_or_weekday": missing,
        "weekday_basis": f"{basis}-based（{'1=周一' if basis == 1 else '0=周一'}）",
        "unknown_keys": sorted(unknown)[:40],
    }
    return snapshot, report
