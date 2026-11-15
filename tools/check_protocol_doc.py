"""插件协议契约文档一致性门禁。

`docs/specs/plugin-protocol-contract.md` 由 `tools/gen_protocol_doc.py` 从
`contracts/plugin.schema.json` 生成（确定性：无时间戳、字段序 = properties 书写序）。

本门禁把文档与生成器**当前输出**逐字比对：
  · 文档过期（schema 改了没重新生成）⇒ exit 1
  · 文档被手改 ⇒ exit 1（生成器是唯一真相源，改文档必须改 schema 再重新生成）

★ 门禁与生成器共用同一份实现（subprocess 调 gen_protocol_doc，不另写解析器）——
  否则迟早「生成器说绿、门禁说红」。

用法：
    python tools/check_protocol_doc.py              # 绿 exit 0，脏 exit 1
    python tools/check_protocol_doc.py --selfcheck  # 自证：篡改临时副本必须报红

★ 负例自证写临时副本，绝不改真文档（r3 注入残留教训）。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent
DOC_REL = "docs/specs/plugin-protocol-contract.md"
DOC = REPO / DOC_REL
GEN = TOOLS / "gen_protocol_doc.py"


def expected_output() -> str:
    """跑生成器拿当前期望输出（stdout，LF 归一）。"""
    r = subprocess.run(
        [sys.executable, str(GEN)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=REPO,
        timeout=60,
    )
    if r.returncode != 0:
        print(f"[protocol-doc] ✗ 生成器失败（exit {r.returncode}）：{r.stderr[:300]}")
        raise SystemExit(2)
    return r.stdout


def check(doc_path: Path) -> int:
    """比对指定文档与生成器输出。0 = 一致，1 = 分叉（打印首个分叉行与修复命令）。"""
    if not doc_path.is_file():
        print(f"[protocol-doc] ✗ 权威文档不存在：{doc_path}")
        print(f"    修复：python tools/gen_protocol_doc.py --out {DOC_REL}")
        return 1
    exp = expected_output()
    got = doc_path.read_text(encoding="utf-8")
    if exp != got:
        exp_lines, got_lines = exp.splitlines(), got.splitlines()
        why = ""
        for i, (a, b) in enumerate(zip(exp_lines, got_lines), 1):
            if a != b:
                why = f"首个分叉：第 {i} 行\n    期望: {a[:100]}\n    实际: {b[:100]}"
                break
        if not why:
            why = f"行数不一致：生成 {len(exp_lines)} 行 vs 文档 {len(got_lines)} 行"
        print(f"[protocol-doc] ✗ 契约文档与 schema 生成结果不一致（{why}）")
        print(f"    修复：python tools/gen_protocol_doc.py --out {DOC_REL}")
        return 1
    print(f"[protocol-doc] ✓ 契约文档与 schema 一致（{DOC_REL}，{len(got.splitlines())} 行）")
    return 0


def selfcheck() -> int:
    """反向自证：把真文档复制到临时目录、篡改副本，比对必须报红。

    报不出红 = 门禁瞎了（比没有门禁更糟）⇒ exit 1。真文档零写入。
    """
    with tempfile.TemporaryDirectory() as td:
        copy = Path(td) / "plugin-protocol-contract.md"
        shutil.copyfile(DOC, copy)
        text = copy.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        # 篡改：把第一个字段行改坏 + 删掉最后一行（两种病灶都要能抓到）
        lines[2] = lines[2].replace("required", "requiers-TAMPERED") if len(lines) > 2 else "# TAMPERED\n"
        tampered = "".join(lines[:-1]) if len(lines) > 1 else "# TAMPERED\n"
        copy.write_text(tampered, encoding="utf-8", newline="\n")
        rc = check(copy)
        if rc == 0:
            print("[protocol-doc] ✗ 自证失败：篡改副本竟然通过 —— 门禁瞎了，禁止登记/使用！")
            return 1
        print("[protocol-doc] ✓ 自证通过：篡改副本被正确报红（真文档零写入）")
        return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--selfcheck" in args:
        return selfcheck()
    return check(DOC)


if __name__ == "__main__":
    raise SystemExit(main())
