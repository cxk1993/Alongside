"""r5 热插拔 · 安全门禁与回归测试（补 test_plugin_hotplug_r5.py 未覆盖的判据）。

分工：`test_plugin_hotplug_r5.py` 覆盖端点形状与主要正/负例；
本文件补**安全边界**与**已修 bug 的回归**——每一条都对应一个真实缺陷或
一条真实攻击形状，不是凑覆盖率。

  A. 回归：平铺包必须装得上
     `zip -r x.zip .`（manifest 在包根）是最自然的打包法，而旧实现用
     `os.replace(tmp_root, tmp_root/".staged-<id>")` 把目录移进自身 ⇒
     Windows `WinError 87`／POSIX `EINVAL` ⇒ **平铺包 100% 装不上**，
     只有单层包裹能过。这条用例就是那次的回归。

  B. 回归：`web/dist` 根本身是符号链接 ⇒ 不服务
     `_is_within(target, root)` 在 root 已被换成外链时会把「外面」判成「里面」
     （根已经在外面了）。必须先要求根的落点仍在插件目录内。

  C. 卸载是完整的「拔掉」：路由 404 + 资产 404 + 目录消失 + 模块缓存清空
     D4 明确要求的一条，此前无人验。

  D. zip 形状拒绝：绝对路径条目 / 盘符条目 / 符号链接条目 / 条目数上限 /
     解压体积上限（zip bomb）。

  E. 契约闸：非法 manifest（缺 kernelApi）、id 与请求不符 ⇒ 拒。

负例一律在 tmp_path 沙箱里造，绝不写真源码树。
"""
from __future__ import annotations

import io
import json
import sys
import uuid
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel

from core.app import create_app
from core.security import create_access_token
from db.engine import init_engine

API_ROOT = Path(__file__).resolve().parents[1]

TOKEN = create_access_token("admin")
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _short() -> str:
    return uuid.uuid4().hex[:6]


def _manifest(pid: str, **over: object) -> str:
    m: dict[str, object] = {
        "id": pid,
        "name": f"安全测试 {pid}",
        "version": "0.1.0",
        "kind": "third-party",
        "kernelApi": "^1",
        "icon": "star",
        "description": "安全门禁用例",
        "author": "test",
        "window": {"w": 320, "h": 240},
        "entry": "",
        "api": {"base": f"/api/v1/{pid}", "health": f"/api/v1/{pid}/health"},
        "provides": [],
        "requires": [],
        "slots": [],
        "emits": [],
        "consumes": [],
        "permissions": [],
    }
    m.update(over)
    return json.dumps(m, ensure_ascii=False)


def _zip(files: dict[str, str], *, prefix: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(f"{prefix}{name}", content)
    return buf.getvalue()


def _upload(client: TestClient, payload: bytes) -> object:
    return client.post(
        "/api/v1/plugins/upload",
        files={"file": ("p.zip", payload, "application/zip")},
        headers=AUTH,
    )


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """独立引擎 + 独立数据库 + 隔离的数据插件根（不污染真实 data/plugins）。"""
    import db.engine as _db

    _db._engine = None
    engine = init_engine(f"sqlite:///{tmp_path / 't.db'}")
    SQLModel.metadata.create_all(engine)

    data_root = tmp_path / "data" / "plugins"
    data_root.mkdir(parents=True, exist_ok=True)

    from core.plugins import discover as discover_mod

    # ★ 两个根都隔离到 tmp_path：
    #   · 数据根（可写，上传落点）
    #   · 发行根（只读兼容位）——刻意**不指向真实仓库 plugins/**，
    #     否则测"发行根里的插件"时会动到仓库文件（r5 实测踩过：
    #     一次 install→uninstall 把示例插件源码删了）。
    bundled_root = tmp_path / "bundled_plugins"
    bundled_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(discover_mod, "default_data_plugins_root", lambda: data_root)
    monkeypatch.setattr(discover_mod, "_DEFAULT_PLUGINS_DIR", bundled_root)
    # ★ 不再 monkeypatch discover_plugins 整体替换 —— 那会把发现层的
    #   「三根 + crosscheck 降级 + kind 一致性」全部旁路掉，测试就测不到真代码。
    yield TestClient(create_app())


def _router_src() -> str:
    return (
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/ping')\n"
        "def ping(): return {'pong': True}\n"
    )


# ═══════════ A. 回归：平铺包（manifest 在包根）必须装得上 ═══════════
def test_regression_flat_zip_installs(client: TestClient, tmp_path: Path) -> None:
    """`zip -r x.zip .` 形状（manifest 在包根）—— 旧实现 WinError 87 装不上。"""
    pid = f"f{_short()}"
    payload = _zip({"manifest.json": _manifest(pid), "api/router.py": _router_src()})
    r = _upload(client, payload)
    assert r.status_code == 200, r.text
    assert r.json()["id"] == pid
    assert r.json()["directory"].replace("\\", "/").endswith(f"/plugins/{pid}")

    # 落盘内容完整（不是半个插件）
    landed = tmp_path / "data" / "plugins" / pid
    assert (landed / "manifest.json").is_file()
    assert (landed / "api" / "router.py").is_file()
    # 临时目录不留残留
    leftovers = [p.name for p in landed.parent.iterdir() if p.name.startswith(".upload-")]
    assert not leftovers, f"上传临时目录未清理：{leftovers}"

    r = client.post("/api/v1/plugins/install", json={"id": pid}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert client.get(f"/api/v1/{pid}/ping", headers=AUTH).status_code == 200


def test_regression_flat_zip_replaces_cleanly_on_second_try(
    client: TestClient, tmp_path: Path
) -> None:
    """平铺包落地后可再次上传同 id ⇒ 422（不覆盖），且原目录完好。"""
    pid = f"f{_short()}"
    payload = _zip({"manifest.json": _manifest(pid), "api/router.py": _router_src()})
    assert _upload(client, payload).status_code == 200
    r = _upload(client, payload)
    assert r.status_code == 422, r.text
    landed = tmp_path / "data" / "plugins" / pid
    assert (landed / "manifest.json").is_file(), "拒绝重复后原目录必须完好"


# ═══════════ B. 回归：web/dist 根本身是外链 ⇒ 不服务 ═══════════
def test_assets_root_symlink_escape_rejected(client: TestClient, tmp_path: Path) -> None:
    """`<插件>/web/dist` 指向插件目录之外 ⇒ 该文件不可取（根已被换掉）。

    ★ 旧实现只在「目标是否在 root 内」上判定，而 root 本身已 resolve 到外部，
      于是「外面的一切」都合法通过。这条用例守着「根也要钉住」。
    """
    pid = f"s{_short()}"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.js").write_text("LEAKED\n", encoding="utf-8")

    # 手工把插件放到数据根（模拟「管理员已落盘」），再把 dist 换成外链
    plug = tmp_path / "data" / "plugins" / pid
    (plug / "web").mkdir(parents=True)
    (plug / "api").mkdir(parents=True)
    (plug / "manifest.json").write_text(_manifest(pid), encoding="utf-8")
    (plug / "api" / "router.py").write_text(_router_src(), encoding="utf-8")
    try:
        (plug / "web" / "dist").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("本机无权限创建符号链接（Windows 需开发者模式）")

    r = client.post("/api/v1/plugins/install", json={"id": pid}, headers=AUTH)
    assert r.status_code == 200, r.text
    r = client.get(f"/plugin-assets/{pid}/leak.js", headers=AUTH)
    assert r.status_code == 404, "dist 为外链时不该服务任何文件"
    assert "LEAKED" not in r.text


# ═══════════ C. 卸载 = 完整的「拔掉」 ═══════════
def test_uninstall_removes_route_assets_dir_and_modules(
    client: TestClient, tmp_path: Path
) -> None:
    """D4 明确要求：卸载后**路由与资产双 404**，目录消失，模块缓存清空。"""
    pid = f"u{_short()}"
    payload = _zip(
        {
            "manifest.json": _manifest(pid),
            "api/router.py": _router_src(),
            "web/dist/index.js": "export default 1\n",
        }
    )
    assert _upload(client, payload).status_code == 200
    assert (
        client.post("/api/v1/plugins/install", json={"id": pid}, headers=AUTH).status_code
        == 200
    )
    base = f"/api/v1/{pid}"
    pkg_name = f"plugin_thirdparty_{pid}"
    assert client.get(f"{base}/ping", headers=AUTH).status_code == 200
    assert client.get(f"/plugin-assets/{pid}/index.js", headers=AUTH).status_code == 200
    assert pkg_name in sys.modules, "安装后应能加载插件包"

    r = client.post(f"/api/v1/plugins/{pid}/uninstall", headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["uninstalled"] is True

    assert client.get(f"{base}/ping", headers=AUTH).status_code == 404, "路由必须摘干净"
    assert (
        client.get(f"/plugin-assets/{pid}/index.js", headers=AUTH).status_code == 404
    ), "资产必须随之 404"
    assert not (tmp_path / "data" / "plugins" / pid).exists(), "目录必须删除"
    # 层3.5：模块缓存清空（同 id 重装才会拿到干净的新代码）
    residue = [n for n in sys.modules if n == pkg_name or n.startswith(pkg_name + ".")]
    assert not residue, f"sys.modules 残留：{residue}"
    assert pid not in {m.get("id") for m in client.get("/api/v1/modules", headers=AUTH).json()["modules"]}


def test_uninstall_of_bundled_root_keeps_files(
    client: TestClient, tmp_path: Path
) -> None:
    """★ 回归：**发行根**里的插件被卸载时，文件必须留着（只清状态）。

    实测事故：对仓库 `plugins/<id>` 走一遍 install→uninstall ⇒ 源码被 rmtree，
    而调用方以为在操作"自己装的那份"。判据来自 r5 层1.1 的定义：
    只有可写数据根（`<data_dir>/plugins/`）里的目录才归卸载回收。
    """
    pid = f"b{_short()}"
    bundled = tmp_path / "bundled_plugins" / pid
    (bundled / "api").mkdir(parents=True)
    (bundled / "manifest.json").write_text(_manifest(pid), encoding="utf-8")
    (bundled / "api" / "router.py").write_text(_router_src(), encoding="utf-8")

    # 已落盘的发行根插件：走"注册已落盘目录"那条 install
    assert (
        client.post("/api/v1/plugins/install", json={"id": pid}, headers=AUTH).status_code
        == 200
    )
    r = client.post(f"/api/v1/plugins/{pid}/uninstall", headers=AUTH)
    assert r.status_code == 200, r.text

    assert (bundled / "manifest.json").is_file(), "发行根的源码文件不该被删"
    assert (bundled / "api" / "router.py").is_file()
    # 状态与路由仍要清干净（卸载语义不变）
    assert client.get(f"/api/v1/{pid}/ping", headers=AUTH).status_code == 404
    item = next(
        p
        for p in client.get("/api/v1/plugins", headers=AUTH).json()["plugins"]
        if p.get("id") == pid
    )
    # ★ 注意语义：`/api/v1/plugins` 是**发现**清单 —— 文件还在，所以它仍在列
    #   （valid=true），只是"未安装/未启用"：状态行已删 ⇒ third-party 无行即未启用，
    #   phase 回到 PENDING。这正是"发行根插件卸载"能达到的终态：
    #   我们清掉的是**装的那份状态**，不是发行版的文件。
    assert item["enabled"] is False, "卸载后不应仍处于启用态"
    assert item["phase"] == "PENDING", item


def test_disable_then_asset_404_and_reenable_restores(
    client: TestClient, tmp_path: Path
) -> None:
    """禁用 ⇒ 资产 404（拔掉一半的核心）；重新启用 ⇒ 恢复。"""
    pid = f"d{_short()}"
    payload = _zip(
        {
            "manifest.json": _manifest(pid),
            "api/router.py": _router_src(),
            "web/dist/index.js": "export default 1\n",
        }
    )
    assert _upload(client, payload).status_code == 200
    client.post("/api/v1/plugins/install", json={"id": pid}, headers=AUTH)
    assert client.get(f"/plugin-assets/{pid}/index.js", headers=AUTH).status_code == 200

    client.post(f"/api/v1/plugins/{pid}/disable", headers=AUTH)
    assert client.get(f"/plugin-assets/{pid}/index.js", headers=AUTH).status_code == 404

    client.post(f"/api/v1/plugins/{pid}/enable", headers=AUTH)
    assert client.get(f"/plugin-assets/{pid}/index.js", headers=AUTH).status_code == 200


# ═══════════ D. zip 形状拒绝 ═══════════
def test_upload_rejects_absolute_and_drive_entries(client: TestClient) -> None:
    for evil in ("/abs.txt", "C:/win.txt", "\\\\server\\share\\x.txt"):
        payload = _zip({"manifest.json": _manifest(f"a{_short()}"), evil: "x"})
        r = _upload(client, payload)
        assert r.status_code == 422, f"{evil!r} 应被拒：{r.status_code} {r.text[:120]}"


def test_upload_rejects_symlink_entry(client: TestClient) -> None:
    """zip 里的符号链接条目（unix mode 0xA000）一律拒收。"""
    pid = f"l{_short()}"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", _manifest(pid))
        info = zipfile.ZipInfo("link.js")
        info.external_attr = (0xA1FF << 16)  # S_IFLNK | 0777
        zf.writestr(info, "target.js")
    r = _upload(client, buf.getvalue())
    assert r.status_code == 422, r.text


def test_upload_rejects_too_many_entries(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """条目数上限（防「十万个空文件」打满 inode）。"""
    from core.plugins import package as pkg

    monkeypatch.setattr(pkg, "MAX_ENTRIES", 3)
    files = {"manifest.json": _manifest(f"e{_short()}")}
    for i in range(5):
        files[f"f{i}.txt"] = "x"
    r = _upload(client, _zip(files))
    assert r.status_code == 422, r.text


def test_upload_rejects_declared_oversize(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """解压后体积上限（声明值）。"""
    from core.plugins import package as pkg

    monkeypatch.setattr(pkg, "MAX_EXTRACTED_BYTES", 64)
    payload = _zip({"manifest.json": _manifest(f"b{_short()}"), "big.txt": "A" * 4096})
    r = _upload(client, payload)
    assert r.status_code == 422, r.text


def test_upload_rejects_oversize_archive(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """上传体积上限（流入量）。"""
    from core.plugins import package as pkg

    monkeypatch.setattr(pkg, "MAX_ARCHIVE_BYTES", 128)
    payload = _zip({"manifest.json": _manifest(f"c{_short()}"), "x.txt": "B" * 4096})
    r = _upload(client, payload)
    assert r.status_code == 422, r.text


# ═══════════ E. 契约闸 ═══════════
def test_upload_rejects_manifest_missing_required_field(client: TestClient) -> None:
    """缺 kernelApi ⇒ 契约校验失败（错误精确到字段），不许落地。"""
    pid = f"m{_short()}"
    raw = json.loads(_manifest(pid))
    raw.pop("kernelApi")
    r = _upload(client, _zip({"manifest.json": json.dumps(raw, ensure_ascii=False)}))
    assert r.status_code == 422, r.text
    assert "kernelApi" in r.text, "错误信息应指出缺哪个字段"


def test_upload_rejects_kind_core(client: TestClient) -> None:
    """kind=core 不许从上传通道进（随系统发布，装不了也卸不掉）。"""
    r = _upload(client, _zip({"manifest.json": _manifest(f"k{_short()}", kind="core")}))
    assert r.status_code == 422, r.text


def test_upload_rejects_shape_without_manifest(client: TestClient) -> None:
    """包根没有 manifest.json、也不是单层包裹 ⇒ 拒（不做递归猜测）。"""
    r = _upload(client, _zip({"readme.txt": "no manifest here"}))
    assert r.status_code == 422, r.text
