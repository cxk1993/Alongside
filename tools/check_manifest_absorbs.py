#!/usr/bin/env python3
"""容器吸收关系门禁：`manifest.absorbs` 与 `tabs[].entry` 的一致性。

## 它回答什么问题

前端「谁并进了谁」的推导口径（`apps/web/src/kernel/moduleGraph.ts`）是：

    吸收 = `tabs[].entry`（**有页**的吸收）∪ `absorbs[]`（**无页**归口）

后端每份 manifest 都能写这两样，但**没有东西检查它们彼此自洽**。
2026-11 的实况是：19 条吸收关系写死在前端 `DOCK_MERGE` 里，
manifest 一个字没声明；后端补声明时如果写错（对象不存在 / 两边都写 /
两个容器抢同一个子模块），前端不会报错 —— 它只会**少一个入口**。
少入口是「看起来正常、其实点不到」的那类缺陷，最难被当成 bug 报上来。

⇒ 本门禁把「吸收关系是否自洽」变成可判的东西。

## 判据（为什么这么定）

| # | 判据 | 为什么 |
|---|---|---|
| R1 | `absorbs` 与自身 `tabs[].entry` 重复 | entry 已隐含吸收。两处都写 = 两个真相源，早晚分叉（`core/manifest.py` 已拦，这里是双保险） |
| R2 | 一个模块被**两个**容器吸收 | 「一个模块只归一个容器」是前端推导的前提（多容器争抢时按 id 升序先到先得 —— 那是**静默**丢一个） |
| R3 | `absorbs` / `tabs[].entry` 指向不存在的模块 | 幽灵吸收 / 幽灵页：前端会静默跳过，用户看到的是「这块不见了」 |
| R4 | 吸收目标是**容器** | 容器不互相吞（防嵌套成环），前端会跳过 —— 声明了等于没声明 |
| R5 | 无页归口了一个**有前端入口**的模块 | 它会被并进容器但容器里没有它的页 ⇒ 入口消失（`diary` 踩过这个坑） |
| R6 | 自报 `kind=container` 却既不声明 `tabs` 也不声明 `absorbs` | 容器什么都不装 = 清单在说谎 |

★ 为什么不判「`B` 被 `A.tabs[].entry` 指向，但 `A.absorbs` 里没有 `B`」：
  那正是**正确**写法。`absorbs` 的语义是「**无页**归口」，有页的吸收由 `entry` 表达；
  要求两边都写会与 `core/manifest.py` 的「不得与 tabs[].entry 重复」直接冲突。
  真正要判的是 R1（重复）与 R5（有 UI 却无页）。

## 只读的额外输入

- `plugins/*/manifest.json`：第三方插件也可能是被吸收方（如 `countdown`），
  判定「目标是否存在」必须看得见它们。**只读，不改。**
- `apps/web/src/kernel/moduleGraph.ts`：前端的「已知 manifest 缺口清单」
  （`TAB_SUPPLEMENTS` / `ABSORB_SUPPLEMENTS`）。后端补齐一条，这里就提醒一条可删 ——
  否则兜底表会永远留着，下一次分叉从这里开始。同样**只读**。

用法：
    python tools/check_manifest_absorbs.py
    python tools/check_manifest_absorbs.py --json
    python tools/check_manifest_absorbs.py --selfcheck   # 反向自证：注入失效必须报红

退出码：0 = 自洽（警告不算失败）；1 = 有判据不成立。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from paths import MODULES, PLUGINS, WEB_SRC, rel  # noqa: E402

GRAPH_TS = WEB_SRC / "kernel" / "moduleGraph.ts"


# ─────────────────────────── 读取 ───────────────────────────


def _read_manifests(root: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """读一个目录下的全部 `*/manifest.json`。返回 (id → manifest, 错误)。"""
    out: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    if not root.is_dir():
        return out, errors
    for path in sorted(root.glob("*/manifest.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"{rel(path)}：读不出合法 JSON（{exc}）")
            continue
        mid = raw.get("id")
        if not isinstance(mid, str) or not mid:
            errors.append(f"{rel(path)}：缺 id")
            continue
        if mid != path.parent.name:
            errors.append(f"{rel(path)}：id={mid!r} ≠ 目录名 {path.parent.name!r}")
            continue
        out[mid] = raw
    return out, errors


def _is_container(manifest: dict[str, Any]) -> bool:
    """容器判据 —— 与前端 `moduleGraph.buildModuleGraph` 同口径：
    `kind === "container"` ∪ 声明了非空 `absorbs`。

    ★ 为什么非容器的 `tabs` 不算吸收声明：`finance` 的 tabs 是
      「账本 / 本地流水」两个**自渲染子页**（key 是页名，不是模块 id），
      由 finance 自己的 App 渲染，根本没有「另一个模块」被吸收。
      把非容器的 tabs 当吸收声明，会得到两条幽灵页误报。
    """
    return manifest.get("kind") == "container" or bool(_absorbs(manifest))


def _tab_entries(manifest: dict[str, Any]) -> list[str]:
    """容器的 `tabs[].entry`（缺省时 `key` 即模块 id —— 与前端推导同口径）"""
    if not _is_container(manifest):
        return []
    out: list[str] = []
    for tab in manifest.get("tabs") or []:
        if not isinstance(tab, dict):
            continue
        target = tab.get("entry") or tab.get("key")
        if isinstance(target, str) and target:
            out.append(target)
    return out


def _absorbs(manifest: dict[str, Any]) -> list[str]:
    return [str(x) for x in (manifest.get("absorbs") or []) if isinstance(x, str) and x]


# ─────────────────────────── 判定 ───────────────────────────


def evaluate(manifests: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """对「全量 manifest（内置 + 第三方）」判吸收关系一致性。"""
    errors: list[str] = []
    warnings: list[str] = []
    known = set(manifests)

    # 每个子模块被谁声明吸收（tabs.entry 与 absorbs 都算 —— 前端就是把两者并起来的）
    claims: dict[str, list[str]] = {}

    for mid, m in sorted(manifests.items()):
        entries = _tab_entries(m)
        absorbs = _absorbs(m)
        is_container = m.get("kind") == "container"

        # R1：absorbs 与 tabs[].entry 重复
        dup = sorted(set(absorbs) & set(entries))
        if dup:
            errors.append(
                f"R1 模块 {mid}：absorbs 与 tabs[].entry 重复声明 {dup}"
                "（entry 已隐含吸收，重复写会让吸收关系有两个来源）"
            )

        # 自吸收
        if mid in absorbs:
            errors.append(f"R1 模块 {mid}：absorbs 含自身 id")

        # R3 / R4 / R5：逐条看被吸收者
        for target in absorbs:
            claims.setdefault(target, []).append(mid)
            tgt = manifests.get(target)
            if tgt is None:
                errors.append(f"R3 模块 {mid}：absorbs 指向不存在的模块 {target!r}")
                continue
            if tgt.get("kind") == "container":
                errors.append(
                    f"R4 模块 {mid}：吸收目标是容器 {target!r}（容器不互相吞，前端会跳过）"
                )
                continue
            if tgt.get("entry") and target not in entries:
                warnings.append(
                    f"R5 模块 {mid}：无页归口 {target!r}，但后者有前端入口"
                    f"（entry={tgt['entry']!r}）⇒ 并进容器后看不到它（diary 的教训）"
                )

        # R3：tabs 页指向不存在的模块
        for target in entries:
            claims.setdefault(target, []).append(mid)
            if target not in known:
                errors.append(f"R3 模块 {mid}：tabs[].entry 指向不存在的模块 {target!r}")

        # R6：容器什么都不装
        if is_container and not m.get("tabs") and not absorbs:
            warnings.append(f"R6 模块 {mid}：自报 kind=container 却没有 tabs / absorbs（容器是空的）")

    # R2：一个模块被多个容器吸收
    for child, parents in sorted(claims.items()):
        uniq = sorted(set(parents))
        if len(uniq) > 1:
            errors.append(f"R2 模块 {child!r} 被多个容器吸收：{uniq}（一个模块只归一个容器）")

    # 前端兜底表的「可退休」提示（只读、只提示）
    warnings.extend(_stale_frontend_supplements(manifests))

    containers = sorted(mid for mid, m in manifests.items() if _is_container(m))
    return {
        "moduleCount": len(manifests),
        "containers": containers,
        "absorbed": {c: sorted({p for p, ps in claims.items() if c in ps}) for c in containers},
        "errors": errors,
        "warnings": warnings,
        "ok": not errors,
    }


# ────────────────────── 前端兜底表（只读）──────────────────────


def _ts_record(text: str, name: str) -> dict[str, list[str]]:
    """从 `moduleGraph.ts` 里粗读一个 `Record<container, readonly string[]>` 常量。

    ★ 只做「读一眼」：解析失败返回空 dict（前端还在改，不该让后端门禁假红）。
    """
    block = re.search(
        rf"{name}\s*(?::[^=]*)?=\s*\{{(.*?)\n\}};", text, re.S
    )
    if not block:
        return {}
    out: dict[str, list[str]] = {}
    for m in re.finditer(r'(?:"([\w-]+)"|([\w-]+))\s*:\s*\[([^\]]*)\]', block.group(1)):
        key = m.group(1) or m.group(2)
        items = re.findall(r'"([\w.-]+)"', m.group(3))
        out[key] = items
    return out


def _stale_frontend_supplements(manifests: dict[str, dict[str, Any]]) -> list[str]:
    if not GRAPH_TS.is_file():
        return []
    try:
        text = GRAPH_TS.read_text(encoding="utf-8")
    except OSError:
        return []
    warnings: list[str] = []
    for cid, kids in _ts_record(text, "ABSORB_SUPPLEMENTS").items():
        declared = set(_absorbs(manifests.get(cid) or {}))
        for kid in kids:
            if kid in declared:
                warnings.append(
                    f"前端 ABSORB_SUPPLEMENTS[{cid}] 里的 {kid!r}：后端 manifest 已声明 absorbs"
                    " ⇒ 兜底条目可删（不删会重新长出第二真相源）"
                )
    # TAB_SUPPLEMENTS：后端 tabs 已补上同名页时可退休
    block = re.search(r"TAB_SUPPLEMENTS\s*(?::[^=]*)?=\s*\{(.*?)\n\};", text, re.S)
    if block:
        for m in re.finditer(
            r'(?:"([\w-]+)"|([\w-]+))\s*:\s*\[(.*?)\]\s*,?\s*(?://[^\n]*)?\n',
            block.group(1),
            re.S,
        ):
            cid = m.group(1) or m.group(2)
            keys = set(re.findall(r'key:\s*"([\w-]+)"', m.group(3)))
            declared_keys = {
                t.get("key") for t in (manifests.get(cid) or {}).get("tabs") or [] if isinstance(t, dict)
            }
            for k in sorted(keys & declared_keys):
                warnings.append(
                    f"前端 TAB_SUPPLEMENTS[{cid}] 里的页 {k!r}：后端 manifest 已有同 key 的 tabs"
                    " ⇒ 兜底条目可删"
                )
    return warnings


# ─────────────────────────── 自检 ───────────────────────────


def selfcheck() -> int:
    """反向自证：三条判据各自注入一次失效，都必须报红。

    ★ 门禁最危险的失效是「一直绿」。这里不碰真实文件，只喂构造数据。
    """
    base = {
        "a": {"id": "a", "kind": "container", "tabs": [{"key": "b", "label": "B", "entry": "b"}]},
        "b": {"id": "b", "kind": "builtin", "entry": "@/apps/b"},
    }
    checks: list[tuple[str, bool]] = []
    checks.append(("基线应通过", evaluate(base)["ok"]))

    # ① 两个容器抢同一个子模块
    two = {
        **base,
        "c": {"id": "c", "kind": "container", "absorbs": ["b"]},
    }
    got = evaluate(two)
    checks.append(("注入「双容器争抢」应报红", any("R2" in e for e in got["errors"])))

    # ② absorbs 指向不存在的模块
    ghost = {
        "a": {"id": "a", "kind": "container", "absorbs": ["nope"]},
    }
    got = evaluate(ghost)
    checks.append(("注入「幽灵吸收」应报红", any("R3" in e for e in got["errors"])))

    # ③ absorbs 与 tabs.entry 重复
    dup = {
        "a": {
            "id": "a",
            "kind": "container",
            "tabs": [{"key": "b", "label": "B", "entry": "b"}],
            "absorbs": ["b"],
        },
        "b": {"id": "b", "kind": "builtin", "entry": "@/apps/b"},
    }
    got = evaluate(dup)
    checks.append(("注入「重复声明」应报红", any("R1" in e for e in got["errors"])))

    # ④ 吸收容器（嵌套）
    nested = {
        "a": {"id": "a", "kind": "container", "absorbs": ["b"]},
        "b": {"id": "b", "kind": "container", "tabs": [{"key": "b1", "label": "B1", "entry": "b1"}]},
        "b1": {"id": "b1", "kind": "builtin", "entry": "@/apps/b1"},
    }
    got = evaluate(nested)
    checks.append(("注入「吸收容器」应报红", any("R4" in e for e in got["errors"])))

    print("吸收关系门禁自检（反向验证）")
    for label, ok in checks:
        print(f"  {'✓' if ok else '✗'} {label}")
    passed = sum(1 for _l, ok in checks if ok)
    print(f"\n{passed}/{len(checks)} 通过")
    return 0 if passed == len(checks) else 1


# ─────────────────────────── 入口 ───────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="容器吸收关系一致性门禁")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    parser.add_argument("--selfcheck", action="store_true", help="反向自证")
    args = parser.parse_args(argv)

    if args.selfcheck:
        return selfcheck()

    builtin, err_a = _read_manifests(MODULES)
    third, err_b = _read_manifests(PLUGINS)
    manifests = {**third, **builtin}  # 内置优先（同 id 时内置为准；discover 已保证不会重名）
    result = evaluate(manifests)
    result["errors"] = err_a + err_b + result["errors"]
    result["ok"] = not result["errors"]
    result["sources"] = {
        "builtin": rel(MODULES),
        "thirdParty": rel(PLUGINS),
        "builtinCount": len(builtin),
        "thirdPartyCount": len(third),
    }
    if len(builtin) == 0:
        result["errors"].append("内置模块目录一个 manifest 都没读到 —— 门禁扫了零个文件")
        result["ok"] = False

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1

    src = result["sources"]
    print(
        f"容器吸收关系门禁 · 内置 {src['builtinCount']} 份 + 第三方 {src['thirdPartyCount']} 份 manifest"
    )
    print(f"  容器 {len(result['containers'])} 个：{', '.join(result['containers']) or '（无）'}")
    for cid in result["containers"]:
        kids = result["absorbed"].get(cid) or []
        print(f"    {cid:12s} ← {', '.join(kids) if kids else '（不吸收任何模块）'}")

    for w in result["warnings"]:
        print(f"  △ {w}")
    if result["errors"]:
        print("\n✗ 门禁不通过：")
        for e in result["errors"]:
            print(f"  · {e}")
    else:
        print("\n✓ 吸收关系自洽（无幽灵页 / 无双容器争抢 / 无重复声明）")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
