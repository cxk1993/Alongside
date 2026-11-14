# ── entry 三态的存活证明 ──
"""`entry` 字段契约（r5 热插拔契约层）的协议测试。

★ 为什么这件事要单独一个文件
--------------------------
第三方插件的 UI 不再要求构建期收录：产物放在插件目录的 `web/dist/` 下，
由后端资产端点 `GET /plugin-assets/{id}/{path}` 运行时服务。
于是 `entry` 从「一个构建期别名」变成**三态**：

    ① ``""``                       —— 纯后端插件，没有前端 UI
    ② ``@/apps/<id>``              —— 构建期别名，Vite 按 glob 解析
    ③ ``/plugin-assets/<id>/...``  —— 运行时资产 URL（也允许 http(s) 绝对/协议相对）

三态共存意味着「一个字符串字段承载三种来源」，而字符串字段最容易退化成
「什么都能写」：写 `javascript:` 就是注入，写别人的 id 就是越权取资产。
所以形状（schema pattern）与跨字段规则（validate.py 规则 15/16）都要有测试盯着。

★ 测试纪律（本项目 r3 的教训）
--------------------------
负例**一律用内存 dict**，绝不把坏值写进真实 manifest 再还原 ——
曾经有门禁自检就地写盘注入、把真源码文件改坏的事故。
本文件全程只构造字典，不写仓库内任何文件。
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

_API_ROOT = Path(__file__).resolve().parents[1]
_REPO = Path(__file__).resolve().parents[3]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

MODULES = _API_ROOT / "modules"
PLUGINS = _REPO / "plugins"


def _load(module_name: str, relpath: Path) -> Any:
    """按文件加载内核模块（绕开 core.plugins.__init__ 的重依赖）。

    ★ 与 test_protocol_gates.py 同款夹具：临时装一个只有 ManifestError 的
      假 core.errors，加载完**立刻恢复** sys.modules，否则会污染同进程里
      其它需要真 core.errors 的测试。
    """
    err_mod = types.ModuleType("core.errors")

    class ManifestError(Exception):
        pass

    err_mod.ManifestError = ManifestError  # type: ignore[attr-defined]
    real_errors = sys.modules.get("core.errors")
    sys.modules["core.errors"] = err_mod
    try:
        spec = importlib.util.spec_from_file_location(module_name, relpath)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if real_errors is None:
            sys.modules.pop("core.errors", None)
        else:
            sys.modules["core.errors"] = real_errors
    return mod


_validate = _load("entry_contract_validate", _API_ROOT / "core/plugins/validate.py")
validate_manifest = _validate.validate_manifest
_check_consistency = _validate._check_consistency
_SCHEMA = _validate._SCHEMA

_ENTRY_SPEC = _SCHEMA["properties"]["entry"]

#: 一份最小合法规约（id = acme，第三方）。
BASE: dict[str, Any] = {
    "id": "acme",
    "name": "样例插件",
    "version": "1.0.0",
    "kind": "third-party",
    "kernelApi": "^1",
    "api": {"base": "/api/v1/acme"},
    "provides": [],
    "requires": [],
    "slots": [],
    "emits": [],
    "consumes": [],
    "permissions": [],
}


def _mf(**mutate: Any) -> dict[str, Any]:
    m = copy.deepcopy(BASE)
    m.update(mutate)
    return m


# ═══════════════ ① 五种合法形状 ═══════════════


@pytest.mark.parametrize(
    ("label", "entry"),
    [
        ("空串=纯后端", ""),
        ("构建期别名", "@/apps/acme"),
        ("根相对资产 URL", "/plugin-assets/acme/index.js"),
        ("资产 URL 带子路径", "/plugin-assets/acme/dist/assets/index-1a2b.js"),
        ("绝对 URL", "https://cdn.example.com/acme/index.js"),
        ("绝对 URL（http + 端口）", "http://localhost:5173/src/main.tsx"),
        ("协议相对 URL", "//cdn.example.com/acme/index.js"),
    ],
)
def test_legal_entry_shapes_pass(label: str, entry: str) -> None:
    """五种形状各自必须过：三态语义都得有合法路径，否则契约自断一条腿。"""
    validate_manifest(_mf(entry=entry))


def test_entry_is_not_required() -> None:
    """★ 兼容性铁律：entry 不在 required 里，字段缺失按保守默认（纯后端）处理。"""
    assert "entry" not in _SCHEMA["required"]
    validate_manifest(_mf())  # 根本不写 entry，也必须过（auth 就是这种）


# ═══════════════ ② 非法形状（结构层 pattern 必须拦） ═══════════════


@pytest.mark.parametrize(
    ("label", "entry"),
    [
        ("别名 id 大写", "@/apps/Acme"),
        ("别名 id 为空", "@/apps/"),
        ("别名带子路径", "@/apps/acme/sub"),
        ("路径穿越（占位 id）", "/plugin-assets/../x/index.js"),
        ("路径穿越（合法 id 后）", "/plugin-assets/acme/../index.js"),
        ("路径穿越（中段）", "/plugin-assets/acme/a/../../secret.js"),
        ("资产 path 为空", "/plugin-assets/acme/"),
        ("资产 id 大写", "/plugin-assets/Acme/index.js"),
        ("javascript scheme", "javascript:alert(1)"),
        ("data scheme", "data:text/html,<script>alert(1)</script>"),
        ("ftp scheme", "ftp://x/index.js"),
        ("file scheme", "file:///etc/passwd"),
        ("乱码", "这不是一个入口"),
        ("裸文件名（既非别名也非绝对路径）", "index.js"),
        ("去掉 @ 的别名", "apps/acme"),
    ],
)
def test_illegal_entry_shapes_are_rejected(label: str, entry: str) -> None:
    """形状非法必须在结构层就被拒，且错误里能看出是 entry 这个字段。"""
    with pytest.raises(Exception, match=r"manifest\.entry"):
        validate_manifest(_mf(entry=entry))


# ═══════════════ ③ 规则 15：资产 id 必须等于 manifest.id ═══════════════


def test_rule15_asset_id_must_match_manifest_id() -> None:
    """形状合法但指向别人的 id：结构层放行，一致性层必须拒。"""
    with pytest.raises(Exception, match="越权"):
        validate_manifest(_mf(id="acme", entry="/plugin-assets/system/index.js"))


def test_rule15_same_id_is_fine() -> None:
    """自己的 id 指向自己的资产，正常。"""
    validate_manifest(_mf(id="acme", entry="/plugin-assets/acme/index.js"))


def test_rule15_only_guards_the_asset_prefix() -> None:
    """`@/apps/<id>` 不做 id 相等约束 —— plugins/pi-agent 就是 kind=third-party
    却写 `@/apps/pi-agent` 的存量反例，任何「third-party 不许用别名」的规则
    都会把存量打死。这里锁住「不加那条规则」这个决定。"""
    validate_manifest(_mf(id="acme", kind="third-party", entry="@/apps/somewhere-else"))


# ═══════════════ ④ 规则 16：URL scheme 白名单（纵深防御层） ═══════════════


@pytest.mark.parametrize(
    "entry",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "file:///etc/passwd",
        "ftp://x/index.js",
    ],
)
def test_rule16_scheme_whitelist_lives_in_consistency_layer(entry: str) -> None:
    """★ 直接打第二层，绕开 schema pattern。

    目的不是重复测 pattern，而是证明**pattern 之外还有一道判据**：
    将来为兼容放宽 pattern，这条规则不会跟着失效。
    一致性层必须给出「只允许 http / https」的人话错误。
    """
    errors = _check_consistency(_mf(entry=entry))
    assert any("只允许 http / https" in e for e in errors), errors


def test_rule16_http_and_protocol_relative_are_accepted_by_layer_two() -> None:
    """正向对照：http(s) 与协议相对 URL 不该被第二层误杀。"""
    for entry in (
        "https://cdn.example.com/acme/index.js",
        "http://localhost:5173/main.tsx",
        "//cdn.example.com/acme/index.js",
        "/plugin-assets/acme/index.js",
        "@/apps/acme",
        "",
    ):
        assert _check_consistency(_mf(entry=entry)) == [], entry


# ═══════════════ ⑤ 两个实现同判（自研校验器 vs jsonschema） ═══════════════


def test_two_schema_implementations_agree_on_entry() -> None:
    """同一份 pattern，自研 _validate_node 与 jsonschema 必须同判。

    ★ 为什么要对拍：本项目 schema 由自研校验器消费（不引 jsonschema 依赖），
      但 CI/IDE 里用 jsonschema 看这份契约。两个实现出现分歧时，
      「本地绿、别人报红」是最难查的一类问题。
    """
    jsonschema = pytest.importorskip("jsonschema")
    wrapper = {"type": "object", "properties": {"entry": _ENTRY_SPEC}}

    samples = [
        "",
        "@/apps/acme",
        "/plugin-assets/acme/index.js",
        "https://cdn.example.com/acme/index.js",
        "//cdn.example.com/acme/index.js",
        "@/apps/Acme",
        "/plugin-assets/../x/index.js",
        "javascript:alert(1)",
        "data:text/html,x",
        "ftp://x/index.js",
        "乱码",
    ]

    def custom_ok(value: str) -> bool:
        errs: list[str] = []
        _validate._validate_node({"entry": value}, wrapper, "manifest", errs)
        return not errs

    def jsonschema_ok(value: str) -> bool:
        try:
            jsonschema.validate({"entry": value}, wrapper)
        except jsonschema.ValidationError:
            return False
        return True

    diffs = [(s, custom_ok(s), jsonschema_ok(s)) for s in samples if custom_ok(s) != jsonschema_ok(s)]
    assert diffs == [], f"两个实现分歧（值, 自研, jsonschema）：{diffs}"


# ═══════════════ ⑥ 存量兼容：真实 31 份 manifest 全过 ═══════════════


def _real_manifest_paths() -> list[Path]:
    return sorted(MODULES.glob("*/manifest.json")) + sorted(PLUGINS.glob("*/manifest.json"))


_REAL = _real_manifest_paths()


def test_real_manifest_inventory_is_not_empty() -> None:
    """先确认扫描面没扫空：空目录扫出零个文件也能「全过」。"""
    assert len(_REAL) >= 31, f"只读到 {len(_REAL)} 份 manifest：{_REAL}"
    names = {p.parent.name for p in _REAL}
    assert {"ai-chat", "summary", "auth", "pi-agent", "countdown"} <= names


@pytest.mark.parametrize("path", _REAL, ids=lambda p: p.parent.name)
def test_real_manifest_passes_entry_contract(path: Path) -> None:
    """★ 存量零改动必须全过。

    存量取值只有三种：`@/apps/<id>`（22 个内置模块 + pi-agent）、
    `""`（ai-chat / summary / plugins / countdown）、不写（auth）。
    任何一条规则都不许把它们打死。
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    validate_manifest(raw)
