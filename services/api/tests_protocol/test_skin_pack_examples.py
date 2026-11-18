"""皮肤包示例的守卫：`docs/示例/skins/*.skin.json` 必须全部能被导入。

★ 为什么要有这条测试
--------------------
规范文档会漂，**能被导入的真包不会**。仓库里放的示例皮肤如果哪天因为
schema 变更（新增必填令牌、改了亮度单调判据）而导不进去，用户照着示例写
就会一头雾水——而没有人会因为「文档示例过期」去开 bug。

⇒ 把示例包当**测试夹具**：每个 `*.skin.json` 都要过一遍运行时同一套校验。
   校验器只有一个（apps/web/src/shared/styles/skin.ts 的 validateSkinPack），
   这里用 Node 跑它，避免在 Python 里复刻第二份判据（复刻必然漂）。

用法（由 pytest 调用，也可单独跑）：
    python -m pytest services/api/tests_protocol/test_skin_pack_examples.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_SKINS_DIR = _REPO / "docs" / "示例" / "skins"
_WEB = _REPO / "apps" / "web"

#: 跑一次 Node 校验器（用仓库里已有的 vitest 生态，不引新依赖）
_RUNNER = """
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { validateSkinPack } from "./src/shared/styles/skin.ts";

const dir = process.argv[2];
const files = readdirSync(dir).filter((f) => f.endsWith(".skin.json"));
const out = [];
for (const f of files) {
  const raw = JSON.parse(readFileSync(join(dir, f), "utf8"));
  const candidate = raw && typeof raw === "object" && "skin" in raw ? raw.skin : raw;
  const r = validateSkinPack(candidate);
  out.push({ file: f, ok: r.ok, errors: r.errors, warnings: r.warnings });
}
console.log(JSON.stringify(out));
"""


def _node_bin() -> str | None:
    return shutil.which("node")


def _validate_all() -> list[dict[str, object]]:
    node = _node_bin()
    if not node:
        pytest.skip("环境里没有 node，跳过皮肤包校验（CI 与开发机都有，本地裸环境才没有）")
    runner = _WEB / ".__skin_pack_check.mjs"
    runner.write_text(_RUNNER, encoding="utf-8")
    try:
        proc = subprocess.run(  # noqa: S603 - 固定命令 + 仓库内临时脚本
            [node, "--experimental-strip-types", str(runner), str(_SKINS_DIR)],
            cwd=str(_WEB),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    finally:
        runner.unlink(missing_ok=True)
    assert proc.returncode == 0, f"校验器自身跑挂：{proc.stderr[:800]}"
    return json.loads(proc.stdout)


def test_skins_dir_exists_with_examples() -> None:
    assert _SKINS_DIR.is_dir(), f"示例皮肤目录不存在：{_SKINS_DIR}"
    packs = sorted(_SKINS_DIR.glob("*.skin.json"))
    assert len(packs) >= 3, f"示例皮肤太少（{len(packs)} 个），README 里承诺了 3 个"


def test_every_example_pack_passes_validation() -> None:
    """每个示例包都必须能通过运行时校验 —— 这是「示例不会撒谎」的唯一保证。"""
    results = _validate_all()
    assert results, "一个包都没读到"
    bad = [r for r in results if not r["ok"]]
    detail = "\n".join(f"  {r['file']}: {r['errors']}" for r in bad)
    assert not bad, f"示例皮肤包导不进去（用户照抄就会踩坑）：\n{detail}"


def test_examples_are_small_enough_to_share() -> None:
    """分享用的包应当很小（<= 16KB）；超了说明有人往里塞了不该塞的东西。"""
    for p in sorted(_SKINS_DIR.glob("*.skin.json")):
        size = p.stat().st_size
        assert size <= 16 * 1024, f"{p.name} 有 {size} 字节，超过 16KB 的分享上限"


def test_examples_cover_three_shapes() -> None:
    """示例必须覆盖三种用法形态 —— 否则「示例集合」会退化成同一个写法的三个副本。

    ① 最小形态：只给 theme（强调色 + 圆角），其余沿用令牌默认
    ② 双分区：theme + dark/light 各自覆盖（跟随主题切换）
    ③ 整套表面：在 theme 里换掉全部表面层级 + 线 + 文字（一次性定义整套外观）
    """
    shapes: set[str] = set()
    for p in sorted(_SKINS_DIR.glob("*.skin.json")):
        skin = json.loads(p.read_text(encoding="utf-8"))["skin"]
        tokens = skin["tokens"]
        theme = tokens.get("theme", {})
        if theme.keys() >= {"--bg", "--surface", "--surface-2", "--surface-3", "--txt", "--line"}:
            shapes.add("full-surface")
        elif tokens.get("dark") or tokens.get("light"):
            shapes.add("zoned")
        elif theme:
            shapes.add("minimal")
    missing = {"minimal", "zoned", "full-surface"} - shapes
    assert not missing, f"示例缺这些形态：{missing}（现有 {shapes}）"
