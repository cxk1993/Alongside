"""Q1 · query 模块后端断言（presets 注册表 + 形状 + 错误分支）。

★ 部分签收补件（2026-09-24）：
  后端子件缺独立断言 → 本文补（presets 五条齐全 / 形状 `_pack` 契约 /
  未知 qid 抛 KeyError / HTTP 列表端点 401 与 200 双态）。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from core.app import create_app
from modules.query.presets import PRESETS, run_preset

_EXPECTED_IDS = {
    "q_open_overdue",
    "q_week_health_open",
    "q_habit_streak_break",
    "q_free_slots_today",
    "q_finance_week_sum",
}


def test_presets_registry_has_five_expected():
    """Q1 五条预置查询全部注册。"""
    assert set(PRESETS) == _EXPECTED_IDS


def test_pack_shape_contract():
    """每条预置查询返回 `_pack` 形状：query/rows/row_count/empty/partial/errors。

    ★ 2026-11-22（汐瑶）：原来这里断言 `partial is False` + `errors == []`，
      但输入是 `object()`（假 session）——内部必然抛异常。
      那条断言等于钉死「崩溃一律装成『查完了，就是空的』」，正是
      `_调研/free-slots-口径矛盾-调查.md` 里那个「今天排满了」的真因。
      现在改成：**不许抛，但必须如实报 partial=True + errors 非空**。
    """
    for qid in sorted(PRESETS):
        result = run_preset(object(), qid)  # 假 session：内部必抛，但必须被如实记录
        assert set(result) == {
            "query",
            "rows",
            "row_count",
            "empty",
            "partial",
            "errors",
        }, qid
        assert result["query"]["id"] == qid
        assert result["row_count"] == len(result["rows"])
        assert result["empty"] == (not result["rows"])
        assert result["partial"] is True, f"{qid} 内部异常未标记 partial"
        assert result["errors"], f"{qid} 内部异常未记录，不许静默吞掉"


def test_pack_empty_text_present():
    """空态文案存在（前端兜底展示用）。"""
    for qid in sorted(PRESETS):
        result = run_preset(object(), qid)
        assert result["query"]["empty_text"], qid


def test_run_preset_unknown_qid_raises_keyerror():
    """未知 qid 必须抛 KeyError（不为静默空结果）。"""
    import pytest

    with pytest.raises(KeyError):
        run_preset(object(), "q_no_such_preset")


def test_http_presets_list_public():
    """列表端点公开可读（无鉴权 200）；执行端点才需鉴权。"""
    client = TestClient(create_app())
    assert client.get("/api/v1/query/presets").status_code == 200


def test_http_run_preset_requires_auth():
    """执行端点无鉴权 → 401（run 带 get_current_user）。"""
    client = TestClient(create_app())
    assert client.get("/api/v1/query/presets/q_open_overdue").status_code == 401


def test_http_presets_list_returns_ids():
    """列表端点（带鉴权）返回五条 id+title。"""
    from core.security import create_access_token

    client = TestClient(create_app())
    auth = {"Authorization": f"Bearer {create_access_token('admin')}"}
    r = client.get("/api/v1/query/presets", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert {item["id"] for item in body["items"]} == _EXPECTED_IDS