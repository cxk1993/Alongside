"""插件骨架生成器（T14）——**新插件唯一入口**。

为什么必须由它生成：
    插件清单是契约（`contracts/plugin.schema.json`）。如果每张卡自己手写 manifest，
    迟早写出十种写法，而"内核能不能不改一行代码就加载它"这件事就没法保证了。
    所以：**不许手搓插件目录，一律跑这个脚本。**

用法：
    python scripts/create_plugin.py <id> "<名称>"                     # 第三方插件（默认）
    python scripts/create_plugin.py <id> "<名称>" --kind builtin      # 内置插件
    python scripts/create_plugin.py <id> "<名称>" --force             # 目录已存在时覆盖

生成物：
  第三方（`plugins/<id>/`）—— 整体放一处，自带 web/ 与 api/
    ├─ manifest.json          kind=third-party，可禁用、可卸载
    ├─ README.md
    ├─ api/router.py          路由（HTTP 契约：成功返资源 JSON，失败 RFC7807）
    ├─ api/models.py          表模型（表名必须以插件 id 为前缀）
    ├─ api/settings.schema.json
    └─ api/migrations/0001_init.py

  内置（半半分居两处，用同一个 id 关联）
    ├─ services/api/modules/<id>/      manifest + router + models + migrations/
    └─ apps/web/src/apps/<id>/index.tsx

生成后请立刻验证：
    python -m pytest tests/test_plugins.py -q      # 框架自测
    python -m db.migrate upgrade head              # 插件迁移会被一起执行
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_ID_MIN, _ID_MAX = 2, 39

_API_ROOT = Path(__file__).resolve().parents[1]      # services/api
_PROJECT_ROOT = _API_ROOT.parent.parent              # 项目根
_PLUGINS_DIR = _PROJECT_ROOT / "plugins"
_BUILTIN_API_DIR = _API_ROOT / "modules"
_BUILTIN_WEB_DIR = _PROJECT_ROOT / "apps" / "web" / "src" / "apps"


def _table_prefix(plugin_id: str) -> str:
    """表名前缀：连字符换成下划线（SQL 标识符不允许连字符）。"""
    return plugin_id.replace("-", "_")


# 与 contracts/plugin.schema.json 的 required 清单保持一致。
# 注意：勿猜 的 activates_on 等未入 schema 的新字段，候合入后再补。
_REQUIRED_MANIFEST_FIELDS: tuple[str, ...] = (
    "id",
    "name",
    "version",
    "kind",
    "kernelApi",
    "api",
    "provides",
    "requires",
    "slots",
    "emits",
    "consumes",
    "permissions",
)

# 常用权限值提示（声明式权限，A 路径回退数组格式 list[str]）。
# 生成骨架默认空数组（显式授权），开发者在 README/注释指引下按需补。
_PERMISSION_HINTS = "\n".join(
    [
        "db:own            # 插件自己的表（最常用）",
        "db:read           # 读共享数据",
        "http:out          # 出站 HTTP 请求",
        "fs:read           # 读文件系统",
        "fs:write          # 写文件系统（高风险，慎用）",
        "shell:exec        # 执行命令（极高风险，默认不授）",
    ]
)


def _validate_manifest(manifest: dict[str, object]) -> list[str]:
    """生成后自检：必填字段是否齐全、格式是否符合契约。

    返回错误清单；为空即通过。错误精确到字段，方便内核强校验前先挡一道。
    """
    errs: list[str] = []

    for field in _REQUIRED_MANIFEST_FIELDS:
        if field not in manifest:
            errs.append(f"缺少必填字段：{field}")

    if "id" in manifest:
        pid = manifest["id"]
        if not isinstance(pid, str) or not _ID_RE.match(pid):
            errs.append(f"id 格式不合法：{pid!r}（小写开头，只含小写/数字，连字符作分隔）")

    if "version" in manifest and not isinstance(manifest["version"], str):
        errs.append("version 必须是字符串（三段式语义化版本，如 0.1.0）")

    if "kind" in manifest and manifest["kind"] not in ("core", "builtin", "third-party"):
        errs.append(f"kind 非法：{manifest['kind']!r}（只能是 core/builtin/third-party）")

    if "permissions" in manifest and not isinstance(manifest["permissions"], list):
        errs.append("permissions 必须是数组（A 路径回退数组格式 list[str]）")
    elif isinstance(manifest.get("permissions"), list) and not all(
        isinstance(p, str) for p in manifest["permissions"]
    ):
        errs.append("permissions 数组元素必须都是字符串（如 [\"db:own\"]）")

    return errs


def _manifest(plugin_id: str, name: str, kind: str) -> dict[str, object]:
    return {
        "id": plugin_id,
        "name": name,
        "version": "0.1.0",
        "kind": kind,
        "minKernel": "0.1.0",
        "kernelApi": "^1",
        "icon": "box",
        "description": f"{name}（由 create_plugin.py 生成，请补全 description）",
        "author": "yunxi",
        "window": {"w": 720, "h": 520},
        # 第三方插件的 web 部分由加载器统一处理，v0.1 先不挂前端入口
        "entry": "" if kind == "third-party" else f"@apps/{plugin_id}",
        "api": {
            "base": f"/api/v1/{plugin_id}",
            "openapi": f"/api/v1/{plugin_id}/openapi.json",
            "health": f"/api/v1/{plugin_id}/health",
        },
        # ★ 能力必须声明：没写进 provides 的能力，别的插件调不通
        "provides": [],
        # ★ 读别人的数据必须写进 requires，没写的调用内核直接拒
        "requires": [],
        "slots": [],
        "emits": [],
        "consumes": [],
        # ★ 声明式权限（数组格式，旧格式）：默认空数组，需显式授权
        # 新对象格式（{"filesystem": false, ...}）schema 已放行，但内核仍只收 list[str]
        # 待内核升级（B 路径）后再切对象格式，此处回退数组格式避免启动 fail
        "permissions": [],
        # ★ 2026-11-12 修复（实测：生成内置插件后内核直接 ManifestError，整个 app 起不来）：
        #   ① 内置插件的迁移/设置在**模块根**（modules/<id>/migrations、settings.schema.json），
        #      只有第三方插件才在 api/ 下；
        #   ② lifecycle 契约只允许 "hooks" 或 null，写成 {onInstall: null, ...} 是非法类型。
        "migrations": "api/migrations" if kind == "third-party" else "migrations",
        "settingsSchema": (
            "api/settings.schema.json" if kind == "third-party" else "settings.schema.json"
        ),
        "lifecycle": None,
    }


_ROUTER_PY = '''"""插件路由：{name}。

HTTP 契约（全项目统一，不许自创）：
  成功 → 直接返回资源 JSON（200/201）
  失败 → 由内核统一转成 RFC7807 application/problem+json

★ 加数据库路由时，照 `docs/示例/calendar_event_示例.py` 的写法；
  内置插件的 models.py / router.py 由内核**按包导入**
  （`importlib.import_module("modules.<id>.router")`，
  见 core/plugins/discover.py），两者之间**可以正常用 `from .models import ...` 相对导入**
  （现有全部内置插件皆如此）。
  ★「按文件路径加载、不能用相对导入」的只有两处：**迁移文件**与**第三方插件**。
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, bool]:
    """每个插件都必须有 health —— 内核据此判断"该能力是否可用"。"""
    return {{"ok": True}}
'''


_ROUTER_PY_WITH_EXAMPLE = '''"""插件路由：{name}（含跨插件调用示例，由 --with-example 生成）。

HTTP 契约（全项目统一，不许自创）：
  成功 → 直接返回资源 JSON（200/201）
  失败 → 由内核统一转成 RFC7807 application/problem+json

★ 跨插件调用（ISSUE-005 C 案参考）：
  1. 在 manifest.json 的 requires 里声明目标能力，如 ["catalog.read"]；
  2. 用 core.deps.get_plugin_client 拿客户端，再调对方 API；
  3. 对方提供的必须是 provides 里声明过的能力，否则内核拒绝。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from core.deps import get_plugin_client

router = APIRouter()


@router.get("/health")
def health() -> dict[str, bool]:
    """每个插件都必须有 health —— 内核据此判断"该能力是否可用"。"""
    return {{"ok": True}}


@router.get("/example-cross-plugin")
def example_cross_plugin(client=Depends(get_plugin_client)) -> dict:
    """跨插件调用示例：调 catalog 的 health（requires 需声明 catalog.read）。"""
    resp = client.get("/api/v1/catalog/health")
    return {{"catalog_health": resp.status_code == 200, "status": resp.status_code}}
'''


_MODELS_PY = '''"""插件模型：{name}。

★ 表名必须以插件 id 为前缀（`{prefix}_xxx`），否则内核拒绝建表。
★ 时间列一律用 `db.base.TimestampTZ`（UTC 存储 + 往返保时区）。
★ Mixin 里的字段只能用 sa_type=（用 sa_column= 会在多表继承时抛
  "Column object ... already assigned to Table"）。
"""
from __future__ import annotations

from sqlmodel import Field

from db.base import PkMixin, TimestampMixin


class {cls}(PkMixin, TimestampMixin, table=True):
    __tablename__ = "{prefix}_item"

    title: str = Field(default="", max_length=120, index=True)
'''


_MIGRATION_PY = '''"""插件迁移 0001：建 {cls} 表。

★ 只增不改：已执行过的迁移文件不许再动，要改就加 0002。
★ models.py 必须**按文件路径**加载，不能写 `from .models import ...`：
  内核是按文件路径加载插件文件的（core/plugins/discover.py），
  相对导入会抛 ImportError: attempted relative import with no known parent package。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

_MODEL_MODULE = "{prefix}_models"


def _load_models() -> Any:
    path = Path(__file__).resolve().parent.parent / "models.py"
    spec = importlib.util.spec_from_file_location(_MODEL_MODULE, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MODEL_MODULE] = mod
    spec.loader.exec_module(mod)
    return mod


def upgrade(engine: Any) -> None:
    _load_models().{cls}.__table__.create(bind=engine, checkfirst=True)


def downgrade(engine: Any) -> None:
    _load_models().{cls}.__table__.drop(bind=engine, checkfirst=True)
'''


_SETTINGS_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "插件设置",
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_WEB_TSX = '''/** {name} —— 内置插件的前端入口（T14 的加载器按 manifest.entry 加载它）。 */
export function Component() {{
  return (
    <div className="p-4">
      <h2 className="text-lg font-semibold">{name}</h2>
      <p className="text-sm opacity-70">由 create_plugin.py 生成，请把界面写在这里。</p>
    </div>
  );
}}

export default {{ Component }};
'''

_README = """# {name}（插件 id：`{plugin_id}`）

由 `scripts/create_plugin.py` 生成。**请先读 `docs/adr/0002-插件协议.md`。**

| 项 | 值 |
|:--|:--|
| id | `{plugin_id}` |
| kind | `{kind}` |
| API 前缀 | `/api/v1/{plugin_id}` |
| 表名前缀 | `{prefix}_` |
| 迁移目录 | `api/migrations/` |

## 改这个插件时不许做的事

1. 不许 import 别的插件 —— 走事件总线 / 对方公开 API / 扩展点
2. 不许 join 别人的表 —— 要数据就调对方的 API，并在 manifest.requires 里声明
3. 不许自己写登录 —— 内核统一注入 `Authorization: Bearer <jwt>`
4. 不许写死颜色 —— 只用设计令牌（`var(--accent)` 之类）
5. 不许改 `kernel/` 或 `core/` —— 那是框架的 bug，去 `docs/issues/` 写卡
"""


def _write(path: Path, content: str) -> None:
    """写文件。

    ★ `newline="\\n"` 不是可有可无的：Windows 上 `Path.write_text()` 会把 `\\n`
    翻译成 CRLF，而本仓库用 `.gitattributes` 强制 LF（`* text=auto eol=lf`），
    prettier 也是 `endOfLine: lf`。少了这个参数，生成出来的每个文件都会让
    `make lint` 的 `prettier --check` 报错 —— 即"按官方入口建插件，verify 必红"。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def create(
    plugin_id: str, name: str, kind: str, force: bool, with_example: bool = False
) -> int:
    if not _ID_RE.match(plugin_id) or not (_ID_MIN <= len(plugin_id) <= _ID_MAX):
        print(f"❌ id 不合法：{plugin_id!r}")
        print(
            f"   要求：小写字母开头，只含小写字母/数字，连字符仅作分隔且不连续，"
            f"{_ID_MIN}~{_ID_MAX} 字符。例如 calendar / my-notes / habit-log"
        )
        return 2

    target = (
        _PLUGINS_DIR / plugin_id if kind == "third-party" else _BUILTIN_API_DIR / plugin_id
    )

    if target.exists():
        if not force:
            print(f"❌ 已存在：{target}")
            print("   要覆盖请加 --force（会先删掉该目录）")
            return 3
        shutil.rmtree(target)

    prefix = _table_prefix(plugin_id)
    cls = "".join(part.capitalize() for part in re.split(r"[-_]", plugin_id)) + "Item"
    fmt = {"plugin_id": plugin_id, "name": name, "kind": kind, "prefix": prefix, "cls": cls}

    # ── manifest：生成后自检（必填字段校验，错误精确到字段）──
    manifest = _manifest(plugin_id, name, kind)
    errs = _validate_manifest(manifest)
    if errs:
        for err in errs:
            print(f"❌ manifest 校验失败：{err}")
        return 2

    # ── 写骨架 ──
    router_tpl = _ROUTER_PY_WITH_EXAMPLE if with_example else _ROUTER_PY

    # ── manifest ──
    target.mkdir(parents=True, exist_ok=True)
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _write(target / "README.md", _README.format(**fmt))

    if kind == "third-party":
        _write(target / "api" / "router.py", router_tpl.format(**fmt))
        _write(target / "api" / "models.py", _MODELS_PY.format(**fmt))
        _write(
            target / "api" / "settings.schema.json",
            json.dumps(_SETTINGS_SCHEMA, ensure_ascii=False, indent=2) + "\n",
        )
        _write(target / "api" / "migrations" / "0001_init.py", _MIGRATION_PY.format(**fmt))
        _write(target / "web" / "index.tsx", _WEB_TSX.format(**fmt))
    else:
        # 内置插件：后端在 services/api/modules/<id>/，前端在 apps/web/src/apps/<id>/
        _write(target / "router.py", router_tpl.format(**fmt))
        _write(target / "models.py", _MODELS_PY.format(**fmt))
        _write(
            target / "settings.schema.json",
            json.dumps(_SETTINGS_SCHEMA, ensure_ascii=False, indent=2) + "\n",
        )
        _write(target / "migrations" / "0001_init.py", _MIGRATION_PY.format(**fmt))
        _write(_BUILTIN_WEB_DIR / plugin_id / "index.tsx", _WEB_TSX.format(**fmt))

    print(f"✅ 已生成插件骨架：{target}")
    if with_example:
        print("（含跨插件调用示例，requires 记得声明目标能力）")
    if kind != "third-party":
        print(f"   前端入口：{_BUILTIN_WEB_DIR / plugin_id / 'index.tsx'}")
    print()
    print("下一步：")
    print("  1. 补全 manifest.json 里的 description / provides / requires / slots / permissions")
    print(f"     permissions 常用值：{_PERMISSION_HINTS}")
    print(f"  2. 表名一律用 {prefix}_ 前缀，时间列用 db.base.TimestampTZ")
    print("  3. 跑 python -m pytest tests/test_plugins.py -q 确认框架能接受它")
    print("  4. 起服务后它会自动出现在 /api/v1/plugins 与 /api/docs")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        print('用法：python scripts/create_plugin.py <id> "<名称>" \\')
        print("        [--kind third-party|builtin] [--force] [--with-example]")
        return 2

    plugin_id, name = args[0].strip(), args[1].strip()
    kind = "third-party"
    force = False
    with_example = False
    rest = args[2:]
    i = 0
    while i < len(rest):
        if rest[i] == "--kind" and i + 1 < len(rest):
            kind = rest[i + 1]
            i += 2
            continue
        if rest[i] == "--force":
            force = True
            i += 1
            continue
        if rest[i] == "--with-example":
            with_example = True
            i += 1
            continue
        print(f"❌ 未知参数：{rest[i]}")
        return 2

    if kind not in ("third-party", "builtin"):
        print(f"❌ --kind 只能是 third-party 或 builtin，收到 {kind!r}")
        return 2

    return create(plugin_id, name, kind, force, with_example)


if __name__ == "__main__":
    sys.exit(main())
