"""T07 笔记后端测试（只跑这一个文件，不影响别的卡）。

数据库隔离：用临时库 ./data/tmp_t07.db，绝不碰主库 alongside.db。
桥客户端全部 mock——本机没有真实 vault，不伪造「已接入」。
"""
from __future__ import annotations

import os

# ★ 必须在 import 任何内核/模块之前设置临时库，init_engine 只认一次。
os.environ["DB_PATH"] = "./data/tmp_t07.db"

from typing import Any  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from core.app import create_app  # noqa: E402
from core.security import create_access_token  # noqa: E402
from db.engine import get_engine, init_engine  # noqa: E402
from modules.notes.bridge_client import BridgeClient, BridgeError, _sign  # noqa: E402
from modules.notes.models import NoteIndex, NoteLib  # noqa: E402


class FakeBridge(BridgeClient):
    """测试用桥：内存里放索引与全文，不发真 HTTP。"""

    def __init__(self) -> None:
        super().__init__(base_url="http://fake", psk="test-psk")
        self.scan_data: list[dict[str, Any]] = []
        self.read_data: dict[str, dict[str, Any]] = {}
        self.fail_scan = False

    def scan(self, lib: str, limit: int = 5000) -> list[dict[str, Any]]:
        if self.fail_scan:
            raise BridgeError("桥不可达")
        return list(self.scan_data)

    def read(self, lib: str, path: str) -> dict[str, Any]:
        if path not in self.read_data:
            raise BridgeError("文件不存在", status=404)
        return self.read_data[path]


@pytest.fixture(scope="module")
def client():
    init_engine()
    engine = get_engine()
    NoteLib.__table__.create(bind=engine, checkfirst=True)
    NoteIndex.__table__.create(bind=engine, checkfirst=True)
    app = create_app()
    c = TestClient(app)
    yield c
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean():
    engine = get_engine()
    with __import__("sqlmodel").Session(engine) as s:
        s.exec(text("DELETE FROM note_index"))
        s.exec(text("DELETE FROM note_lib"))
        s.commit()
    yield


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {create_access_token('admin')}"}


def _seed_lib(client, auth, key="main", name="主仓库") -> dict:
    r = client.post(
        "/api/v1/notes/libs", json={"key": key, "name": name}, headers=auth
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


# ───────────────────────── 基础 ─────────────────────────
def test_health(client):
    r = client.get("/api/v1/notes/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_manifest(client):
    r = client.get("/api/v1/notes/manifest")
    assert r.status_code == 200
    m = r.json()
    assert m["id"] == "notes"
    assert "bridge:read" in m["permissions"]


# ───────────────────────── 库登记 ─────────────────────────
def test_lib_upsert_idempotent(client, auth):
    a = _seed_lib(client, auth, key="main", name="主仓库")
    b = _seed_lib(client, auth, key="main", name="主仓库改名")
    assert a["id"] == b["id"]
    assert b["name"] == "主仓库改名"

    r = client.get("/api/v1/notes/libs", headers=auth)
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_lib_empty_key_rejected(client, auth):
    r = client.post("/api/v1/notes/libs", json={"key": "  "}, headers=auth)
    assert r.status_code == 422


# ───────────────────────── 同步（mock 桥） ─────────────────────────
def test_sync_upserts_index(client, auth, monkeypatch):
    lib = _seed_lib(client, auth)
    fake = FakeBridge()
    fake.scan_data = [
        {
            "rel_path": "a/one.md",
            "title": "第一篇",
            "mtime": 1720000000,
            "size": 120,
            "hash": "abc",
            "excerpt": "这是摘要",
        },
        {
            "rel_path": "b/two.md",
            "title": "第二篇",
            "mtime": 1720001000,
            "size": 80,
            "hash": "def",
            "excerpt": "另一篇",
        },
    ]
    monkeypatch.setattr(
        "modules.notes.service.NotesService.bridge", property(lambda self: fake)
    )

    r = client.post(f"/api/v1/notes/libs/{lib['id']}/sync", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["upserted"] == 2
    assert body["removed"] == 0
    assert body["md_count"] == 2

    s = client.get("/api/v1/notes/search", headers=auth)
    items = s.json()["items"]
    assert len(items) == 2
    titles = {i["title"] for i in items}
    assert titles == {"第一篇", "第二篇"}


def test_sync_removes_missing(client, auth, monkeypatch):
    lib = _seed_lib(client, auth)
    fake = FakeBridge()
    fake.scan_data = [
        {"rel_path": "keep.md", "title": "留下", "mtime": 1, "size": 1, "hash": "h"}
    ]
    # 先塞一条同步后会消失的
    engine = get_engine()
    from sqlmodel import Session

    with Session(engine) as s:
        s.add(NoteIndex(lib_id=lib["id"], rel_path="gone.md", title="会消失"))
        s.commit()

    monkeypatch.setattr(
        "modules.notes.service.NotesService.bridge", property(lambda self: fake)
    )
    r = client.post(f"/api/v1/notes/libs/{lib['id']}/sync", headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["removed"] == 1
    titles = {i["title"] for i in client.get("/api/v1/notes/search", headers=auth).json()["items"]}
    assert "会消失" not in titles
    assert "留下" in titles


def test_sync_disabled_lib_rejected(client, auth):
    lib = _seed_lib(client, auth, key="off", name="禁用库")
    client.post(
        "/api/v1/notes/libs",
        json={"key": "off", "name": "禁用库", "enabled": False},
        headers=auth,
    )
    r = client.post(f"/api/v1/notes/libs/{lib['id']}/sync", headers=auth)
    assert r.status_code in (400, 422), r.text


# ───────────────────────── 搜索 / 全文 ─────────────────────────
def _seed_index(client, auth) -> tuple[dict, dict]:
    """直接写库，不依赖桥。返回 (lib, first_note_brief)。"""
    lib = _seed_lib(client, auth, key="main")
    engine = get_engine()
    from sqlmodel import Session

    with Session(engine) as s:
        n1 = NoteIndex(
            lib_id=lib["id"],
            rel_path="weekly/2026-09-13.md",
            title="周报 2026-09-13",
            excerpt="写了进度和下周计划",
            mtime=1720000000,
            size=200,
            content_hash="h1",
        )
        n2 = NoteIndex(
            lib_id=lib["id"],
            rel_path="inbox/idea.md",
            title="一个想法",
            excerpt="关于人生管理系统",
            mtime=1720001000,
            size=50,
            content_hash="h2",
        )
        s.add(n1)
        s.add(n2)
        s.commit()
        s.refresh(n1)
        s.refresh(n2)
        return lib, {
            "id": n1.id,
            "rel_path": n1.rel_path,
            "title": n1.title,
        }


def test_search_by_title(client, auth):
    _seed_index(client, auth)
    r = client.get("/api/v1/notes/search?q=周报", headers=auth)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["title"] == "周报 2026-09-13"


def test_search_by_excerpt(client, auth):
    _seed_index(client, auth)
    r = client.get("/api/v1/notes/search?q=人生管理", headers=auth)
    assert len(r.json()["items"]) == 1
    assert r.json()["items"][0]["title"] == "一个想法"


def test_search_empty_q_returns_all(client, auth):
    _seed_index(client, auth)
    r = client.get("/api/v1/notes/search", headers=auth)
    assert r.json()["total"] == 2


def test_search_o2_title_scores_higher(client, auth):
    """O2：标题命中分高于仅摘要命中。"""
    _seed_index(client, auth)
    r = client.get("/api/v1/notes/search?q=周报", headers=auth)
    items = r.json()["items"]
    assert items[0]["score"] >= 3.0


def test_search_o2_highlight_marks(client, auth):
    """O2：高亮摘录带 [[词]] 标记。"""
    _seed_index(client, auth)
    r = client.get("/api/v1/notes/search?q=人生管理", headers=auth)
    items = r.json()["items"]
    assert items[0]["highlight"]
    assert "[[人生管理]]" in items[0]["highlight"]


def test_search_o2_multi_term_and(client, auth):
    """O2：多词 AND——缺词条目不进结果。"""
    _seed_index(client, auth)
    r = client.get("/api/v1/notes/search?q=周报 人生管理", headers=auth)
    assert r.json()["total"] == 0  # 两词分属不同笔记


def test_get_note_with_content_from_bridge(client, auth, monkeypatch):
    _, note = _seed_index(client, auth)
    fake = FakeBridge()
    fake.read_data[note["rel_path"]] = {
        "content": "# 周报\n\n- 完成了 T05",
        "mtime": 1,
        "hash": "h1",
    }
    monkeypatch.setattr(
        "modules.notes.service.NotesService.bridge", property(lambda self: fake)
    )
    r = client.get(f"/api/v1/notes/notes/{note['id']}", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "周报 2026-09-13"
    assert "T05" in body["content"]


def test_get_note_bridge_down_returns_index_only(client, auth, monkeypatch):
    _, note = _seed_index(client, auth)
    fake = FakeBridge()
    fake.read_data = {}  # 全部 404 / BridgeError
    monkeypatch.setattr(
        "modules.notes.service.NotesService.bridge", property(lambda self: fake)
    )
    r = client.get(f"/api/v1/notes/notes/{note['id']}", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "周报 2026-09-13"
    assert body["content"] == ""


def test_get_note_not_found(client, auth):
    r = client.get("/api/v1/notes/notes/no-such-id", headers=auth)
    assert r.status_code == 404


# ───────────────────────── 桥签名协议一致性 ─────────────────────────
def test_bridge_sign_matches_protocol():
    """云侧签名必须能被 bridge.protocol.verify_sig 验过。"""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # services/
    from bridge.protocol import verify_sig

    psk = "unit-test-psk"
    sig = _sign(psk, "GET", "/bridge/healthz", 1720000000, "nonce-abc")
    assert verify_sig(psk, "GET", "/bridge/healthz", 1720000000, "nonce-abc", sig)
    assert not verify_sig(psk, "GET", "/bridge/healthz", 1720000000, "nonce-abc", "bad")


# ───────────────────────── 边界 ─────────────────────────
def test_boundary_no_auth(client):
    r = client.get("/api/v1/notes/search")
    assert r.status_code in (401, 403)


def test_boundary_sync_missing_lib(client, auth):
    r = client.post("/api/v1/notes/libs/does-not-exist/sync", headers=auth)
    assert r.status_code == 404
