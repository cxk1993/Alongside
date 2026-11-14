"""isdu 课表映射/差量的纯函数测试（不需要 FastAPI / SQLModel / 数据库）。

覆盖两件最容易出错的事：
  1. 上游字段名不认识 —— `map_courses` 的别名与"未知键上报"；
  2. 重复同步造重复课 —— `normalize_courses` + `plan_push` 的幂等。

跑法（裸 Python 也行）：
    python services/api/tests/test_isdu_timetable.py
    python -m pytest services/api/tests/test_isdu_timetable.py -q
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_MODULE_DIR = Path(__file__).resolve().parents[1] / "modules" / "isdu"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _MODULE_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


map_courses = _load("isdu_map_courses", "map_courses.py")
timetable = _load("isdu_timetable", "timetable.py")


def _raw_row(**overrides):
    row = {
        "courseName": "高等数学",
        "teacherName": "张三",
        "classroom": "理综楼 305",
        "weekday": 1,
        "startSection": 1,
        "endSection": 2,
        "weeks": [1, 2, 3, 4],
    }
    row.update(overrides)
    return row


def test_map_aliases_and_weekday_basis():
    payload = {"termStart": "2026-09-07", "data": [_raw_row()]}
    snapshot, report = map_courses.map_courses(payload)
    assert snapshot["term_start"] == "2026-09-07"
    row = snapshot["courses"][0]
    assert row["name"] == "高等数学"
    assert row["weekday"] == 0  # 1=周一 → 0
    assert row["weeks"] == "1-4"
    assert row["location"] == "理综楼 305"
    assert report["mapped"] == 1


def test_zero_based_detected_by_presence_of_zero():
    rows = [_raw_row(weekday=2), _raw_row(courseName="体育", weekday=0)]
    snapshot, report = map_courses.map_courses({"courses": rows})
    assert snapshot["courses"][0]["weekday"] == 2
    assert "0-based" in report["weekday_basis"]


def test_unknown_keys_reported():
    snapshot, report = map_courses.map_courses(
        {"list": [_raw_row(credits=3, mystery="x")]}
    )
    assert len(snapshot["courses"]) == 1
    assert "credits" in report["unknown_keys"]
    assert "mystery" in report["unknown_keys"]


def test_rows_without_name_are_counted_not_silently_dropped():
    snapshot, report = map_courses.map_courses(
        {"items": [{"teacher": "李四"}, _raw_row(courseName="化学", startSection=5)]}
    )
    assert len(snapshot["courses"]) == 1
    assert report["missing_name_or_weekday"] == 1


def test_real_isdu_course_row_shape():
    """真实响应形状（字段已脱敏）：大节→小节、周次 JSON 串、地点“无”。"""
    payload = [
        {
            "courseIndex": "3",
            "courseTeacher": "示例教师",
            "courseWeeks": "[1,2,3,4]",
            "courseName": "示例课程",
            "courseWeekday": 2,
            "courseLocation": "示例楼A101",
            "courseOrder": 1,
            "startTime": "08:00",
            "id": 1,
            "credit": 3.0,
            "courseId": "sd00000000",
            "addition": None,
            "userCourse": False,
        },
        {
            "courseName": "示例课程二",
            "courseTeacher": "示例教师二",
            "courseWeeks": "[1,2,3,4,5]",
            "courseWeekday": 1,
            "courseLocation": "无",
            "courseOrder": 2,
            "startTime": "10:10",
        },
    ]
    snapshot, report = map_courses.map_courses(payload)
    assert report["mapped"] == 2
    first = snapshot["courses"][0]
    assert first["name"] == "示例课程"
    assert first["weekday"] == 1  # 2=周二 → 1
    assert first["start_section"] == 1 and first["end_section"] == 2  # 第 1 大节
    assert first["weeks"] == "1-4"
    assert first["teacher"] == "示例教师"
    assert first["location"].endswith("A101")
    assert first["start_time"] == "08:00"

    second = snapshot["courses"][1]
    assert second["start_section"] == 3 and second["end_section"] == 4
    assert "location" not in second  # “无”不当地点


def test_repeated_import_creates_no_duplicates():
    snapshot, _ = map_courses.map_courses({"courses": [_raw_row()]})
    desired = timetable.normalize_courses(snapshot)

    first = timetable.plan_push(desired, [])
    assert len(first["create"]) == 1 and not first["update"]

    existing = [{"id": "c0", **desired[0]}]
    second = timetable.plan_push(desired, existing)
    assert not second["create"] and not second["update"] and len(second["skip"]) == 1

    existing[0]["location"] = "旧地点"
    third = timetable.plan_push(desired, existing)
    assert not third["create"] and len(third["update"]) == 1
    assert third["update"][0]["_changed"]["location"] == "理综楼 305"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    raise SystemExit(1 if failures else 0)
