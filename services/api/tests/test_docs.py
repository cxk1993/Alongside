"""T15 文档树内核后端测试（只跑这一个文件，不影响别的卡）。

数据库隔离：用临时库 ./data/tmp_t15_<随机>.db（★ 2026-09-20 Qoder 建议：
固定名 tmp_t15.db 会被并发 pytest 进程互踩，改唯一名），绝不碰主库 alongside.db。
表只在当前进程内由模型直接建（create_app 不自动跑迁移）。
"""
from __future__ import annotations

import atexit
import os
import uuid
from contextlib import suppress

# ★ 必须在 import 任何内核/模块之前设置临时库，init_engine 只认一次。
_TMP_DB = f"./data/tmp_t15_{uuid.uuid4().hex[:8]}.db"
os.environ["DB_PATH"] = _TMP_DB


def _cleanup_tmp_db() -> None:
    with suppress(FileNotFoundError, PermissionError):
        # Windows 上 SQLite 引擎句柄可能未释放，删不掉就算了（名字唯一不互踩即可）
        os.remove(_TMP_DB)


atexit.register(_cleanup_tmp_db)

from datetime import timedelta, timezone  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, text  # noqa: E402

from core.app import create_app  # noqa: E402
from core.security import create_access_token  # noqa: E402
from db.engine import get_engine, init_engine  # noqa: E402
from modules.docs.models import DocsContent, DocsNode, DocsRevision  # noqa: E402

TZ = timezone(timedelta(hours=8))  # 主人本地时区 Asia/Shanghai


@pytest.fixture(scope="module")
def client():
    init_engine()  # 用 DB_PATH 指向的临时库
    engine = get_engine()
    # 只建本插件三张表 + FTS5 虚拟表（与迁移一致）
    DocsNode.__table__.create(bind=engine, checkfirst=True)
    DocsContent.__table__.create(bind=engine, checkfirst=True)
    DocsRevision.__table__.create(bind=engine, checkfirst=True)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5("
            "node_id UNINDEXED, title, body, tokenize = 'trigram')"
        )
    app = create_app()
    c = TestClient(app)
    yield c
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean():
    """每条测试前清空本插件表，测试之间不得相互依赖。"""
    engine = get_engine()
    with Session(engine) as s:
        s.exec(text("DELETE FROM docs_fts"))
        s.exec(text("DELETE FROM docs_revision"))
        s.exec(text("DELETE FROM docs_content"))
        s.exec(text("DELETE FROM docs_node"))
        s.commit()
    yield


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {create_access_token('admin')}"}


def _create(client, auth, **kw):
    r = client.post("/api/v1/docs/nodes", json=kw, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


def _save(client, auth, node_id, body="hello", format="md"):
    r = client.put(
        f"/api/v1/docs/nodes/{node_id}/content",
        json={"body": body, "format": format},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    return r.json()


# ───────────────────────── 基础 ─────────────────────────
def test_health(client):
    r = client.get("/api/v1/docs/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_manifest(client):
    r = client.get("/api/v1/docs/manifest")
    assert r.status_code == 200
    m = r.json()
    assert m["id"] == "docs"
    assert "docs.node.read" in m["provides"]
    assert "docs.node.write" in m["provides"]
    assert "docs.search" in m["provides"]
    assert "docs.content.write" in m["provides"]  # 2026-09-27 按路径写正文
    assert m["api"]["tools"]["content"] == "/content"
    # 2026-09-27：路径参数型端点（改名/移动、软删）
    assert "docs.node.patch" in m["provides"]
    assert "docs.node.delete" in m["provides"]
    assert m["api"]["tools"]["node.patch"] == "/nodes/{node_id}"
    assert "docs.node.created" in m["emits"]


# ───────────────────────── 树查询（一次查，禁 N+1） ─────────────────────────
def test_tree_forest(client, auth):
    """默认整片森林：只返回根节点。"""
    a = _create(client, auth, kind="folder", name="人格体系")
    b = _create(client, auth, kind="folder", name="日记")
    r = client.get("/api/v1/docs/nodes", headers=auth)
    assert r.status_code == 200
    nodes = r.json()
    ids = {n["id"] for n in nodes}
    assert {a["id"], b["id"]} <= ids


def test_tree_subtree(client, auth):
    """以 root 为顶点的完整子树，一次装配。"""
    root = _create(client, auth, kind="folder", name="日记")
    y2026 = _create(client, auth, kind="folder", name="2026", parent_id=root["id"])
    doc = _create(client, auth, kind="doc", name="2026-09-19", parent_id=y2026["id"])
    r = client.get(f"/api/v1/docs/nodes?root={root['id']}", headers=auth)
    assert r.status_code == 200
    tree = r.json()
    assert len(tree) == 1
    assert tree[0]["id"] == root["id"]
    assert len(tree[0]["children"]) == 1
    assert tree[0]["children"][0]["id"] == y2026["id"]
    assert tree[0]["children"][0]["children"][0]["id"] == doc["id"]


def test_tree_ordering(client, auth):
    """同层排序：sort → name → created_at。"""
    root = _create(client, auth, kind="folder", name="根")
    _create(client, auth, kind="doc", name="b", parent_id=root["id"], sort=1)
    _create(client, auth, kind="doc", name="a", parent_id=root["id"], sort=0)
    _create(client, auth, kind="doc", name="c", parent_id=root["id"], sort=0)
    r = client.get(f"/api/v1/docs/nodes?root={root['id']}", headers=auth)
    names = [n["name"] for n in r.json()[0]["children"]]
    assert names == ["a", "c", "b"]  # sort 优先，再按 name


# ───────────────────────── 节点 CRUD ─────────────────────────
def test_create_doc(client, auth):
    doc = _create(client, auth, kind="doc", name="第一篇")
    assert doc["kind"] == "doc"
    assert doc["name"] == "第一篇"
    assert doc["parent_id"] is None


def test_create_invalid_kind(client, auth):
    r = client.post("/api/v1/docs/nodes", json={"kind": "nope", "name": "x"}, headers=auth)
    assert r.status_code == 422


def test_create_requires_name(client, auth):
    r = client.post("/api/v1/docs/nodes", json={"kind": "doc", "name": "  "}, headers=auth)
    assert r.status_code == 422


def test_get_node_with_content(client, auth):
    doc = _create(client, auth, kind="doc", name="带正文")
    _save(client, auth, doc["id"], body="# 标题\n正文内容", format="md")
    r = client.get(f"/api/v1/docs/nodes/{doc['id']}", headers=auth)
    assert r.status_code == 200
    data = r.json()
    assert data["body"] == "# 标题\n正文内容"
    assert data["format"] == "md"


def test_rename_and_meta(client, auth):
    doc = _create(client, auth, kind="doc", name="原名")
    r = client.patch(
        f"/api/v1/docs/nodes/{doc['id']}",
        json={"name": "新名", "meta_json": {"tags": ["重要"], "icon": "🌟"}},
        headers=auth,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "新名"
    assert data["meta_json"]["tags"] == ["重要"]
    assert data["meta_json"]["icon"] == "🌟"


# ───────────────────────── 移动与防环 ─────────────────────────
def test_move_node(client, auth):
    root = _create(client, auth, kind="folder", name="根")
    child = _create(client, auth, kind="doc", name="孩子", parent_id=root["id"])
    new_root = _create(client, auth, kind="folder", name="新根")
    r = client.patch(
        f"/api/v1/docs/nodes/{child['id']}", json={"parent_id": new_root["id"]}, headers=auth
    )
    assert r.status_code == 200
    assert r.json()["parent_id"] == new_root["id"]


def test_move_into_self_rejected(client, auth):
    """把节点移进它自己 → 拒绝（防环）。"""
    doc = _create(client, auth, kind="doc", name="自己")
    r = client.patch(f"/api/v1/docs/nodes/{doc['id']}", json={"parent_id": doc["id"]}, headers=auth)
    assert r.status_code == 422


def test_move_into_own_descendant_rejected(client, auth):
    """把节点移进自己的子孙 → 拒绝（防环）。"""
    root = _create(client, auth, kind="folder", name="根")
    child = _create(client, auth, kind="folder", name="子", parent_id=root["id"])
    grandchild = _create(client, auth, kind="doc", name="孙", parent_id=child["id"])
    # 把 root 移进 grandchild（自己的子孙）→ 拒
    r = client.patch(
        f"/api/v1/docs/nodes/{root['id']}", json={"parent_id": grandchild["id"]}, headers=auth
    )
    assert r.status_code == 422
    # 把 child 移进 grandchild → 拒
    r = client.patch(
        f"/api/v1/docs/nodes/{child['id']}", json={"parent_id": grandchild["id"]}, headers=auth
    )
    assert r.status_code == 422


def test_move_into_doc_rejected(client, auth):
    """父节点必须是 folder，不能挂到 doc 下。"""
    root = _create(client, auth, kind="folder", name="根")
    doc = _create(client, auth, kind="doc", name="文档", parent_id=root["id"])
    r = client.patch(
        f"/api/v1/docs/nodes/{root['id']}", json={"parent_id": doc["id"]}, headers=auth
    )
    assert r.status_code == 422


# ───────────────────────── 软删 / 回收站 / 彻底删除 ─────────────────────────
def test_soft_delete_and_restore(client, auth):
    doc = _create(client, auth, kind="doc", name="待删")
    r = client.delete(f"/api/v1/docs/nodes/{doc['id']}", headers=auth)
    assert r.status_code == 204
    # 回收站里有
    r = client.get("/api/v1/docs/trash", headers=auth)
    assert r.status_code == 200
    assert any(n["id"] == doc["id"] for n in r.json()["items"])
    # 恢复正常
    r = client.post(f"/api/v1/docs/trash/{doc['id']}/restore", headers=auth)
    assert r.status_code == 200
    assert r.json()["deleted_at"] is None
    # 树里回来了
    r = client.get("/api/v1/docs/nodes", headers=auth)
    assert any(n["id"] == doc["id"] for n in r.json())


def test_soft_deleted_not_in_tree(client, auth):
    root = _create(client, auth, kind="folder", name="根")
    doc = _create(client, auth, kind="doc", name="被删", parent_id=root["id"])
    client.delete(f"/api/v1/docs/nodes/{doc['id']}", headers=auth)
    r = client.get(f"/api/v1/docs/nodes?root={root['id']}", headers=auth)
    assert len(r.json()[0]["children"]) == 0


def test_purge_hard_delete(client, auth):
    root = _create(client, auth, kind="folder", name="根")
    child = _create(client, auth, kind="doc", name="子", parent_id=root["id"])
    _save(client, auth, child["id"], body="正文")
    client.delete(f"/api/v1/docs/nodes/{child['id']}", headers=auth)
    r = client.delete(f"/api/v1/docs/trash/{child['id']}", headers=auth)
    assert r.status_code == 204
    # 彻底没了
    r = client.get(f"/api/v1/docs/nodes/{child['id']}", headers=auth)
    assert r.status_code == 404
    # content / revision 也清了
    engine = get_engine()
    with Session(engine) as s:
        assert s.get(DocsContent, child["id"]) is None
        stmt = text("SELECT COUNT(*) FROM docs_revision WHERE node_id = :n")
        revs = s.exec(stmt.bindparams(n=child["id"])).one()
        assert revs[0] == 0


# ───────────────────────── 版本历史 ─────────────────────────
def test_version_snapshot_on_save(client, auth):
    doc = _create(client, auth, kind="doc", name="版本")
    _save(client, auth, doc["id"], body="v1")
    _save(client, auth, doc["id"], body="v2")
    _save(client, auth, doc["id"], body="v3")
    r = client.get(f"/api/v1/docs/nodes/{doc['id']}/revisions", headers=auth)
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 3
    assert items[0]["content"] == "v3"  # 新的在前


def test_restore_revision_keeps_history(client, auth):
    doc = _create(client, auth, kind="doc", name="版本回退")
    _save(client, auth, doc["id"], body="v1")
    _save(client, auth, doc["id"], body="v2")
    r = client.get(f"/api/v1/docs/nodes/{doc['id']}/revisions", headers=auth)
    items = r.json()["items"]
    rev_v1 = items[-1]  # v1 在最后
    # 回退到 v1
    r = client.post(
        f"/api/v1/docs/nodes/{doc['id']}/revisions/{rev_v1['id']}/restore", headers=auth
    )
    assert r.status_code == 200
    assert r.json()["body"] == "v1"
    # 历史不丢：现在有 3 条（v1, v2, 回退到 v1 的新版本）
    r = client.get(f"/api/v1/docs/nodes/{doc['id']}/revisions", headers=auth)
    assert len(r.json()["items"]) == 3


def test_restore_revision_idempotent(client, auth):
    """重复回退同一版本不出错、不重复造垃圾。"""
    doc = _create(client, auth, kind="doc", name="幂等")
    _save(client, auth, doc["id"], body="v1")
    _save(client, auth, doc["id"], body="v2")
    r = client.get(f"/api/v1/docs/nodes/{doc['id']}/revisions", headers=auth)
    rev_v1 = r.json()["items"][-1]
    client.post(f"/api/v1/docs/nodes/{doc['id']}/revisions/{rev_v1['id']}/restore", headers=auth)
    client.post(f"/api/v1/docs/nodes/{doc['id']}/revisions/{rev_v1['id']}/restore", headers=auth)
    r = client.get(f"/api/v1/docs/nodes/{doc['id']}/revisions", headers=auth)
    assert len(r.json()["items"]) == 4  # v1, v2, 回退v1, 回退v1


# ───────────────────────── 检索（FTS5，中文） ─────────────────────────
def test_search_chinese_substring(client, auth):
    root = _create(client, auth, kind="folder", name="根")
    doc = _create(client, auth, kind="doc", name="学习笔记", parent_id=root["id"])
    _save(client, auth, doc["id"], body="今天学习了量子力学和相对论")
    r = client.get("/api/v1/docs/search", params={"q": "量子力学"}, headers=auth)
    assert r.status_code == 200
    hits = r.json()["items"]
    assert any(h["id"] == doc["id"] for h in hits)


def test_search_matches_title(client, auth):
    doc = _create(client, auth, kind="doc", name="周报-2026-09-19")
    _save(client, auth, doc["id"], body="内容")
    r = client.get("/api/v1/docs/search", params={"q": "周报"}, headers=auth)
    assert any(h["id"] == doc["id"] for h in r.json()["items"])


def test_search_excludes_soft_deleted(client, auth):
    doc = _create(client, auth, kind="doc", name="被删的文档")
    _save(client, auth, doc["id"], body="敏感词XYZ")
    client.delete(f"/api/v1/docs/nodes/{doc['id']}", headers=auth)
    r = client.get("/api/v1/docs/search", params={"q": "敏感词"}, headers=auth)
    assert not any(h["id"] == doc["id"] for h in r.json()["items"])


def test_search_empty_query(client, auth):
    r = client.get("/api/v1/docs/search", params={"q": ""}, headers=auth)
    assert r.status_code == 422


def test_search_o2_title_weight_beats_body(client, auth):
    """O2：标题命中排在仅正文命中之前（短词与长词均适用）。"""
    a = _create(client, auth, kind="doc", name="无关文档")
    _save(client, auth, a["id"], body="这里提到一次旅行见闻")
    b = _create(client, auth, kind="doc", name="旅行计划")
    _save(client, auth, b["id"], body="普通内容")
    r = client.get("/api/v1/docs/search", params={"q": "旅行"}, headers=auth)
    ids = [h["id"] for h in r.json()["items"]]
    assert b["id"] in ids and a["id"] in ids
    assert ids.index(b["id"]) < ids.index(a["id"])


def test_search_o2_highlight_fields(client, auth):
    """O2：结果带 score / highlight，且高亮含 [[ ]] 标记。"""
    doc = _create(client, auth, kind="doc", name="高亮测试甲")
    _save(client, auth, doc["id"], body="正文里有独特短语乙丙丁")
    r = client.get("/api/v1/docs/search", params={"q": "独特短语"}, headers=auth)
    hit = next(h for h in r.json()["items"] if h["id"] == doc["id"])
    assert "score" in hit
    blob = (hit.get("highlight") or "") + (hit.get("title_highlight") or "")
    assert "[[" in blob


# ───────────────────────── 大文档（10 万字） ─────────────────────────
def test_large_document(client, auth):
    doc = _create(client, auth, kind="doc", name="十万字")
    big = "字" * 100_000
    r = client.put(
        f"/api/v1/docs/nodes/{doc['id']}/content", json={"body": big, "format": "txt"}, headers=auth
    )
    assert r.status_code == 200
    assert len(r.json()["body"]) == 100_000
    r = client.get(f"/api/v1/docs/nodes/{doc['id']}", headers=auth)
    assert len(r.json()["body"]) == 100_000


# ───────────────────────── 分页边界 ─────────────────────────
def test_pagination_empty(client, auth):
    r = client.get("/api/v1/docs/trash", headers=auth)
    assert r.status_code == 200
    assert r.json()["items"] == []
    assert r.json()["next_cursor"] is None


def test_pagination_last_page(client, auth):
    for i in range(5):
        _create(client, auth, kind="doc", name=f"文档{i}")
    r = client.get("/api/v1/docs/nodes", headers=auth)
    assert r.status_code == 200
    # 树接口不分页（返回全部根），这里验证 trash 分页
    for i in range(5):
        doc = _create(client, auth, kind="doc", name=f"待删{i}")
        client.delete(f"/api/v1/docs/nodes/{doc['id']}", headers=auth)
    r = client.get("/api/v1/docs/trash", params={"limit": 2}, headers=auth)
    assert r.status_code == 200
    assert len(r.json()["items"]) == 2
    assert r.json()["next_cursor"] is not None
    r2 = client.get(
        "/api/v1/docs/trash", params={"limit": 2, "cursor": r.json()["next_cursor"]}, headers=auth
    )
    assert r2.status_code == 200
    assert len(r2.json()["items"]) == 2
    assert r2.json()["next_cursor"] is not None
    r3 = client.get(
        "/api/v1/docs/trash", params={"limit": 2, "cursor": r2.json()["next_cursor"]}, headers=auth
    )
    assert r3.status_code == 200
    assert len(r3.json()["items"]) == 1
    assert r3.json()["next_cursor"] is None


def test_pagination_bad_cursor(client, auth):
    """坏 cursor → 4xx（项目约定 ValidationError=422，跟代码库走）。"""
    r = client.get("/api/v1/docs/trash", params={"cursor": "not-a-cursor"}, headers=auth)
    assert r.status_code == 422


def test_pagination_limit_cap(client, auth):
    """limit 封顶 200：超过上限 FastAPI Query(le=200) 直接 422（这正是卡里要求的）。"""
    r = client.get("/api/v1/docs/trash", params={"limit": 99999}, headers=auth)
    assert r.status_code == 422


# ───────────────────────── 留白判据 ─────────────────────────
def test_zero_business_columns(client, auth):
    """docs_ 三张表 + docs_fts 无任何人/日记专有列；meta_json 承载扩展属性。"""
    engine = get_engine()
    with engine.connect() as conn:
        node_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(docs_node)")}
        content_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(docs_content)")}
        rev_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(docs_revision)")}
    business_words = {"价值观", "性格", "情绪", "心情", "人格", "日记", "标签", "评分", "封面"}
    for cols in (node_cols, content_cols, rev_cols):
        assert not (cols & business_words), f"发现业务字段：{cols & business_words}"
    # 扩展属性可写进 meta_json
    doc = _create(
        client, auth, kind="doc", name="带扩展", meta_json={"mood": "平静", "tags": ["a"]}
    )
    r = client.get(f"/api/v1/docs/nodes/{doc['id']}", headers=auth)
    assert r.json()["meta_json"]["mood"] == "平静"


# ---------- 按路径写正文（AI 友好 · 2026-09-27 主人）----------
def _by_path(client, auth, **kw):
    return client.post("/api/v1/docs/content", json=kw, headers=auth)


def test_put_content_by_path_creates_whole_chain(client, auth):
    """一次调用：建文件夹 -> 建文档 -> 写正文（AI 不必先查 id）。"""
    r = _by_path(client, auth, path="线上课/计算机视觉", body="# CV")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["kind"] == "doc" and d["name"] == "计算机视觉"
    assert (d["body"] or "").startswith("# CV")
    tree = client.get("/api/v1/docs/nodes", headers=auth).json()
    folder = [n for n in tree if n["name"] == "线上课"]
    assert folder and folder[0]["kind"] == "folder"
    assert [c["name"] for c in folder[0]["children"]] == ["计算机视觉"]


def test_put_content_by_path_is_idempotent_and_snapshots(client, auth):
    """同路径重复写 = 更新正文（不重复建节点），且每次保存都留版本快照。"""
    a = _by_path(client, auth, path="线上课/英语", body="v1").json()
    b = _by_path(client, auth, path="线上课/英语", body="v2").json()
    assert a["id"] == b["id"]
    assert b["body"] == "v2"
    revs = client.get(f"/api/v1/docs/nodes/{a['id']}/revisions", headers=auth).json()
    assert len(revs["items"]) == 2


def test_put_content_by_path_deep_nesting(client, auth):
    """多级路径逐段建：A/B/C -> 两个 folder + 一个 doc。"""
    d = _by_path(client, auth, path="A/B/C", body="deep").json()
    assert d["name"] == "C" and d["kind"] == "doc"
    tree = client.get("/api/v1/docs/nodes", headers=auth).json()
    a = [n for n in tree if n["name"] == "A"][0]
    b = a["children"][0]
    assert b["name"] == "B" and b["kind"] == "folder"
    assert b["children"][0]["name"] == "C"


def test_put_content_by_path_respects_create_if_missing(client, auth):
    r = _by_path(client, auth, path="没有的/文档", body="x", create_if_missing=False)
    assert r.status_code == 404


def test_put_content_by_path_rejects_all_empty_segments(client, auth):
    r = _by_path(client, auth, path="///", body="x")
    assert r.status_code == 422


def test_put_content_by_path_rejects_kind_conflict(client, auth):
    """中段撞上 doc（该是 folder）-> 422，不静默乱建。"""
    _create(client, auth, kind="doc", name="撞名")
    r = _by_path(client, auth, path="撞名/子文档", body="x")
    assert r.status_code == 422
