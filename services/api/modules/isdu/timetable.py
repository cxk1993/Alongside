"""课表快照 → `course` 插件入参（纯函数，可脱库测试）。

★ 为什么把"归一化 + 差量"单独拆出来：
  它决定了**重复同步会不会造重复课**。真正的接入口（iSDU / 教务网页 / 手动导入）
  以后还会换，但这段逻辑不该跟着换 —— 所以它不 import 任何框架，能被裸 Python 测。

输入是**一个声明的中间格式**（iSDU 课表经 `map_courses` 映射后的结果），
输出是 course 的 CourseCreate 形状。
"""

from __future__ import annotations

import re
from typing import Any

MAX_COURSES = 200
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_WEEKS_RE = re.compile(r"^[0-9,\-单双周]+$")


class NormalizeError(ValueError):
    """快照格式不对（缺字段 / 越界）——调用方应当 400，而不是静默丢弃。"""


def _text(raw: dict[str, Any], key: str, *, max_len: int, default: str = "") -> str:
    value = raw.get(key, default)
    if value is None:
        return default
    text = str(value).strip()
    if len(text) > max_len:
        raise NormalizeError(f"{key} 超长（>{max_len}）：{text[:20]}…")
    return text


def _section(raw: dict[str, Any], key: str) -> int | None:
    value = raw.get(key)
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise NormalizeError(f"{key} 必须是整数：{value!r}") from exc
    if not 1 <= number <= 20:
        raise NormalizeError(f"{key} 越界（1..20）：{number}")
    return number


def _time(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None or value == "":
        return None
    text = str(value).strip()
    if not _TIME_RE.match(text):
        raise NormalizeError(f"{key} 必须是 HH:MM：{text!r}")
    return text


def normalize_courses(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """把中间格式校验并归一成 course 的入参列表（按"同名+星期+节次"去重）。"""
    if not isinstance(payload, dict):
        raise NormalizeError("快照根必须是对象")
    courses = payload.get("courses")
    if not isinstance(courses, list) or not courses:
        raise NormalizeError("courses 必须是非空数组")
    if len(courses) > MAX_COURSES:
        raise NormalizeError(f"一次最多 {MAX_COURSES} 条（收到 {len(courses)}）")

    term_start = _text(payload, "term_start", max_len=10) or None
    if term_start is not None and not re.match(r"^\d{4}-\d{2}-\d{2}$", term_start):
        raise NormalizeError(f"term_start 必须是 YYYY-MM-DD：{term_start!r}")

    out: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for index, raw in enumerate(courses):
        if not isinstance(raw, dict):
            raise NormalizeError(f"courses[{index}] 必须是对象")
        name = _text(raw, "name", max_len=100)
        if not name:
            raise NormalizeError(f"courses[{index}].name 不能为空")

        weekday = raw.get("weekday")
        try:
            weekday_int = int(weekday)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise NormalizeError(f"courses[{index}].weekday 必须是 0..6：{weekday!r}") from exc
        if not 0 <= weekday_int <= 6:
            raise NormalizeError(f"courses[{index}].weekday 越界（0=周一）：{weekday_int}")

        start_section = _section(raw, "start_section")
        end_section = _section(raw, "end_section")
        if start_section and end_section and start_section > end_section:
            raise NormalizeError(f"courses[{index}] 起始节次大于结束节次")

        start_time = _time(raw, "start_time")
        end_time = _time(raw, "end_time")
        if not (start_section or start_time):
            raise NormalizeError(f"courses[{index}] 至少要有节次或开始时间")

        weeks = _text(raw, "weeks", max_len=200)
        if weeks and not _WEEKS_RE.match(weeks):
            raise NormalizeError(f"courses[{index}].weeks 含非法字符：{weeks!r}")

        key = (name, weekday_int, start_section or 0, end_section or 0, weeks)
        if key in seen:
            continue
        seen.add(key)

        out.append(
            {
                "name": name,
                "teacher": _text(raw, "teacher", max_len=50) or None,
                "location": _text(raw, "location", max_len=100) or None,
                "weekday": weekday_int,
                "start_section": start_section,
                "end_section": end_section,
                "start_time": start_time,
                "end_time": end_time,
                "weeks": weeks or None,
                "term_start": term_start,
                "enabled": True,
            }
        )
    return out


def _key(item: dict[str, Any]) -> tuple[Any, ...]:
    # ★ 周次必须进 key：同一门课同一节次可能分两段周次（如 1-8 / 9-16），
    #   只用「名字+星期+节次」会把后一段吞掉（2026-11-14 实测：26 行只剩 20 条）。
    return (item.get("name"), item.get("weekday"), item.get("start_section") or 0,
            item.get("end_section") or 0, item.get("weeks") or "")


#: 这几个字段变了才值得 PATCH；其余字段（id / created_at / enabled / sort）不参与比较
_COMPARE_FIELDS = ("teacher", "location", "start_time", "end_time", "term_start")


def plan_push(
    desired: list[dict[str, Any]], existing: list[dict[str, Any]]
) -> dict[str, Any]:
    """算差量：create / update / skip。**重复同步不会造重复课**靠的就是这里。"""
    index: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in existing:
        index.setdefault(_key(item), item)

    create: list[dict[str, Any]] = []
    update: list[dict[str, Any]] = []
    skip: list[dict[str, Any]] = []

    for want in desired:
        found = index.get(_key(want))
        if found is None:
            create.append(want)
            continue
        changed = {
            field: want[field]
            for field in _COMPARE_FIELDS
            if want.get(field) not in (None, "") and found.get(field) != want.get(field)
        }
        if changed:
            update.append({"id": found.get("id"), **want, "_changed": changed})
        else:
            skip.append({"id": found.get("id"), "name": want["name"]})

    return {"create": create, "update": update, "skip": skip}
