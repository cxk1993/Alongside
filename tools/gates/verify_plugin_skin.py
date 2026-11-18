#!/usr/bin/env python3
"""门禁 G20 · 皮肤插件契约：`manifest.skin` 指针必须指向一个**真能导入**的皮肤文件。

★ 为什么需要这道门禁
--------------------
`contracts/plugin.schema.json` 允许插件用 `skin` 声明「本插件带一套皮肤」，
但 schema 只能判它是「一个不以 / 开头、不带 ..、以 .json 结尾的字符串」——
**它指向的文件是否存在、有多大、里面的令牌是否自洽，schema 一个字都管不了**。
于是会出现三种静默失败：

  · 指针写对了形状、写错了名字（`skin.json` vs `skins/default.json`）→
    界面上一片默认外观，插件作者以为「皮肤没生效」而没人知道是文件缺了；
  · 皮肤文件里塞了一张 5MB 的封面图 base64 → 导入时把 localStorage 塞爆，
    整个皮肤库连带陪葬（库的单条上限是 64KB）；
  · 皮肤文件里写了 `var(--x)` / `url(...)` / `}` → 那是从「一份变量表」
    扩权到「能引入外部资源、能注入新声明」的口子，运行时一律拒收，
    但代码里拒收等于「用户点导入后看到一句报错」——作者发版前就该知道。

⇒ 判据必须来自**运行时那一个校验器**，不是文档、也不是在 Python 里复刻第二份：
    apps/web/src/shared/styles/skin.ts 的 validateSkinPack()
   本门禁写一个临时 .mjs 让 Node 直接 import 这个 .ts 跑它
   （做法照抄 services/api/tests_protocol/test_skin_pack_examples.py：
    `node --experimental-strip-types` + 在 apps/web 里放临时脚本，不引新依赖）。
   复刻判据必然漂 —— 运行时改了「必须给 --bg/--txt」，复刻版还绿着，门禁就成了伪证。

★ 逐插件的判据（顺序即报告顺序）
   1. `plugins/*/manifest.json`（**只扫一层**，不进插件内部找别的清单）。
      没有 `skin` 字段 ⇒ 跳过，不算违规；
   2. 路径安全：不以 `/` 开头、不含 `..` 段、以 `.json` 结尾；
      另外解析后必须仍落在插件目录内（防 `C:/x.json` 这类平台绝对路径）；
   3. 目标文件存在（相对插件目录解析）；
   4. 大小 ≤ 65536 字节（与 apps/web/src/shared/styles/skin-library.ts 的
      单条上限 64 * 1024 同一口径）；
   5. 内容过 `validateSkinPack()`（Node 跑运行时同一份代码）。

★ 反空转（本门禁最重要的一条）
   一个带 skin 的插件都没有 ⇒ **判红**。理由：门禁空转等于没有门禁。
   今天仓库里还没有插件带皮肤，所以这道门禁默认就是红的 —— 这是刻意的：
   它逼着「第一个带皮肤的插件」出现时，有人真的跑过一次这条判据。
   只想看报告（巡检 / 灰度期）用 `--report`，那时永远 0。

★ 退出码
   0 = 全部通过（或 --report 模式）
   1 = 有阻断项（含「零皮肤插件」的空转判定）
   2 = 参数错误

用法：
    python tools/gates/verify_plugin_skin.py            # 拦
    python tools/gates/verify_plugin_skin.py --report   # 只报告，永远 0
    python tools/gates/verify_plugin_skin.py --strict   # 校验器的 warning 也算阻断
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent.parent
_REPO = _TOOLS.parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from paths import REPO, WEB  # noqa: E402

#: 第三方插件根（`plugins/`）—— 只扫这一层的 `*/manifest.json`。
PLUGINS_DIR: Path = REPO / "plugins"

#: 前端根（`apps/web/`）—— Node 校验器要在这里跑，才能 import 到 src 下的 .ts。
WEB_DIR: Path = WEB

#: 运行时校验器的相对位置（相对 `apps/web/`）。★ 换路径就等于换了判据，必须同步改这里。
SKIN_MODULE = "src/shared/styles/skin.ts"

#: 皮肤文件大小上限，与 skin-library.ts 的 `MAX_ITEM_BYTES = 64 * 1024` 同一口径。
MAX_SKIN_BYTES = 64 * 1024

#: Node 侧跑一次运行时校验器：读 argv 里的每个文件 → 喂给 validateSkinPack。
#: 包络（`{alongsideSkin:1, skin:{...}}`）与裸皮肤两种写法都接受，
#: 解包规则与 test_skin_pack_examples.py 逐字一致（同一个导入侧语义）。
_NODE_RUNNER = """
import { readFileSync } from "node:fs";
import { validateSkinPack } from "./src/shared/styles/skin.ts";

const out = [];
for (const file of process.argv.slice(2)) {
  let raw;
  try {
    raw = JSON.parse(readFileSync(file, "utf8"));
  } catch (e) {
    out.push({ file, ok: false, errors: [`JSON 解析失败：${e.message}`], warnings: [] });
    continue;
  }
  const candidate = raw && typeof raw === "object" && "skin" in raw ? raw.skin : raw;
  try {
    const r = validateSkinPack(candidate);
    out.push({ file, ok: Boolean(r.ok), errors: r.errors ?? [], warnings: r.warnings ?? [] });
  } catch (e) {
    out.push({ file, ok: false, errors: [`校验器抛错：${e.message}`], warnings: [] });
  }
}
console.log(JSON.stringify(out));
"""


@dataclass
class Report:
    ok: bool = True
    scanned: int = 0
    with_skin: int = 0
    skipped: list[str] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)
    #: (manifest 相对路径, 结论)
    findings: list[tuple[str, str]] = field(default_factory=list)
    #: 空转判定（零皮肤插件）—— 单独记，因为它不是某个插件的错。
    idle: str = ""

    def block(self, manifest: str, reason: str) -> None:
        self.findings.append((manifest, reason))

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "scanned": self.scanned,
            "with_skin": self.with_skin,
            "skipped": self.skipped,
            "passed": self.passed,
            "findings": [{"manifest": m, "reason": r} for m, r in self.findings],
            "idle": self.idle,
        }


def _rel(path: Path) -> str:
    """报告里统一用相对仓库根的 POSIX 路径；测试造在临时目录时退回绝对路径。"""
    try:
        return path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return str(path)


def iter_manifests() -> list[Path]:
    """`plugins/*/manifest.json` —— **只 glob 一层**，不进插件内部找别的清单。"""
    if not PLUGINS_DIR.is_dir():
        return []
    return sorted(p for p in PLUGINS_DIR.glob("*/manifest.json") if p.is_file())


def pointer_unsafe(pointer: str) -> str | None:
    """路径安全判据；安全返回 None，否则返回中文原因。

    与 `contracts/plugin.schema.json` 里 `skin` 的 pattern 同一口径：
    不许绝对路径、不许 `..` 段、必须 `.json` 结尾（`entry` 的 /plugin-assets
    穿越防护是同一套想法）。
    """
    if pointer.startswith("/"):
        return "路径不许以 / 开头（那会跳到插件目录之外）"
    segments = pointer.replace("\\", "/").split("/")
    if ".." in segments:
        return "路径不许含 .. 段（会跳出插件目录）"
    if not pointer.endswith(".json"):
        return "路径必须以 .json 结尾"
    return None


def _node_bin() -> str | None:
    return shutil.which("node")


def _validate_with_node(paths: list[Path]) -> dict[str, dict[str, object]]:
    """跑一次运行时校验器（Node），返回 {文件绝对路径: 结果}。

    ★ 没有 node 就直接判红（返回空字典由调用方拦），因为「装不上校验器」
      与「校验通过」在报告里长得一样 —— 静默放过比报错更糟。
    """
    node = _node_bin()
    if not node:
        return {}
    fd, runner_name = tempfile.mkstemp(
        prefix=".__plugin_skin_check_", suffix=".mjs", dir=str(WEB_DIR)
    )
    runner = Path(runner_name)
    try:
        with open(fd, "w", encoding="utf-8") as fh:
            fh.write(_NODE_RUNNER)
        proc = subprocess.run(  # noqa: S603 - 固定命令 + 仓库内临时脚本 + 明确文件清单
            [node, "--experimental-strip-types", str(runner), *[str(p) for p in paths]],
            cwd=str(WEB_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    finally:
        runner.unlink(missing_ok=True)

    if proc.returncode != 0:
        raise RuntimeError(f"Node 校验器跑挂（退出码 {proc.returncode}）：{proc.stderr[:800]}")

    payload = None
    for line in reversed(proc.stdout.splitlines()):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            payload = stripped
            break
    if payload is None:
        raise RuntimeError(f"Node 校验器没有输出结果：{proc.stdout[:500]} / {proc.stderr[:500]}")

    out: dict[str, dict[str, object]] = {}
    for item in json.loads(payload):
        out[str(Path(str(item["file"])).resolve())] = item
    return out


def check(report: Report, strict: bool = False) -> None:
    manifests = iter_manifests()
    report.scanned = len(manifests)

    # ① 先做纯静态判据（路径 / 存在 / 大小），把能进 Node 的文件攒起来。
    candidates: list[tuple[Path, Path, str]] = []  # (manifest, 皮肤文件, 插件 id)
    for manifest in manifests:
        plugin_dir = manifest.parent
        rel_manifest = _rel(manifest)
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 - 坏 manifest 本身就是门禁要抓的
            report.block(rel_manifest, f"manifest 读不出来：{e}")
            continue
        if not isinstance(data, dict):
            report.block(rel_manifest, "manifest 不是对象")
            continue

        plugin_id = str(data.get("id") or plugin_dir.name)
        if "skin" not in data:
            report.skipped.append(plugin_id)
            continue

        report.with_skin += 1
        pointer = data["skin"]
        if not isinstance(pointer, str) or not pointer.strip():
            report.block(rel_manifest, f"skin 必须是非空字符串，收到 {pointer!r}")
            continue
        unsafe = pointer_unsafe(pointer)
        if unsafe:
            report.block(rel_manifest, f"skin={pointer!r} → {unsafe}")
            continue

        target = (plugin_dir / pointer).resolve()
        try:
            target.relative_to(plugin_dir.resolve())
        except ValueError:
            report.block(rel_manifest, f"skin={pointer!r} → 解析后落在插件目录之外（{target}）")
            continue
        if not target.is_file():
            report.block(rel_manifest, f"skin={pointer!r} → 文件不存在：{_rel(target)}")
            continue
        size = target.stat().st_size
        if size > MAX_SKIN_BYTES:
            report.block(
                rel_manifest,
                f"skin={pointer!r} → 文件 {size} 字节，超过 {MAX_SKIN_BYTES} 字节上限"
                "（与皮肤库单条上限同一口径）",
            )
            continue
        candidates.append((manifest, target, plugin_id))

    # ② 剩下的交给运行时那一个校验器（Node 跑 skin.ts）。
    if candidates:
        results = _validate_with_node([target for _, target, _ in candidates])
        if not results:
            report.block(
                _rel(PLUGINS_DIR),
                "环境里没有 node，跑不了运行时校验器（装不上校验器 ≠ 校验通过，直接判红）",
            )
            return
        for manifest, target, plugin_id in candidates:
            rel_manifest = _rel(manifest)
            res = results.get(str(target))
            if res is None:
                report.block(rel_manifest, f"校验器没有返回 {_rel(target)} 的结果")
                continue
            raw_errors = res.get("errors")
            raw_warnings = res.get("warnings")
            errors = [str(e) for e in raw_errors] if isinstance(raw_errors, list) else []
            warnings = [str(w) for w in raw_warnings] if isinstance(raw_warnings, list) else []
            if errors:
                report.block(
                    rel_manifest,
                    f"{_rel(target)} 过不了 validateSkinPack：" + "；".join(errors[:4]),
                )
                continue
            if strict and warnings:
                report.block(
                    rel_manifest,
                    f"{_rel(target)} 有 {len(warnings)} 条警告（--strict 视为阻断）："
                    + "；".join(warnings[:4]),
                )
                continue
            note = f"{_rel(target)}（{target.stat().st_size} 字节"
            note += f"，{len(warnings)} 条警告）" if warnings else "）"
            report.passed.append(f"{plugin_id} → {note}")

    # ③ 反空转：没有任何插件带皮肤 ⇒ 门禁空转，判红。
    if report.with_skin == 0:
        report.idle = (
            "零皮肤插件：没有插件声明 skin 字段，这道门禁在空转"
            "（空转的门禁等于没有门禁；--report 只报告）"
        )

    report.ok = not report.findings and not report.idle


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="G20 · manifest.skin 必须指向一个真能导入的皮肤文件")
    ap.add_argument("--report", action="store_true", help="只报告不拦（永远 0，供巡检用）")
    ap.add_argument(
        "--strict",
        action="store_true",
        help="把运行时校验器的 warning（未知令牌 / 缺 meta.name 等）也算阻断",
    )
    args = ap.parse_args(argv)

    report = Report()
    try:
        check(report, strict=args.strict)
    except RuntimeError as e:
        print(f"✗ {e}")
        return 0 if args.report else 1

    for plugin_id in report.skipped:
        print(f"— {plugin_id}：无 skin 字段，跳过")
    for manifest, reason in report.findings:
        print(f"✗ {manifest}：{reason}")
    for line in report.passed:
        print(f"✓ {line}")
    if report.idle:
        print(f"✗ {report.idle}")
    blocked = len(report.findings) + (1 if report.idle else 0)
    print(f"扫描 {report.scanned} 个插件，{report.with_skin} 个带皮肤，{blocked} 项阻断")

    if args.report:
        return 0
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
