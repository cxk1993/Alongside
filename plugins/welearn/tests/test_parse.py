"""WE Learn 解析器回归测试。

★ fixture 来源说明（诚实标注）：`fixtures/course_info_sample.html` 是**按真实页面
  结构手写的脱敏样本**（`<h4 class="panel-title">` + `<a>` + 「已完成：<span>N</span>/<span>M</span>」），
  不是从主人账号抓的真实响应。它钉住的是「四种形态的解析行为」，不是像素级还原；
  拿到真实快照后应补一份替换进去（见文末 TODO）。

★ 为什么必须测这一条：`parse_units` 靠一条正则从 HTML 抠进度，平台改一次 class 名
  就返回 `[]`，而上层把 `[]` 解释成「没有未完成单元」——**静默假成功**，
  用户以为是真没作业。这是五个插件里唯一「改版即无声失效」的解析点。

跑法（不需要起服务、不需要装依赖）：
    python plugins/welearn/tests/test_parse.py
    # 或
    python -m pytest plugins/welearn/tests/test_parse.py -q
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[2]  # 仓库根
_SRC = _HERE.parent / "api" / "router.py"


def _load_parse() -> object:
    """按内核的包命名注册 api/ 后再加载 router —— 与线上同一条加载路径。

    router 里有 `from . import models/client`，所以必须先有包上下文；
    直接按路径加载 router.py 会抛「attempted relative import with no known parent package」。
    """
    pkg = "plugin_thirdparty_welearn"
    api_dir = _HERE.parent / "api"
    if pkg not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            pkg, api_dir / "__init__.py", submodule_search_locations=[str(api_dir)]
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        mod.__path__ = [str(api_dir)]  # type: ignore[attr-defined]
        mod.__package__ = pkg
        sys.modules[pkg] = mod
    # router 依赖内核模块（core.*），把 services/api 放进 sys.path
    api_root = _ROOT / "services" / "api"
    if str(api_root) not in sys.path:
        sys.path.insert(0, str(api_root))
    key = f"{pkg}.router"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, _SRC)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


def _fixture(name: str) -> str:
    return (_HERE / "fixtures" / name).read_text(encoding="utf-8")


def test_parse_units_basic() -> None:
    """三种形态：正常单元、已完成单元、0/0 说明行（必须跳过）。"""
    mod = _load_parse()
    units = mod.parse_units(_fixture("course_info_sample.html"))  # type: ignore[attr-defined]
    names = [u["name"] for u in units]
    assert len(units) == 3, f"应解析出 3 个学习单元（0/0 说明行不算），实际 {len(units)}: {names}"
    assert names == ["Unit 1 Travel", "Unit 2 Food", "Unit 3 Sports"], names

    by = {u["name"]: u for u in units}
    assert by["Unit 1 Travel"]["done"] == 19 and by["Unit 1 Travel"]["total"] == 19
    assert by["Unit 1 Travel"]["percent"] == 100
    assert by["Unit 1 Travel"]["finished"] is True

    assert by["Unit 2 Food"]["done"] == 3 and by["Unit 2 Food"]["total"] == 10
    assert by["Unit 2 Food"]["percent"] == 30
    assert by["Unit 2 Food"]["finished"] is False

    # 嵌套标签必须被剥掉，标题里不能残留 HTML
    assert "<" not in "".join(names)


def test_parse_units_empty_is_empty() -> None:
    """改版后正则不匹配时返回 []（上层须据此提示「可能是改版」，不能当「没作业」）。"""
    mod = _load_parse()
    assert mod.parse_units("<html><body>nothing here</body></html>") == []  # type: ignore[attr-defined]
    assert mod.parse_units("") == []  # type: ignore[attr-defined]


def test_to_int_tolerates_dirty_numbers() -> None:
    """`per` 类字段可能是 \"12\" / \"12%\" / \"12.0\" / None —— 都不能抛。"""
    mod = _load_parse()
    f = mod._to_int  # type: ignore[attr-defined]
    assert f("12") == 12
    assert f("12%") == 12
    assert f("12.0") == 12
    assert f(12) == 12
    assert f(None) == 0
    assert f("abc") == 0
    assert f(None, default=-1) == -1


# TODO（需要真实快照）：拿到一份真实 `/student/course_info.aspx` 响应（脱敏后）替换
# `fixtures/course_info_sample.html`，让测试对齐真实站点的空白/属性顺序差异。

if __name__ == "__main__":
    test_parse_units_basic()
    test_parse_units_empty_is_empty()
    test_to_int_tolerates_dirty_numbers()
    print("welearn parse_units: 3/3 通过 ✓")
