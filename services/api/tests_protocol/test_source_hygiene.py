"""源码卫生门禁（`tools/gates/verify_source_hygiene.py`）的自检测试。

★ 为什么门禁要有自己的测试：
  一道一直报绿的门禁，看起来和「没有问题」完全一样。
  所以这里三类判据缺一不可：
    ① 干净样本必须绿（门禁能跑、判据没写反）；
    ② 注入每种问题，门禁必须各自报红/报告（门禁抓得住）；
    ③ 已知的**误报源**必须不报（正则字面量 / 注释 / url(#id) / 令牌文件）——
       不测这一条，门禁会被人当成噪音关掉。

★ 测试不跑子进程、不碰真实仓库：全部在 `tmp_path` 里造样本，
  直接调脚本的 `scan()` / `decide()`。期望值是**人写的**，
  不是拿门禁输出回填的（后者是自证，永远绿）。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

#: 仓库根。合入后测试在 `services/api/tests_protocol/` ⇒ parents[3] 就是仓库根；
#: 提案阶段测试与脚本同目录、不在仓库里，此时向上找不到 `apps/web/src`，
#: 允许用 `ALONGSIDE_HYGIENE_REPO` 显式指定（CI / 评审用），否则相关测试跳过。
_GATE_REL = Path("tools") / "gates" / "verify_source_hygiene.py"


def _find_repo() -> Path:
    env = os.environ.get("ALONGSIDE_HYGIENE_REPO")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "apps" / "web" / "package.json").is_file():
            return parent
    return here.parents[3]


_REPO = _find_repo()


def _find_gate() -> Path:
    """定位门禁脚本：先看同目录，再按仓库结构找。

    ★ 同一个测试要在两种布局下都能跑：
      · 提案阶段 —— 测试与脚本都躺在 `gates-proposal/`（不碰仓库）；
      · 合入之后 —— 脚本在 `tools/gates/`，测试在 `services/api/tests_protocol/`。
    只认一种布局的测试，会在搬家那天变成「测试找不到门禁」而不是「门禁坏了」。
    """
    local = Path(__file__).resolve().parent / "verify_source_hygiene.py"
    if local.is_file():
        return local
    repo_level = _REPO / _GATE_REL
    assert repo_level.is_file(), f"找不到门禁脚本（试过 {local} 与 {repo_level}）"
    return repo_level


GATE = _find_gate()


def _load():
    spec = importlib.util.spec_from_file_location("_source_hygiene_gate", GATE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # ★ 必须先登记进 sys.modules 再 exec：
    #   脚本用了 `from __future__ import annotations`，dataclass 会在 exec 时反查
    #   `sys.modules[cls.__module__]` 解析字段注解。不登记就是 KeyError/NoneType，
    #   而报错位置在 dataclasses 内部——看起来像门禁坏了，其实只是加载姿势不对。
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _mkroot(tmp_path: Path) -> Path:
    """造一个「形状对但内容空」的树：apps/web/src 必须在，否则门禁该报「根不存在」。"""
    root = tmp_path / "repo"
    (root / "apps" / "web" / "src").mkdir(parents=True)
    return root


def _write(root: Path, rel: str, text: str, *, bom: bool = False) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    path.write_bytes((b"\xef\xbb\xbf" + data) if bom else data)
    return path


# ─────────────────────────── ① 干净样本必须绿 ───────────────────────────


def test_clean_tree_passes(tmp_path: Path) -> None:
    gate = _load()
    root = _mkroot(tmp_path)
    _write(root, "apps/web/src/App.tsx", 'export const A = () => <div className="x" />;\n')
    _write(root, "apps/web/src/styles.css", ".x { color: var(--fg); }\n")

    result = gate.scan(root)
    assert result["scanned"]["files"] == 2
    assert result["bom"] == []
    assert result["hex"] == []
    assert gate.decide(result, strict=True) == 0


def test_tokens_css_hex_is_allowed(tmp_path: Path) -> None:
    """令牌文件是颜色的唯一来源：它写 hex 是本职，不是违规。"""
    gate = _load()
    root = _mkroot(tmp_path)
    _write(root, "apps/web/src/shared/styles/tokens.css", ":root { --bg: #0e1116; --fg: #e6edf5; }\n")

    result = gate.scan(root)
    assert result["hex"] == [], f"令牌文件被误报：{result['hex']}"
    assert result["scanned"]["hex_exempt_files"] == ["apps/web/src/shared/styles/tokens.css"]
    assert gate.decide(result, strict=True) == 0


def test_allow_glob_exempts_hex(tmp_path: Path) -> None:
    gate = _load()
    root = _mkroot(tmp_path)
    _write(root, "apps/web/src/chart/palette.ts", 'export const C = ["#5b8ff9"];\n')

    result = gate.scan(root, allow=("apps/web/src/chart/*",))
    assert result["hex"] == []
    assert gate.decide(result, strict=True) == 0


# ─────────────────────────── ② 注入问题必须被抓 ───────────────────────────


def test_bom_blocks(tmp_path: Path) -> None:
    gate = _load()
    root = _mkroot(tmp_path)
    _write(root, "apps/web/src/apps/appearance/index.tsx", "export const A = 1;\n", bom=True)

    result = gate.scan(root)
    assert len(result["bom"]) == 1
    assert result["bom"][0]["path"] == "apps/web/src/apps/appearance/index.tsx"
    assert gate.decide(result, strict=False) == 1, "BOM 必须 blocking，即使没开 --strict"


def test_bom_in_tokens_css_also_blocks(tmp_path: Path) -> None:
    """★ hex 豁免不等于文件豁免：令牌文件带 BOM 一样会让工具静默跳文件。"""
    gate = _load()
    root = _mkroot(tmp_path)
    _write(root, "apps/web/src/shared/styles/tokens.css", ":root { --bg: #0e1116; }\n", bom=True)

    result = gate.scan(root)
    assert len(result["bom"]) == 1
    assert gate.decide(result, strict=False) == 1


def test_component_hex_is_reported_not_blocking_by_default(tmp_path: Path) -> None:
    gate = _load()
    root = _mkroot(tmp_path)
    _write(root, "apps/web/src/apps/catalog/catalog.css", ".tag { background: #4caf50; }\n")

    result = gate.scan(root)
    assert len(result["hex"]) == 1
    assert result["hex"][0]["line"] == 1
    assert "#4caf50" in result["hex"][0]["detail"]
    assert gate.decide(result, strict=False) == 0, "存量没清完，默认不该拦"
    assert gate.decide(result, strict=True) == 1, "--strict 必须拦"


def test_hex_in_jsx_string_and_css_fallback_are_both_reported(tmp_path: Path) -> None:
    gate = _load()
    root = _mkroot(tmp_path)
    _write(
        root,
        "apps/web/src/apps/isdu/index.tsx",
        'const PALETTE = ["#5b8ff9", "#61ddaa"];\n'
        "export const A = () => <div style={{ color: '#f6bd16' }} />;\n",
    )
    _write(root, "apps/web/src/a.css", ".x { color: var(--accent, #4caf50); }\n")

    result = gate.scan(root)
    assert len(result["hex"]) == 4, result["hex"]
    assert len(result["hex_files"]) == 2


def test_plugin_web_src_is_scanned(tmp_path: Path) -> None:
    """第三方插件与前端同构：插件的写死色值一样要报。"""
    gate = _load()
    root = _mkroot(tmp_path)
    _write(root, "plugins/demo/web/src/index.tsx", "const C = '#123abc';\n")

    result = gate.scan(root)
    assert [h["path"] for h in result["hex"]] == ["plugins/demo/web/src/index.tsx"]


def test_test_files_are_skipped_by_default(tmp_path: Path) -> None:
    """夹具里的颜色值是数据，不是设计令牌——默认不查，--include-tests 才查。"""
    gate = _load()
    root = _mkroot(tmp_path)
    _write(root, "apps/web/src/a.test.ts", 'ev({ color: "#ff6600" });\n')
    _write(root, "apps/web/src/__tests__/b.tsx", 'const c = "#ff6600";\n')

    result = gate.scan(root)
    assert result["scanned"]["test_files_skipped"] == 2
    assert result["hex"] == []

    strict_result = gate.scan(root, include_tests=True)
    assert len(strict_result["hex"]) == 2
    assert strict_result["scanned"]["test_files_skipped"] == 0


def test_node_modules_and_dist_are_pruned(tmp_path: Path) -> None:
    """内存红线：不存在的目录不该被走进去，也不该被报成 BOM。"""
    gate = _load()
    root = _mkroot(tmp_path)
    _write(root, "apps/web/src/node_modules/x/index.ts", 'const c = "#ff0000";\n', bom=True)
    _write(root, "apps/web/src/dist/bundle.css", ".x { color: #ff0000; }\n")

    result = gate.scan(root)
    assert result["scanned"]["files"] == 0, "进了 node_modules / dist"
    assert result["bom"] == []
    assert result["hex"] == []


# ────────────────────── ③ 已知误报源必须不报 ──────────────────────


def test_regex_literal_lines_are_not_hex(tmp_path: Path) -> None:
    """正则里出现的 `#rrggbb` 是判据本身，不是色值。"""
    gate = _load()
    root = _mkroot(tmp_path)
    _write(
        root,
        # ★ 刻意不叫 skin.ts / wallpaperApply.tsx —— 那是内置豁免文件（有理由的例外），
        #   拿它们当样本等于在豁免区里测规则，测不出东西。
        "apps/web/src/apps/demo/regex-sample.ts",
        "const HEX6 = /^#[0-9a-f]{6}$/i;\n"
        "export const isHex = (s: string) => HEX6.test(s);\n"
        "export const strip = (s: string) => s.replace(/#5b8cff/g, '');\n"
        "export const re = new RegExp('^#5b8cff$');\n"
        "const m = 'x'.match(/#4caf50/);\n",
    )

    result = gate.scan(root)
    assert result["hex"] == [], f"正则行被误报：{result['hex']}"
    assert result["suppressed"]["regex"] >= 3, result["suppressed"]


def test_comment_hex_is_not_reported(tmp_path: Path) -> None:
    gate = _load()
    root = _mkroot(tmp_path)
    _write(
        root,
        "apps/web/src/a.css",
        "/* 示例：#5b8cff 是强调色 */\n"
        ".x { color: var(--accent); } /* 旧的 #ff0000 */\n"
        ".y { color: var(--fg); }\n",
    )
    _write(
        root,
        "apps/web/src/b.ts",
        "// 不要写 #ffffff\n"
        "/**\n * 也不要写 #000000\n */\n"
        "export const A = 1;\n",
    )

    result = gate.scan(root)
    assert result["hex"] == [], f"注释被误报：{result['hex']}"
    assert result["suppressed"]["comment"] >= 4


def test_comment_at_end_of_code_line_does_not_hide_code_hex(tmp_path: Path) -> None:
    """★ 屏蔽注释不能顺手把整行丢掉：代码里的色值在同一行后面跟着注释时仍要报。"""
    gate = _load()
    root = _mkroot(tmp_path)
    _write(root, "apps/web/src/a.ts", 'export const c = "#5b8cff"; // 主色\n')

    result = gate.scan(root)
    assert len(result["hex"]) == 1
    assert result["hex"][0]["line"] == 1


def test_url_fragment_ids_are_not_hex(tmp_path: Path) -> None:
    """`url(#ffffff)` 是 SVG 片段 id，不是色值（三位 id 由 --include-short-hex 放大搜时才出现）。"""
    gate = _load()
    root = _mkroot(tmp_path)
    _write(root, "apps/web/src/a.css", ".x { fill: url(#ffffff); }\n")

    result = gate.scan(root)
    assert result["hex"] == []
    assert result["suppressed"]["url"] == 1, result["suppressed"]

    _write(root, "apps/web/src/b.css", ".y { fill: url(#abc); }\n")
    short = gate.scan(root, include_short_hex=True)
    assert short["hex"] == [], short["hex"]
    assert short["suppressed"]["url"] == 2, short["suppressed"]


def test_string_with_slashes_does_not_swallow_rest_of_line(tmp_path: Path) -> None:
    """`"https://…"` 里的 `//` 不是注释开头——否则同行的色值会被静默吞掉。"""
    gate = _load()
    root = _mkroot(tmp_path)
    _write(
        root,
        "apps/web/src/a.ts",
        'const u = "https://example.com/x"; const c = "#5b8cff";\n',
    )

    result = gate.scan(root)
    assert len(result["hex"]) == 1, result["hex"]


def test_css_protocol_relative_url_is_not_a_comment(tmp_path: Path) -> None:
    """CSS 没有 `//` 行注释：`url(//cdn/a.png)` 后面的色值不该被吞掉。"""
    gate = _load()
    root = _mkroot(tmp_path)
    _write(
        root,
        "apps/web/src/a.css",
        ".x { background: url(//cdn.example.com/a.png); color: #4caf50; }\n",
    )

    result = gate.scan(root)
    assert len(result["hex"]) == 1, result["hex"]


def test_short_hex_only_with_flag(tmp_path: Path) -> None:
    gate = _load()
    root = _mkroot(tmp_path)
    _write(root, "apps/web/src/a.css", ".x { color: #fff; }\n")

    assert gate.scan(root)["hex"] == []
    assert len(gate.scan(root, include_short_hex=True)["hex"]) == 1


def test_six_hex_is_not_double_counted_as_short_hex(tmp_path: Path) -> None:
    gate = _load()
    root = _mkroot(tmp_path)
    _write(root, "apps/web/src/a.css", ".x { color: #ffffff; }\n")

    result = gate.scan(root, include_short_hex=True)
    assert len(result["hex"]) == 1, result["hex"]


# ───────────────────────── 判据的空转防护 ─────────────────────────


def test_missing_base_root_fails(tmp_path: Path) -> None:
    """根不存在 ⇒ 不是「没问题」，是「没查到」——必须报红。"""
    gate = _load()
    empty = tmp_path / "not-a-repo"
    empty.mkdir()

    result = gate.scan(empty)
    assert result["zero_scan"] is True
    assert result["missing_roots"] == ["apps/web/src"]
    assert gate.decide(result, strict=False) == 1


def test_empty_but_present_root_fails(tmp_path: Path) -> None:
    """扫到 0 个文件同样是假绿：门禁检查的目录存在，但里面没有可查的东西。"""
    gate = _load()
    root = _mkroot(tmp_path)

    result = gate.scan(root)
    assert result["scanned"]["files"] == 0
    assert result["zero_scan"] is True
    assert gate.decide(result, strict=False) == 1


def test_scan_is_line_streaming(tmp_path: Path) -> None:
    """★ 内存红线：逐行产出，不把文件读成一个 bytes/str。

    判据：给一个远大于「一次读 64KB 缓冲」的文件，`scan()` 仍只返回聚合结果，
    且 `lines` 计数精确到行——整文件读入的实现在大文件上会明显更慢，更关键的是
    会同时持有全部内容；这里用「行计数 == 预期行数」确认它真的一行一行走过。
    """
    gate = _load()
    root = _mkroot(tmp_path)
    total = 20_000
    body = "".join(f".c{i} {{ color: var(--c{i}); }}\n" for i in range(total))
    _write(root, "apps/web/src/big.css", body)

    result = gate.scan(root)
    assert result["scanned"]["lines"] == total
    assert result["hex"] == []


# ───────────────────────── CLI 契约 ─────────────────────────


def test_main_returns_nonzero_for_missing_root(tmp_path: Path, capsys) -> None:
    gate = _load()
    code = gate.main(["--root", str(tmp_path / "nope")])
    assert code == 1
    assert "扫描根不存在" in capsys.readouterr().err


# ───────────────────── 真实仓库：判据不能空转 ─────────────────────


def _looks_like_real_repo(repo: Path) -> bool:
    """本仓库前端的判据：有 `apps/web/package.json`。

    ★ 用它而不是「有 apps/web/src 目录」：任何造出来的样本树都能有那个目录，
      而 package.json 只有真的前端工程才有——否则这条测试会在样本树上假红。
    """
    return (repo / "apps" / "web" / "package.json").is_file()


def test_real_repo_is_not_zero_scan() -> None:
    """合入后跑在真仓库上时，必须真扫到东西（防「根算错 ⇒ 零文件 ⇒ 绿」）。

    ★ 这条**不断言真仓库是干净的**——存量 BOM / 色值本来就还在。
      它只断言「门禁看见了这个仓库」，这正是其它所有断言的前提。
    """
    import pytest

    gate = _load()
    repo = _REPO
    if not _looks_like_real_repo(repo):
        pytest.skip(f"不是真仓库布局（{repo.as_posix()} 下没有 apps/web/package.json），跳过")

    result = gate.scan(repo)
    assert result["scanned"]["files"] >= 100, f"真仓库只扫到 {result['scanned']['files']} 个文件"
    assert result["missing_roots"] == []
    assert result["scanned"]["hex_exempt_files"], "tokens.css 没被识别成令牌文件"
    # ★ 2026-11-18：存量已清零（139 → 0），这条断言跟着反转 ——
    #   以前用它证明「判据没失效」，现在用「必须为 0」证明「清理没回退」，
    #   同时靠上面 hex_exempt_files 非空证明门禁不是扫空目录。
    assert result["hex"] == [], f"出现了新的写死色值：{result['hex']}"


def test_main_json_is_machine_readable(tmp_path: Path, capsys) -> None:
    import json

    gate = _load()
    root = _mkroot(tmp_path)
    _write(root, "apps/web/src/a.css", ".x { color: #4caf50; }\n")

    # ★ 2026-11-18 起默认 strict（存量已清零）：默认必须拦
    assert gate.main(["--root", str(root), "--json"]) == 1, "默认 strict 应当拦下写死色值"
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["strict"] is True
    assert len(payload["hex"]) == 1

    # 逃生门：--no-strict 只报告不拦（临时排查存量用）
    assert gate.main(["--root", str(root), "--json", "--no-strict"]) == 0
    lenient = json.loads(capsys.readouterr().out)
    assert lenient["ok"] is True
    assert lenient["strict"] is False
