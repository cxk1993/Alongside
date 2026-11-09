# ── 门禁自身的存活证明 ──
"""协议质量门禁的**自检测试**。

★ 为什么要有这个文件：
  这一轮给协议加了两道新门禁——
    ① 校验器交叉验证（自研 JSON Schema 子集 vs 标准 jsonschema 实现）
    ② 协议 ↔ 调研产物 对齐核查
  但门禁本身也可能有 bug。而门禁出 bug 是**最危险**的一种：
  它会一直报「全绿」，而实际早已放行了问题。

  所以门禁必须有反向测试：
    · 交叉验证必须能抓到**故意注入**的 bug
    · 对齐核查必须能抓到**故意删掉**的字段
  这些测试本身是代码，会被 CI 跑。门禁失效 → 测试报红。

  ★ 另一个纪律：不复用门禁脚本来算期望值。
    直接跑门禁 --json 拿结果 = 自证，永远绿。
    这里刻意**独立实现**一份最小判定，只用来做对照。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
CROSSCHECK = REPO / "tools" / "crosscheck_validator.py"
ALIGNMENT = REPO / "tools" / "check_research_alignment.py"
SCHEMA = REPO / "contracts" / "plugin.schema.json"
VALIDATE = REPO / "services/api/core/plugins/validate.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ══════════════════════════════════════════════════════════════
# ① 交叉验证：门禁必须能抓到故意注入的 bug
# ══════════════════════════════════════════════════════════════


def test_crosscheck_catches_injected_bug() -> None:
    """注入一个明显 bug，交叉验证必须报出差异。

    ★ 这是「门禁有效」的唯一硬证据。
      如果注入 bug 后门禁仍然报「零差异」，
      那它就抓不到任何 bug——它的「全绿」毫无意义。
    """
    cross = _load(CROSSCHECK, "_cc")
    original = VALIDATE.read_text(encoding="utf-8")
    try:
        # 注入：把 enum 校验整个废掉
        mutated = original.replace(
            'if "enum" in schema and value not in schema["enum"]:',
            'if "enum" in schema and False:',
            1,
        )
        assert mutated != original, "注入锚点不存在（源码变了？）"
        VALIDATE.write_text(mutated, encoding="utf-8")

        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        v = cross.load_our_validator()

        # 独立构造一个「enum 违规」的 manifest
        m = cross.base_manifest()
        m["kind"] = "totally-not-a-valid-kind"

        structure_ok, _ = cross.ours_layers(v, m)
        std_ok = cross.standard_says_ok(schema, m)

        assert std_ok is False, "标准校验器应拒绝非法 enum"
        assert structure_ok is True, (
            "注入 bug 后自研校验器应放行——正是要抓的漏网之鱼"
        )
        assert structure_ok != std_ok, "两边结论必须出现差异，交叉验证才有得报"
    finally:
        VALIDATE.write_text(original, encoding="utf-8")


def test_crosscheck_clean_code_has_no_dangerous_diff() -> None:
    """干净代码下，结构层与标准实现必须零差异。"""
    cross = _load(CROSSCHECK, "_cc2")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    v = cross.load_our_validator()

    # ★ 样本名要带进断言消息——否则失败时只看到一个巨大的 dict，
    #   不知道是哪个样本出的问题，排查成本极高。
    samples: list[tuple[str, Any]] = [("合法基线", cross.base_manifest())]
    for name, fn in cross.MUTATIONS:
        m = cross.base_manifest()
        fn(m)
        samples.append((name, m))
    samples.extend(cross.targeted_cases())

    for name, m in samples:
        structure_ok, _ = cross.ours_layers(v, m)
        std_ok = cross.standard_says_ok(schema, m)
        assert structure_ok == std_ok, (
            f"结构层与标准实现结论不一致：样本「{name}」"
            f"我们={'放行' if structure_ok else '拦截'} "
            f"标准={'放行' if std_ok else '拦截'}"
        )


def test_crosscheck_selfcheck_passes() -> None:
    """门禁自检模式必须全绿（5 种注入全部被抓到）。"""
    proc = subprocess.run(
        [sys.executable, str(CROSSCHECK), "--selfcheck"],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        timeout=300,
    )
    assert proc.returncode == 0, f"裁判自检未通过：\n{proc.stdout}\n{proc.stderr}"
    assert "5/5" in proc.stdout, f"自检未覆盖全部注入点：\n{proc.stdout}"


# ══════════════════════════════════════════════════════════════
# ② 对齐核查：删掉字段必须被抓到
# ══════════════════════════════════════════════════════════════


def test_alignment_detects_missing_field() -> None:
    """把 Schema 里的字段删掉，对齐核查必须报缺失。

    ★ 同样是「门禁有效」的硬证据。
      对齐核查如果对字段缺失无感，那它报「24/24 全覆盖」是假的。
    """
    align = _load(ALIGNMENT, "_al")
    original = SCHEMA.read_text(encoding="utf-8")
    try:
        schema = json.loads(original)
        # 删掉一个对齐表里要求存在的字段
        victim = "defines"
        assert victim in schema["properties"], f"字段 {victim} 不存在，换一个"
        del schema["properties"][victim]
        SCHEMA.write_text(
            json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        results = align.check()
        defines_result = next(
            r for r in results if r["mechanism"].startswith("能力缝三角色：Service Definition")
        )
        assert defines_result["status"] != "✓", (
            f"删掉 {victim} 后仍报「协议侧已完备」★ 对齐核查失效"
        )
        assert victim in defines_result["fields_miss"], (
            f"应把 {victim} 报为缺字段，实际：{defines_result['fields_miss']}"
        )
    finally:
        SCHEMA.write_text(original, encoding="utf-8")


def test_alignment_all_mechanisms_covered() -> None:
    """当前代码下，调研机制应当全部在协议侧有完整对应。"""
    align = _load(ALIGNMENT, "_al2")
    results = align.check()
    bad = [
        (r["mechanism"], r["fields_miss"], r["rules_miss"])
        for r in results
        if r["status"] != "✓"
    ]
    assert not bad, f"以下调研机制在协议侧没对齐：{bad}"


def test_alignment_mechanism_table_covers_research_keywords() -> None:
    """对齐表本身不能漏——它必须覆盖调研产物里出现的关键机制词。

    ★ 防止「对齐核查只查了自己表里的东西」这种自证循环：
      表里没列的机制，核查就管不到。
      所以这里独立于表，从调研笔记里抽关键词，断言表里都覆盖到。
    """
    align = _load(ALIGNMENT, "_al3")
    table_text = json.dumps(align.ALIGNMENT, ensure_ascii=False)

    # 调研产物里反复出现的机制关键词，必须在映射表里有对应条目
    for keyword in [
        "defines",
        "provides",
        "requires",
        "lifecycle",
        "sideEffects",
        "permissions",
        "riskLevel",
        "migrations",
        "concurrency",
        "schedule",
        "origin",
        "slots",
        "api",
        "emits",
        "consumes",
        "kernelApi",
        "minKernel",
        "outputPolicy",
        "track",
        "execution",
        "deactivation",
        "activates_on",
        "settingsSchema",
    ]:
        assert keyword in table_text, f"对齐表漏了字段 {keyword}"


# ══════════════════════════════════════════════════════════════
# ③ 校验器本身的规范符合性
# ══════════════════════════════════════════════════════════════


def test_validator_uses_search_semantics_for_pattern() -> None:
    """pattern 必须用 search 语义，不能用 match。

    ★ JSON Schema 规范：pattern 不隐式锚定（部分匹配）。
      用 re.match 是「从头匹配」——对未锚定的 pattern 会与标准实现分歧。
      当前 Schema 的 pattern 恰好都带锚点，属于巧合，不是保证。
      这条测试把「巧合」变成「约定」。
    """
    src = VALIDATE.read_text(encoding="utf-8")
    assert 're.search(schema["pattern"], value)' in src, "pattern 校验未用 re.search"
    assert 're.match(schema["pattern"], value)' not in src, (
        "pattern 校验仍在用 re.match——与 JSON Schema 规范不符"
    )


def test_validator_has_no_bare_except_and_no_todo() -> None:
    """校验器里不该有裸 except 和待办占位。"""
    src = VALIDATE.read_text(encoding="utf-8")
    import re

    assert not re.search(r"except\s*:", src), "校验器里有裸 except"
    for marker in ("TODO", "FIXME", "XXX", "NotImplementedError"):
        assert marker not in src, f"校验器里有待办标记 {marker}"


def test_schema_is_valid_json_with_expected_shape() -> None:
    """Schema 本身必须是合法 JSON，且有协议该有的结构。"""
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema.get("type") == "object"
    assert "properties" in schema
    assert len(schema["properties"]) >= 30, "字段数明显偏少，Schema 是不是被改坏了"
    for required in schema.get("required", []):
        assert required in schema["properties"], f"required 里的 {required} 不在 properties 中"


def test_all_patterns_are_compilable() -> None:
    """Schema 里所有 pattern 都必须能被编译（写错正则会静默失效）。"""
    import re

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    patterns: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            p = node.get("pattern")
            if isinstance(p, str):
                patterns.append(p)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    assert patterns, "Schema 里一个 pattern 都没有？"
    for p in patterns:
        re.compile(p)  # 编译失败会直接抛异常
