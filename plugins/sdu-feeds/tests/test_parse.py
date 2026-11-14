"""解析器回归测试：四个真实页面的快照，钉住"日期怎么长"这三类形态。

fixtures 全部来自 2026-11-13 的真实响应（只截了前 4 条 <li>），
不是手写样例 —— 手写的 HTML 永远比真实站点干净，测不出坑。

跑法（不需要起服务、不需要装依赖）：
    python plugins/sdu-feeds/tests/test_parse.py
    # 或
    python -m pytest plugins/sdu-feeds/tests/test_parse.py -q
"""

import importlib.util
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_FIXTURES = _HERE / "fixtures"
_PARSE_PATH = _HERE.parent / "api" / "parse.py"


def _load_parse():
    """按路径加载解析器（第三方插件不能用相对导入，测试也一样）。"""
    spec = importlib.util.spec_from_file_location("sdu_feeds_parse_test", _PARSE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


parse = _load_parse()


def _run(fixture: str, base: str, pattern: str, mode: str):
    html = (_FIXTURES / fixture).read_text(encoding="utf-8")
    return parse.parse_list(html, base, pattern, date_mode=mode)


def test_college_announcements_full_date():
    items = _run("cs_xygg.html", "https://www.cs.sdu.edu.cn/", r"info/\d+/\d+\.htm", "auto")
    assert len(items) == 4
    first = items[0]
    assert first.url == "https://www.cs.sdu.edu.cn/info/1058/6443.htm"
    assert first.published_on == date(2025, 1, 2)
    # 可见文本被截断成 "…会"，完整标题必须来自 a[title]
    assert first.title.startswith("计算机专业建设与人工智能赋能本科教育教学研讨会")
    assert "…" not in first.title and not first.title.endswith("...")


def test_college_talks_full_date():
    items = _run("cs_xsbg.html", "https://www.cs.sdu.edu.cn/", r"info/\d+/\d+\.htm", "auto")
    assert len(items) == 4
    assert items[0].published_on == date(2026, 5, 12)
    assert items[0].url == "https://www.cs.sdu.edu.cn/info/1056/6806.htm"


def test_youth_year_month_plus_day_node():
    """青春山大：<p>12</p> + <span>2026-11</span> 要拼成 2026-11-12。"""
    items = _run("youth_list.html", "https://www.youth.sdu.edu.cn/", r"tz_content\.jsp", "ym+d")
    assert len(items) == 4
    first = items[0]
    assert first.published_on == date(2026, 11, 12)
    assert first.title.startswith("关于举办第二十届")
    assert "wbnewsid=12463" in first.url


def test_bkjx_month_day_plus_year_node():
    """本科生院：<span>11.11</span> + <i>2026</i>，标题在 <h5> 里。"""
    items = _run(
        "bkjx_tzgg.html",
        "https://www.bkjx.sdu.edu.cn/",
        r"content\.jsp\?urltype=news",
        "md+y",
    )
    assert len(items) == 4
    first = items[0]
    assert first.published_on == date(2026, 11, 11)
    assert first.title == "关于做好2027届毕业生图像信息核对及补报工作的通知"
    assert first.url.startswith("https://www.bkjx.sdu.edu.cn/content.jsp?")


def test_ignores_navigation_and_dedupes():
    """导航链接（list.jsp / index.htm）不该被当条目；重复 URL 只留一条。"""
    html = """
    <ul>
      <li><a href="list.jsp?urltype=tree.TreeTempUrl&wbtreeid=1004">查看更多</a></li>
      <li><a href="info/1/1.htm" title="同样的公告标题">同样的公告标题</a><span>2026-01-02</span></li>
      <li><a href="info/1/1.htm" title="同样的公告标题">同样的公告标题</a><span>2026-01-02</span></li>
    </ul>
    """
    items = parse.parse_list(html, "https://example.edu.cn/", r"info/\d+/\d+\.htm")
    assert len(items) == 1
    assert items[0].published_on == date(2026, 1, 2)


def test_unclosed_tags_do_not_crash():
    """真实站点常有未闭合标签，解析器必须容错而不是抛异常。"""
    html = '<ul><li><a href="info/2/2.htm" title="未闭合也能解析">未闭合也能解析<span>2026-02-03'
    items = parse.parse_list(html, "https://example.edu.cn/", r"info/\d+/\d+\.htm")
    assert len(items) == 1
    assert items[0].title == "未闭合也能解析"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    raise SystemExit(1 if failures else 0)
