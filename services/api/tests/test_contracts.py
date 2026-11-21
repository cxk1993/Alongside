"""契约门禁：用**正例 + 反例**校验 contracts/*.schema.json 真的能拦住人。

为什么要有这个文件：
    一份 schema「看起来对」和「真的在拦」是两件事。
    2026-11-22 夜班实测发现过一个真陷阱：
        `required: ["executed_at"]` 只检查**键在不在**，不检查**值是不是 null** ——
        于是 `{"status":"approved","executed_at":null}` 一路畅通，等于没拦。
    这类坑只有「反例必须被拒」的断言才抓得到。

规则（与 tests/test_sight.py 同一条）：
    不许用 @pytest.mark.asyncio —— 项目未装 pytest-asyncio，异步测试会被静默 skip（假绿）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"
jsonschema = pytest.importorskip("jsonschema")

TS = "2026-11-22T02:00:00+08:00"


def _load(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def _rejects(name: str, inst: dict) -> bool:
    """返回 True 表示该实例被拒（即 schema 起作用了）。"""
    v = jsonschema.Draft7Validator(_load(name))
    return bool(list(v.iter_errors(inst)))


def _accepts(name: str, inst: dict) -> bool:
    return not _rejects(name, inst)


# ----------------------------- 全部契约：必须是合法 JSON Schema -----------------------------

@pytest.mark.parametrize("fname", [
    "sight.schema.json", "asset.schema.json", "action.schema.json",
    "widget.schema.json", "proposal.schema.json",
])
def test_schema_itself_is_valid(fname: str):
    """每份契约都要通过 Draft-07 meta-schema 校验。"""
    jsonschema.Draft7Validator.check_schema(_load(fname))


# ----------------------------- proposal：V1 边界与状态机 -----------------------------

def _proposal(**over) -> dict:
    base = {
        "id": "p_0001", "title": "删掉那条待办", "tool_name": "todo_item_delete",
        "action": "delete", "method": "DELETE", "path": "/api/v1/todo/items/t1",
        "payload": {"id": "t1"}, "before": {"id": "t1", "text": "交计划书"},
        "actor": "mcp:hermes", "status": "pending", "created_at": TS,
        "executed_at": None, "executed_id": None, "executed_status": None,
        "undone_at": None, "undo_result": None,
    }
    base.update(over)
    return base


def test_proposal_pending_ok():
    assert _accepts("proposal.schema.json", _proposal())


def test_proposal_approved_requires_real_executed_at():
    """★ 关键：approved 但 executed_at=null 必须被拒（required 不管 null 值）。"""
    assert _rejects("proposal.schema.json", _proposal(status="approved"))


def test_proposal_undone_requires_real_undone_at():
    assert _rejects("proposal.schema.json", _proposal(status="undone", executed_at=TS))


def test_proposal_create_must_not_carry_before():
    """★ V1 边界：create 的 before 必须为 null —— 带了说明快照逻辑错了。"""
    assert _rejects("proposal.schema.json", _proposal(action="create", before={"id": "x"}))


def test_proposal_op_result_needs_detail():
    """approve/undo 的响应必须带人话 detail —— 前端要显示给用户。"""
    assert _rejects("proposal.schema.json", {"id": "p1", "status": "approved"})
    assert _accepts("proposal.schema.json", {"id": "p1", "status": "approved", "detail": "已执行"})


# ----------------------------- 其它契约：守住各自的「诚实字段」 -----------------------------

def test_sight_requires_tensions():
    """sight 没有 tensons 就不是处境，是数据表。"""
    inst = {
        "schema_version": 1, "generated_at": TS,
        "head": {"now_local": TS, "weekday": "sun", "part_of_day": "late_night"},
        "attention": [],
    }
    assert _rejects("sight.schema.json", inst)


def test_action_reversible_needs_undo_ref():
    """「能撤」必须有 undo_ref，否则是空话。"""
    inst = {"schema_version": 1, "id": "ac_test0001", "at": TS,
            "actor": {"kind": "agent"}, "verb": "delete", "status": "applied",
            "reversible": True}
    assert _rejects("action.schema.json", inst)


def test_asset_superseded_needs_backpointer():
    """记忆的逆：被取代必须指向取代者，否则不知道该信哪条。"""
    inst = {"schema_version": 1, "id": "as_test0001", "type": "decision", "title": "x",
            "body": "y", "provenance": {"agent": "human", "at": TS},
            "status": "superseded", "tier": "private", "created_at": TS}
    assert _rejects("asset.schema.json", inst)


def test_widget_columns_fixed_for_v1():
    """自由度过早就是混乱：v1 栅格只允许 12。"""
    assert _rejects("widget.schema.json",
                    {"schema_version": 1, "version": 1, "columns": 8, "widgets": []})
