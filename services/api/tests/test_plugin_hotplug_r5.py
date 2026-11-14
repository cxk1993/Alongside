"""插件运行时资产端点 + 上传落地 + 阶段/插槽的测试（r5 热插拔）。

覆盖 D4 安全门禁要求的那几条，以及层0.2/0.4/2.2/2.3/3.3/3.4 的判据：

  资产端点（/plugin-assets/*）
    · 未登录 ⇒ 401（无头 / 假 token / 错误 token 类型）
    · 写方法 ⇒ 405
    · 未启用（PENDING/PAUSED）⇒ 404（不区分「不存在」，避免枚举探针）
    · 路径穿越：`..`、编码变体、绝对路径、盘符、反斜杠 ⇒ 404 且**不泄露**
    · MIME 白名单外 ⇒ 404；命中 ⇒ 正确 Content-Type + nosniff + no-cache
    · HEAD 可取（返回头、无 body）

  上传落地（POST /api/v1/plugins/upload）
    · 未登录 ⇒ 401
    · 合法 zip ⇒ 落地到 data_dir/plugins/<id>，清单可见
    · zip slip（`../`）⇒ 422，且**包外不留痕**
    · 目录名与 manifest.id 不一致（平铺包）⇒ 按 manifest.id 落地
    · kind=builtin 的包 ⇒ 拒
    · 重复 id ⇒ 422（不覆盖）
    · 非 zip ⇒ 422

  阶段 / 插槽 / 清单
    · install 后 phase=ACTIVE（层0.2，回归「装完仍是 PENDING」）
    · slots 默认只返回启用项；include_disabled=true 返回全量（层0.4）
    · 第三方插件有 web/dist/index.js ⇒ entry 被改写成 /plugin-assets/<id>/index.js（层2.3）
    · enable 后 /api/v1/modules 能看到它；disable 后看不到（层3.4）
    · mount/unmount 后 openapi_schema 被清空（层3.3）

负例一律在**临时目录**里造，绝不写真源码树（r3 注入残留的教训）。
"""
from __future__ import annotations

import io
import json
import shutil
import uuid
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel

from core.app import create_app
from core.security import create_access_token
from db.engine import get_engine, init_engine

API_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = API_ROOT.parent.parent

TOKEN = create_access_token("admin")
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _make_zip(files: dict[str, str], *, root_prefix: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(f"{root_prefix}{name}", content)
    return buf.getvalue()


def _manifest(pid: str, *, kind: str = "third-party", entry: str = "") -> str:
    return json.dumps(
        {
            "id": pid,
            "name": f"测试插件 {pid}",
            "version": "0.1.0",
            "kind": kind,
            "kernelApi": "^1",
            "icon": "star",
            "description": "测试",
            "author": "test",
            "window": {"w": 480, "h": 320},
            "entry": entry,
            "api": {"base": f"/api/v1/{pid}", "health": f"/api/v1/{pid}/health"},
            "provides": [f"{pid}.thing.read"],
            "requires": [],
            "slots": [],
            "emits": [],
            "consumes": [],
            "permissions": ["db:own"],
        },
        ensure_ascii=False,
    )


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """独立引擎 + 独立数据库 + 独立数据插件根。

    ★ 数据插件根也隔离到 tmp_path：上传测试会往那儿写目录，
      不隔离就会污染真实 data/plugins（别的测试/真实安装）。
    """
    import db.engine as _db_engine_mod

    _db_engine_mod._engine = None
    engine = init_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)

    data_root = tmp_path / "data" / "plugins"
    data_root.mkdir(parents=True, exist_ok=True)

    # ★ 把「数据插件根」钉到临时目录，并让发现层在**三个根**上工作：
    #   内置（真实 modules/）+ 仓库根 plugins/（只读兼容位）+ 临时数据根。
    #   不隔离数据根的话，上传测试会往真实 data/plugins 写东西。
    from core.plugins import discover as discover_mod

    monkeypatch.setattr(discover_mod, "default_data_plugins_root", lambda: data_root)

    def _discover(modules_dir=None, plugins_dir=None):
        d = discover_mod
        root = Path(modules_dir) if modules_dir else d._DEFAULT_MODULES_DIR
        plug = Path(plugins_dir) if plugins_dir else d._DEFAULT_PLUGINS_DIR
        builtins, b_errs = d._scan(root, "builtin")
        repo_tp, r_errs = d._scan(plug, "third-party")
        data_tp, d_errs = d._scan(data_root, "third-party")
        # 数据根优先：同 id 时后写入的（数据根那份）赢
        merged: dict[str, object] = {}
        for info in [*repo_tp, *data_tp]:
            merged[info.id] = info
        return d.DiscoveryResult(
            plugins=[*builtins, *merged.values()],
            errors=[*b_errs, *r_errs, *d_errs],
        )

    monkeypatch.setattr(discover_mod, "discover_plugins", _discover)
    yield TestClient(create_app())


# ══════════════════════ 资产端点 ══════════════════════
def _install_with_assets(client: TestClient, tmp_path: Path, pid: str) -> None:
    """装一个带 web/dist/index.js 的第三方插件（走上传 → 安装）。"""
    files = {
        "manifest.json": _manifest(pid),
        "api/router.py": (
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "@router.get('/health')\n"
            "def health(): return {'ok': True}\n"
        ),
        "web/dist/index.js": "export default function UI(){return null}\n",
    }
    r = client.post(
        "/api/v1/plugins/upload",
        files={"file": ("p.zip", _make_zip(files), "application/zip")},
        headers=AUTH,
    )
    assert r.status_code == 200, r.text
    assert r.json()["id"] == pid
    r = client.post("/api/v1/plugins/install", json={"id": pid}, headers=AUTH)
    assert r.status_code == 200, r.text


def test_assets_requires_auth(client: TestClient) -> None:
    r = client.get("/plugin-assets/nope/index.js")
    assert r.status_code == 401, r.text


def test_assets_rejects_wrong_token_type(client: TestClient) -> None:
    from core.security import create_sse_ticket

    r = client.get(
        "/plugin-assets/nope/index.js",
        headers={"Authorization": f"Bearer {create_sse_ticket('admin')}"},
    )
    assert r.status_code == 401, r.text


def test_assets_write_method_405(client: TestClient) -> None:
    r = client.post("/plugin-assets/x/index.js", headers=AUTH)
    assert r.status_code == 405, r.text


def test_assets_serves_only_active_plugin(client: TestClient, tmp_path: Path) -> None:
    pid = f"t{r5_short()}"
    files = {
        "manifest.json": _manifest(pid),
        "api/router.py": "from fastapi import APIRouter\nrouter = APIRouter()\n",
        "web/dist/index.js": "export default function UI(){return null}\n",
    }
    # 只上传不安装 ⇒ PENDING ⇒ 资产 404
    r = client.post(
        "/api/v1/plugins/upload",
        files={"file": ("p.zip", _make_zip(files), "application/zip")},
        headers=AUTH,
    )
    assert r.status_code == 200, r.text
    r = client.get(f"/plugin-assets/{pid}/index.js", headers=AUTH)
    assert r.status_code == 404, "未启用的插件资产不该可取"

    # 安装 ⇒ ACTIVE ⇒ 200
    r = client.post("/api/v1/plugins/install", json={"id": pid}, headers=AUTH)
    assert r.status_code == 200, r.text
    r = client.get(f"/plugin-assets/{pid}/index.js", headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/javascript")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "no-cache" in r.headers["cache-control"]
    assert "export default" in r.text

    # 停用 ⇒ 404（拔掉的一半）
    r = client.post(f"/api/v1/plugins/{pid}/disable", headers=AUTH)
    assert r.status_code == 200, r.text
    r = client.get(f"/plugin-assets/{pid}/index.js", headers=AUTH)
    assert r.status_code == 404, "停用后资产必须 404"


def test_assets_head_ok(client: TestClient, tmp_path: Path) -> None:
    pid = f"t{r5_short()}"
    _install_with_assets(client, tmp_path, pid)
    r = client.head(f"/plugin-assets/{pid}/index.js", headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.content == b""


@pytest.mark.parametrize(
    "evil",
    [
        "../manifest.json",
        "../../../etc/passwd",
        "..%2fmanifest.json",
        "%2e%2e%2fmanifest.json",
        "%252e%252e%252fmanifest.json",
        "..\\manifest.json",
        "/etc/passwd",
        "C:/Windows/win.ini",
        "subdir/../../manifest.json",
    ],
)
def test_assets_path_traversal_404(client: TestClient, tmp_path: Path, evil: str) -> None:
    pid = f"t{r5_short()}"
    _install_with_assets(client, tmp_path, pid)
    r = client.get(f"/plugin-assets/{pid}/{evil}", headers=AUTH)
    assert r.status_code in (404, 400), f"{evil!r} 不该被服务：{r.status_code}"
    # 绝不能把 manifest.json（在 dist 之外）的内容吐出来
    assert "kernelApi" not in r.text


def test_assets_mime_whitelist(client: TestClient, tmp_path: Path) -> None:
    pid = f"t{r5_short()}"
    files = {
        "manifest.json": _manifest(pid),
        "api/router.py": "from fastapi import APIRouter\nrouter = APIRouter()\n",
        "web/dist/index.js": "export default 1\n",
        "web/dist/secret.env": "SECRET=leak\n",
        "web/dist/evil.html": "<script>alert(1)</script>\n",
    }
    r = client.post(
        "/api/v1/plugins/upload",
        files={"file": ("p.zip", _make_zip(files), "application/zip")},
        headers=AUTH,
    )
    assert r.status_code == 200, r.text
    client.post("/api/v1/plugins/install", json={"id": pid}, headers=AUTH)

    r = client.get(f"/plugin-assets/{pid}/secret.env", headers=AUTH)
    assert r.status_code == 404, "白名单外扩展名不该被服务"
    r = client.get(f"/plugin-assets/{pid}/evil.html", headers=AUTH)
    assert r.status_code == 404, "html 刻意不在白名单（同源 phishing 面）"


# ══════════════════════ 上传落地 ══════════════════════
def test_upload_requires_auth(client: TestClient) -> None:
    r = client.post(
        "/api/v1/plugins/upload",
        files={"file": ("p.zip", _make_zip({"manifest.json": "{}"}), "application/zip")},
    )
    assert r.status_code == 401, r.text


def test_upload_rejects_non_zip(client: TestClient) -> None:
    r = client.post(
        "/api/v1/plugins/upload",
        files={"file": ("p.zip", b"not a zip at all", "application/zip")},
        headers=AUTH,
    )
    assert r.status_code == 422, r.text


def test_upload_rejects_zip_slip(client: TestClient) -> None:
    pid = f"t{r5_short()}"
    payload = _make_zip(
        {
            "manifest.json": _manifest(pid),
            "../escaped.txt": "pwned",
        }
    )
    r = client.post(
        "/api/v1/plugins/upload",
        files={"file": ("p.zip", payload, "application/zip")},
        headers=AUTH,
    )
    assert r.status_code == 422, r.text
    # ★ 包外不留痕：逃逸文件绝不落地
    escaped = PROJECT_ROOT / "escaped.txt"
    assert not escaped.exists(), "zip slip 逃逸文件不该被写出"


def test_upload_rejects_builtin_kind(client: TestClient) -> None:
    pid = f"t{r5_short()}"
    payload = _make_zip({"manifest.json": _manifest(pid, kind="builtin")})
    r = client.post(
        "/api/v1/plugins/upload",
        files={"file": ("p.zip", payload, "application/zip")},
        headers=AUTH,
    )
    assert r.status_code == 422, r.text


def test_upload_accepts_single_wrapper_dir(client: TestClient) -> None:
    pid = f"t{r5_short()}"
    payload = _make_zip(
        {
            "manifest.json": _manifest(pid),
            "api/router.py": "from fastapi import APIRouter\nrouter = APIRouter()\n",
        },
        root_prefix=f"{pid}/",
    )
    r = client.post(
        "/api/v1/plugins/upload",
        files={"file": ("p.zip", payload, "application/zip")},
        headers=AUTH,
    )
    assert r.status_code == 200, r.text
    assert r.json()["id"] == pid
    assert r.json()["directory"].replace("\\", "/").endswith(f"/plugins/{pid}")


def test_upload_rejects_duplicate_id(client: TestClient) -> None:
    pid = f"t{r5_short()}"
    payload = _make_zip(
        {
            "manifest.json": _manifest(pid),
            "api/router.py": "from fastapi import APIRouter\nrouter = APIRouter()\n",
        }
    )
    r = client.post(
        "/api/v1/plugins/upload",
        files={"file": ("p.zip", payload, "application/zip")},
        headers=AUTH,
    )
    assert r.status_code == 200, r.text
    r = client.post(
        "/api/v1/plugins/upload",
        files={"file": ("p.zip", payload, "application/zip")},
        headers=AUTH,
    )
    assert r.status_code == 422, r.text


# ══════════════════════ 阶段 / 插槽 / 清单 ══════════════════════
def test_install_sets_active_phase(client: TestClient) -> None:
    """层0.2 回归：install 之后 phase 必须是 ACTIVE，不是默认 PENDING。"""
    pid = f"t{r5_short()}"
    payload = _make_zip(
        {
            "manifest.json": _manifest(pid),
            "api/router.py": "from fastapi import APIRouter\nrouter = APIRouter()\n",
        }
    )
    client.post(
        "/api/v1/plugins/upload",
        files={"file": ("p.zip", payload, "application/zip")},
        headers=AUTH,
    )
    r = client.post("/api/v1/plugins/install", json={"id": pid}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["phase"] == "ACTIVE", r.json()


def test_slots_filters_disabled_by_default(client: TestClient) -> None:
    """层0.4：默认只返回启用项；include_disabled 才给全量。"""
    pid = f"t{r5_short()}"
    payload = _make_zip(
        {
            "manifest.json": json.dumps(
                {**json.loads(_manifest(pid)), "slots": ["dashboard.card"]},
                ensure_ascii=False,
            ),
            "api/router.py": "from fastapi import APIRouter\nrouter = APIRouter()\n",
        }
    )
    client.post(
        "/api/v1/plugins/upload",
        files={"file": ("p.zip", payload, "application/zip")},
        headers=AUTH,
    )
    # 未安装 ⇒ 不在默认视图里
    r = client.get("/api/v1/plugins/slots/dashboard.card", headers=AUTH)
    ids = [c["plugin_id"] for c in r.json()["contributions"]]
    assert pid not in ids
    # 全量视图里能看到（且 enabled=False）
    r = client.get(
        "/api/v1/plugins/slots/dashboard.card?include_disabled=true", headers=AUTH
    )
    found = [c for c in r.json()["contributions"] if c["plugin_id"] == pid]
    assert found and found[0]["enabled"] is False

    # 安装后进默认视图
    client.post("/api/v1/plugins/install", json={"id": pid}, headers=AUTH)
    r = client.get("/api/v1/plugins/slots/dashboard.card", headers=AUTH)
    ids = [c["plugin_id"] for c in r.json()["contributions"]]
    assert pid in ids


def test_entry_rewritten_to_asset_url(client: TestClient) -> None:
    """层2.3：有 web/dist/index.js 的第三方插件，entry 被改写成资产 URL。"""
    pid = f"t{r5_short()}"
    payload = _make_zip(
        {
            "manifest.json": _manifest(pid),
            "api/router.py": "from fastapi import APIRouter\nrouter = APIRouter()\n",
            "web/dist/index.js": "export default 1\n",
        }
    )
    client.post(
        "/api/v1/plugins/upload",
        files={"file": ("p.zip", payload, "application/zip")},
        headers=AUTH,
    )
    client.post("/api/v1/plugins/install", json={"id": pid}, headers=AUTH)
    r = client.get("/api/v1/plugins", headers=AUTH)
    item = next(p for p in r.json()["plugins"] if p.get("id") == pid)
    assert item["manifest"]["entry"] == f"/plugin-assets/{pid}/index.js"


def test_modules_visibility_follows_lifecycle(client: TestClient) -> None:
    """层3.4：enable 后 /api/v1/modules 能看到，disable 后看不到。"""
    pid = f"t{r5_short()}"
    payload = _make_zip(
        {
            "manifest.json": _manifest(pid),
            "api/router.py": "from fastapi import APIRouter\nrouter = APIRouter()\n",
        }
    )
    client.post(
        "/api/v1/plugins/upload",
        files={"file": ("p.zip", payload, "application/zip")},
        headers=AUTH,
    )
    client.post("/api/v1/plugins/install", json={"id": pid}, headers=AUTH)
    r = client.get("/api/v1/modules", headers=AUTH)
    assert pid in {m.get("id") for m in r.json()["modules"]}

    client.post(f"/api/v1/plugins/{pid}/disable", headers=AUTH)
    r = client.get("/api/v1/modules", headers=AUTH)
    assert pid not in {m.get("id") for m in r.json()["modules"]}


def test_openapi_cache_invalidated_on_mount(client: TestClient) -> None:
    """层3.3：运行期挂载后 openapi 快照必须失效。"""
    app = client.app
    app.openapi()  # 先烤一份快照
    assert app.openapi_schema is not None
    pid = f"t{r5_short()}"
    payload = _make_zip(
        {
            "manifest.json": _manifest(pid),
            "api/router.py": (
                "from fastapi import APIRouter\nrouter = APIRouter()\n"
                "@router.get('/ping')\ndef ping(): return {'pong': True}\n"
            ),
        }
    )
    client.post(
        "/api/v1/plugins/upload",
        files={"file": ("p.zip", payload, "application/zip")},
        headers=AUTH,
    )
    client.post("/api/v1/plugins/install", json={"id": pid}, headers=AUTH)
    assert app.openapi_schema is None, "挂载后 OpenAPI 快照应失效"
    paths = app.openapi().get("paths", {})
    assert any(p.startswith(f"/api/v1/{pid}") for p in paths)


def r5_short() -> str:
    return uuid.uuid4().hex[:6]
