# ── 校验器可信度的外部证据 ──
#!/usr/bin/env python3
"""交叉验证：自研校验器 vs 标准 JSON Schema 校验器（jsonschema 4.26.0）。

★ 为什么需要这个：
  `core/plugins/validate.py` 是**自己实现**的 JSON Schema 子集。
  自研实现最危险的情况是「看起来对，实际和标准不一致」——
  它会放行标准校验器拒绝的 manifest，或拒绝标准允许的 manifest。
  那种错误在测试里很难被发现：测试用的是同一套实现，自证。

  所以用**标准实现**做裁判：同一份 Schema、同一批样本，
  两边结论必须一致。不一致的地方就是 bug。

★ 判定标准：
  · 标准说合法、我们说合法 → ✓
  · 标准说非法、我们说非法 → ✓
  · 两边结论不同 → ✗ 差异（要么改我们，要么改 Schema）

用法：
    python3 tools/crosscheck_validator.py
"""
from __future__ import annotations

import importlib.util
import json
import random
import sys
import types
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO / "contracts" / "plugin.schema.json"
VALIDATE_PATH = REPO / "services/api/core/plugins/validate.py"


def load_our_validator(path: "Path | None" = None):
    """把校验器模块加载起来。``path`` 用于自检时加载**沙箱副本**（绝不碰真源码）。"""
    target = Path(path) if path is not None else VALIDATE_PATH
    err_mod = types.ModuleType("core.errors")

    class ManifestError(Exception):
        pass

    err_mod.ManifestError = ManifestError
    saved = sys.modules.get("core.errors")
    sys.modules["core.errors"] = err_mod
    try:
        spec = importlib.util.spec_from_file_location("_v", target)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法加载校验器：{target}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if saved is None:
            sys.modules.pop("core.errors", None)
        else:
            sys.modules["core.errors"] = saved
    return mod


# ── 样本生成：在合法 manifest 上做随机变异，产生大量「可能非法」的样本 ──

def base_manifest() -> dict[str, Any]:
    return {
        "id": "demo",
        "name": "演示插件",
        "version": "1.0.0",
        "kind": "builtin",
        "kernelApi": "^1",
        "api": {"base": "/api/v1/demo"},
        "provides": [],
        "requires": [],
        "slots": [],
        "emits": [],
        "consumes": [],
        "permissions": {},
    }


# 变异器：每个都制造一类确定的违规
MUTATIONS: list[tuple[str, Any]] = [
    ("id 非法字符", lambda m: m.update(id="Demo_Bad")),
    ("id 数字开头", lambda m: m.update(id="1demo")),
    ("id 空", lambda m: m.update(id="")),
    ("version 非三段", lambda m: m.update(version="1.0")),
    ("version 带前缀", lambda m: m.update(version="v1.0.0")),
    ("kernelApi 非法", lambda m: m.update(kernelApi="~1")),
    ("kind 非法", lambda m: m.update(kind="bogus")),
    ("api.base 非法", lambda m: m.update(api={"base": "api/v1/demo"})),
    ("api.base 不带版本段", lambda m: m.update(api={"base": "/api/demo"})),
    ("api 整体缺", lambda m: m.pop("api")),
    ("provides 项格式错", lambda m: m.update(provides=["bad"])),
    ("provides 项空", lambda m: m.update(provides=[""])),
    ("provides 非常量", lambda m: m.update(provides=[{"a": 1}])),
    ("requires 项格式错", lambda m: m.update(requires=["a"])),
    ("slots 非法", lambda m: m.update(slots=["not-a-slot"])),
    ("slots 非常量", lambda m: m.update(slots=[123])),
    ("emits 格式错", lambda m: m.update(emits=["X"])),
    ("consumes 格式错", lambda m: m.update(consumes=["X"])),
    ("permissions 非对象", lambda m: m.update(permissions="all")),
    ("permissions 值非法", lambda m: m.update(permissions={"network": "yes"})),
    ("permissions 未知键", lambda m: m.update(permissions={"nope": True})),
    ("name 非字符串", lambda m: m.update(name=123)),
    ("name 太长", lambda m: m.update(name="x" * 300)),
    ("version 非字符串", lambda m: m.update(version=1.0)),
    ("顶层数组", lambda m: None),
    ("顶层字符串", lambda m: None),
]


def targeted_cases() -> list[tuple[str, Any]]:
    """定向样本：覆盖随机变异**碰不到**的组合。

    ★ 为什么需要：
      随机变异只会乱改字段，生成不出「kind=container 且缺 api」这类
      需要**多个字段协同**才可观测的场景。而 Schema 里的 const 恰好
      只出现在 `allOf[0].if.properties.kind.not` —— 只有该组合下
      const 的效果才可观测。样本覆盖不到，裁判就永远抓不到那类 bug。
      这是「零差异」可能骗人的一个真实例子。

    做法：手工构造关键字段的组合，逐个断言。
    """
    cases: list[tuple[str, Any]] = []

    # ① kind=container 且缺 api → 应放行（container 可省 api）
    m = base_manifest()
    m["kind"] = "container"
    m.pop("api")
    cases.append(("container 缺 api（应放行）", m))

    # ② kind=container 但 api.base 非法 → 应拦（const 分支虽不触发，base 仍要合法）
    m = base_manifest()
    m["kind"] = "container"
    m["api"] = {"base": "bad-base"}
    cases.append(("container + 非法 api.base（应拦）", m))

    # ③ kind 非 container 且缺 api → 应拦（then.required api）
    m = base_manifest()
    m.pop("api")
    cases.append(("非 container 缺 api（应拦）", m))

    # ④ kind=container + 合法 api → 应放行
    m = base_manifest()
    m["kind"] = "container"
    cases.append(("container + 合法 api（应放行）", m))

    # ⑤ kind 取各种枚举值
    for k in ["builtin", "container", "service", "ui", "tool", "integration"]:
        m = base_manifest()
        m["kind"] = k
        cases.append((f"kind={k}", m))

    # ⑥ 顶层字段全缺
    cases.append(("仅 id", {"id": "demo"}))
    cases.append(("空对象", {}))

    # ⑧ minLength 的目标样本 ★ 这组是「裁判自检」能抓到
    #   「minLength 判断反转」注入的唯一依据。
    #   ★ 为什么必须有：Schema 里 minLength=1 约束着 name / tabs[].key /
    #     tabs[].label / absorbs[] / schedule.title。
    #     若样本里这些字段**全是合法非空串**，把 `<` 反转成 `>`
    #     在样本上**完全不可观测**——裁判不会报警，
    #     而「零差异」会被误读成「裁判在工作」。
    #     ⇒ 只有让样本覆盖「该空却没空」，注入的 bug 才会显形。
    #     这正是本函数存在的意义：覆盖随机变异碰不到的组合。
    for field in ("name", "absorbs", "schedule"):
        m = base_manifest()
        m[field] = "" if field != "absorbs" else [""]
        cases.append((f"{field} 空串（违反 minLength=1）", m))
    m = base_manifest()
    m["tabs"] = [{"key": "", "label": "ok"}]
    cases.append(("tabs[].key 空串（违反 minLength=1）", m))
    m = base_manifest()
    m["tabs"] = [{"key": "k", "label": ""}]
    cases.append(("tabs[].label 空串（违反 minLength=1）", m))

    # ⑦ api.tools 的键（pattern ^/ 是唯一未完全锚定的一处）
    tools_samples: list[dict[str, Any]] = [
        {"/a": {}},
        {"a": {}},
        {"": {}},
        {"/a": {}, "b": {}},
    ]
    for tools_map in tools_samples:
        m = base_manifest()
        m["api"] = {"base": "/api/v1/demo", "tools": tools_map}
        cases.append((f"api.tools={json.dumps(tools_map, ensure_ascii=False)}", m))

    return cases


def ours_says_ok(v, m: dict[str, Any]) -> bool:
    try:
        v.validate_manifest(m)
        return True
    except Exception:
        return False


def ours_layers(v, m: dict[str, Any]) -> tuple[bool, bool]:
    """返回 (结构层是否通过, 整体是否通过)。

    ★ 归因必须分层精确：
      之前把「整体失败」直接记成「②层规则拦的」，是**归因错误**——
      结构层失败也会被误标成②层意图。注入 bug 实测就会看到
      「合法基线」被标成「②层设计意图」，纯属胡说。
      现在分别取两层的结论，逐条归因。
    """
    errors: list[str] = []
    try:
        v._validate_node(m, v._SCHEMA, "manifest", errors)
    except Exception:
        return False, False
    structure_ok = not errors
    overall_ok = structure_ok and not v._check_consistency(m)
    return structure_ok, overall_ok


def standard_says_ok(schema: dict[str, Any], m: Any) -> bool:
    import jsonschema

    try:
        jsonschema.validate(m, schema)
        return True
    except jsonschema.ValidationError:
        return False


def main() -> int:
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        # ★ 刻意**不**返回 0：没装裁判就等于「没验证过」，
        #   报绿是比报错更危险的事——门禁静默失效，外表却是全绿。
        #   依赖已写进 pyproject 的 dev 组（jsonschema），正常环境不会走到这里。
        print("✗ 没装 jsonschema，无法进行交叉验证。")
        print("  装它：cd services/api && .venv/bin/pip install jsonschema")
        print("  （或按 services/api/pyproject.toml 的 dev 依赖组统一安装）")
        return 2

    if "--selfcheck" in sys.argv:
        return selfcheck()

    return run_crosscheck()


# ★ 反向验证用的注入点：(说明, 原代码, 注入后的错误代码)
INJECTIONS: list[tuple[str, str, str]] = [
    (
        "minLength 判断反转",
        'len(value) < schema["minLength"]',
        'len(value) > schema["minLength"]',
    ),
    (
        "const 判断反转",
        'if "const" in schema and value != schema["const"]:',
        'if "const" in schema and value == schema["const"]:',
    ),
    (
        "required 判断反转",
        "if field not in value:",
        "if field in value:",
    ),
    (
        "enum 放行一切",
        'if "enum" in schema and value not in schema["enum"]:',
        'if "enum" in schema and False:',
    ),
    (
        "type 校验失效",
        "if typ is not None and not _match_type(value, typ):",
        "if typ is not None and False:",
    ),
]


def selfcheck() -> int:
    """反向验证：注入已知 bug，确认裁判**抓得到**。

    ★ 为什么必须做：
      「零差异」有两种可能——①代码真的对 ②裁判根本没在工作。
      光看「零差异」无法区分这两者。必须**故意弄坏**一次，
      看裁判是否报警；报警了，才能相信它没报警时的沉默。

    手法：把源码**复制到沙箱文件** → 在副本上改 → 跑裁判 → 断言抓到。
    ★ 绝不改真源码：早先的实现直接改 `validate.py` 再还原，一旦中途异常
      就留下「注入残留」，而且下一轮的 `original` 读到的是污染版
      （连锁污染：曾实测到 `if field in value` 与 `... and False` 两处残留）。
      沙箱副本没有这个面——真源码在自检期间一个字节都不动。
    """
    import shutil
    import tempfile

    print("=" * 68)
    print("裁判自检（反向验证）")
    print("=" * 68)
    print("逻辑：对每种已知 bug 注入（沙箱副本）→ 裁判必须报警")
    print()

    original = VALIDATE_PATH.read_text(encoding="utf-8")
    sandbox_dir = Path(tempfile.mkdtemp())
    # ★ 沙箱必须**复制成真源码的目录深度**：validate.py 用
    #   `Path(__file__).resolve().parents[4] / "contracts"` 定位 schema。
    #   裸放在临时目录里会算到 C:\Users\<user>\contracts ⇒ FileNotFoundError。
    sandbox = sandbox_dir / "services" / "api" / "core" / "plugins" / "validate_sandbox.py"
    sandbox.parent.mkdir(parents=True, exist_ok=True)
    (sandbox_dir / "contracts").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SCHEMA_PATH, sandbox_dir / "contracts" / "plugin.schema.json")

    passed = 0
    failed: list[str] = []
    try:
        for label, old, new in INJECTIONS:
            if old not in original:
                failed.append(f"{label}：未找到注入锚点（源码变了？）")
                print(f"  ✗ {label}：锚点不存在")
                continue
            # ★ newline="\n"：Windows 下默认会把 \n 写成 \r\n，让沙箱副本与真源码不一致
            sandbox.write_text(original.replace(old, new, 1), encoding="utf-8", newline="\n")
            # 直接跑判定逻辑，不看输出；用小样本集 + 定向样本快速判定
            schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
            v = load_our_validator(sandbox)
            caught = 0
            probe = targeted_cases()
            rng = random.Random(20261027)
            fields = list(schema["properties"].keys())
            junk = [None, "", "x", 0, 1, True, [], {}, ["a"], {"a": 1}, "BAD VALUE", 3.14]
            for _ in range(400):
                m = base_manifest()
                for _ in range(rng.randint(1, 3)):
                    m[rng.choice(fields)] = rng.choice(junk)
                probe.append((f"rand{m}", m))
            for _name, m in probe:
                structure_ok, _ = ours_layers(v, m)
                std = standard_says_ok(schema, m)
                if structure_ok != std:
                    caught += 1
            if caught > 0:
                print(f"  ✓ {label}：裁判抓到 {caught} 处差异")
                passed += 1
            else:
                print(f"  ✗ {label}：裁判没抓到 ★ 裁判可能失效")
                failed.append(f"{label}：裁判没抓到")
    finally:
        # ★ 只清沙箱；真源码全程没被写过（写回 original 反而会把污染固化）。
        shutil.rmtree(sandbox_dir, ignore_errors=True)

    print()
    print(f"自检结果：{passed}/{len(INJECTIONS)} 种注入被成功捕获")
    if failed:
        for f in failed:
            print(f"  ★ {f}")
        return 1
    print("✓ 裁判有效：能抓到已知 bug，因此「零差异」是可信的")
    return 0


def run_crosscheck(verbose: bool = True) -> int:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    v = load_our_validator()

    cases: list[tuple[str, Any]] = [("合法基线", base_manifest())]
    for name, fn in MUTATIONS:
        m = base_manifest()
        fn(m)
        cases.append((name, m))

    # 定向样本：多字段协同的场景，随机变异碰不到
    cases.extend(targeted_cases())

    # 随机变异：多轮随机改字段，制造更多样本
    rng = random.Random(20261027)
    fields = list(schema["properties"].keys())
    junk = [None, "", "x", 0, 1, True, [], {}, ["a"], {"a": 1}, "BAD VALUE", 3.14]
    for i in range(1200):
        m = base_manifest()
        for _ in range(rng.randint(1, 3)):
            f = rng.choice(fields)
            m[f] = rng.choice(junk)
        cases.append((f"随机变异 #{i}", m))

    # 嵌套字段变异：深入 api / origin / execution / concurrency 等子对象
    nested_paths = [
        ("api", "base"),
        ("api", "tools"),
        ("origin", "installedBy"),
        ("origin", "signature"),
        ("origin", "signed"),
        ("execution", "mode"),
        ("execution", "parallelSafe"),
        ("execution", "checkpoint"),
        ("concurrency", "exclusive"),
        ("concurrency", "optimisticLock"),
        ("schedule", "cron"),
        ("schedule", "time"),
        ("lifecycle", "onEnable"),
        ("permissions", "network"),
        ("permissions", "filesystem"),
        ("sideEffects", "external"),
        ("sideEffects", "checkpoint"),
        ("track", "level"),
        ("riskLevel", "level"),
    ]
    nested_junk: list[Any] = [
        None, "", "x", 0, 1, True, [], {}, ["a"], "BAD", 3.14,
        "read", "L0", "before-side-effect",
    ]
    for i in range(1200):
        m = base_manifest()
        for _ in range(rng.randint(1, 2)):
            parent, child = rng.choice(nested_paths)
            cur = m
            cur[parent] = {} if not isinstance(cur.get(parent), dict) else cur[parent]
            cur[parent][child] = rng.choice(nested_junk)
        cases.append((f"嵌套变异 #{i}", m))

    # 结构层 vs 标准：真正的 bug 判定
    danger: list[str] = []          # 我们放行、标准拦截 → 危险（必须是 bug）
    suspect: list[str] = []         # 我们拦截、标准放行，且**非**②层所拦 → 可疑
    layer2_only: list[str] = []     # 结构层过、仅②层拦 → 设计意图，正常

    for name, m in cases:
        structure_ok, overall_ok = ours_layers(v, m)
        std = standard_says_ok(schema, m)
        if structure_ok == std:
            # 结构层结论一致。标准放行但我们整体拦 → ②层额外严格（设计意图）
            if structure_ok and not overall_ok:
                layer2_only.append(name)
            continue
        # 结构层结论不一致
        if structure_ok and not std:
            danger.append(name)      # 我们放行、标准拦截 ★ 危险
        else:
            suspect.append(name)     # 我们拦截、标准放行 → 可疑

    if not verbose:
        # 静默模式：只返回结论码，不打印
        return 1 if (danger or suspect) else 0

    print("=" * 68)
    print("校验器交叉验证（自研结构层 vs jsonschema 标准实现）")
    print("=" * 68)
    print(f"样本数：{len(cases)}")
    print(f"★ 危险差异（我们放行 / 标准拦截）：{len(danger)}")
    print(f"△ 可疑差异（我们拦截 / 标准放行）：{len(suspect)}")
    print(f"  其中仅因②层跨字段规则而拦（设计意图）：{len(layer2_only)}")
    print()

    if danger:
        print("★★ 危险：不合规 manifest 会被放行 —— 必须是 bug")
        for name in danger:
            print(f"  {name}")
        print()

    if suspect:
        print("△ 可疑差异（结构层实现可能过头了）：")
        for name in suspect[:20]:
            print(f"  {name}")
        if len(suspect) > 20:
            print(f"  …（其余 {len(suspect) - 20} 条同类）")
        print()

    if danger or suspect:
        print("结论：结构层与标准 JSON Schema 存在偏差，必须修。")
        return 1
    print("✓ 结构层与标准 JSON Schema 完全一致——自研实现无偏差")
    print(f"  （另有 {len(layer2_only)} 个样本结构层合法、被②层跨字段规则额外拦下，属设计意图）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
