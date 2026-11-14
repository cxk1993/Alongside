"""列表页解析器（纯标准库）—— sdu-feeds 的抓取核心。

★ 为什么单独一个文件、且**不 import 任何第三方库**：
  1. 第三方插件不能用相对导入（内核按文件路径加载），router 只能按路径把它拉起来；
  2. 解析器可以脱离 FastAPI / SQLModel 单独验证 —— 改选择规则时先跑
     `python -m pytest plugins/sdu-feeds/tests/test_parse.py`，
     不必等整个后端起得来（本模块就是这么开发的）。

★ 解析策略（不绑死某一家 CMS）：
  站点差异不在"哪一行是新闻"，而在**日期怎么长**。所以：
  · 条目定位 = 每个 `<li>` 里第一条 href 命中 `link_pattern` 的 `<a>`；
  · 标题 = `a[title]` → 内部标题标签(h1~h6) → `a` 的文本；
  · 日期 = 由 `date_mode` 决定，见 `extract_date()` 的三种模式。

  三种日期形态都在山大真实站点上验证过：
  · `ymd`   学院站  `<span class="fr">2025-01-02</span>`
  · `ym+d`  青春山大 `<div class=date><p>12</p><span>2026-11</span></div>`
  · `md+y`  本科生院 `<div class=time><span>11.11</span><i>2026</i></div>`
"""

import re
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urljoin

VOID_TAGS = frozenset(
    {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }
)

_WS_RE = re.compile(r"\s+")
_YMD_RE = re.compile(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?")
_YM_RE = re.compile(r"(20\d{2})[-/.年](\d{1,2})月?")
_MD_RE = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})(?!\d)")
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")


@dataclass(frozen=True)
class ParsedItem:
    """一条列表条目（还没入库）。"""

    url: str
    title: str
    published_on: date | None


class _Node:
    """极简 DOM 节点：tag / attrs / children（str 或 _Node）。"""

    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag: str, attrs: dict[str, str] | None = None) -> None:
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list["_Node | str"] = []

    def text(self) -> str:
        parts: list[str] = []
        for child in self.children:
            if isinstance(child, str):
                parts.append(child)
            else:
                parts.append(child.text())
        return "".join(parts)

    def iter(self, tag: str | None = None):
        for child in self.children:
            if isinstance(child, _Node):
                if tag is None or child.tag == tag:
                    yield child
                yield from child.iter(tag)


class _Tree(HTMLParser):
    """容错建树：遇到不配对的闭合标签就忽略，不抛异常。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#document")
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs) -> None:
        node = _Node(tag, {k: (v or "") for k, v in attrs})
        self._stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs) -> None:
        self._stack[-1].children.append(_Node(tag, {k: (v or "") for k, v in attrs}))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self._stack[-1].children.append(data)


def clean_text(value: str) -> str:
    """压掉所有空白（含全角空格与零宽字符），用于标题/日期比较。"""
    text = (value or "").replace("\u3000", " ").replace("\u200b", "").replace("\xa0", " ")
    return _WS_RE.sub(" ", text).strip()


def _parse_html(html: str) -> _Node:
    tree = _Tree()
    tree.feed(html)
    tree.close()
    return tree.root


def _href_of(anchor: _Node) -> str:
    return (anchor.attrs.get("href") or "").strip()


def _is_content_link(href: str, pattern: re.Pattern[str]) -> bool:
    low = href.lower()
    if not href or low.startswith(("javascript:", "mailto:", "#", "tel:")):
        return False
    return bool(pattern.search(href))


def _title_of(anchor: _Node) -> str:
    """标题优先级：a[title] → 内部 h1~h6 → a 文本。

    · 学院站：可见文本被截断（"…会"），完整标题只在 title 属性里；
    · 本科生院：title 是空串，标题在 <h5> 里；
    · 青春山大：两个都没有，a 文本就是完整标题。
    """
    attr = clean_text(anchor.attrs.get("title", ""))
    if len(attr) >= 4:
        return attr
    for heading in _HEADINGS:
        for node in anchor.iter(heading):
            text = clean_text(node.text())
            if len(text) >= 4:
                return text
    return clean_text(anchor.text())


def _year_in_nodes(node: _Node) -> int | None:
    """找一个"整格就是四位年份"的子节点（如本科生院的 `<i>2026</i>`）。

    ★ 为什么要限定整格相等：标题里常出现 "2027届" —— 按文本正则抓四位数字
      会把标题里的年份错当成发布日期（实测踩过）。
    """
    for child in node.iter():
        text = clean_text(child.text())
        if len(text) == 4 and text.isdigit() and _YEAR_RE.fullmatch(text):
            return int(text)
    return None


def _small_int_in_nodes(node: _Node, *, low: int = 1, high: int = 31) -> int | None:
    """找一个"整格就是 1~2 位数字"的子节点（如青春山大的 `<p>12</p>`）。"""
    for child in node.iter():
        if child.tag in ("script", "style"):
            continue
        text = clean_text(child.text())
        if text.isdigit() and 1 <= len(text) <= 2 and low <= int(text) <= high:
            return int(text)
    return None


def _as_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def extract_date(node: _Node, mode: str = "auto") -> date | None:
    """从条目节点里取发布日期。

    mode:
      · ymd  —— 文本里有完整 `2025-01-02`
      · ym+d —— 文本里有 `2026-11`，日号在另一个节点里（`<p>12</p>`）
      · md+y —— 文本里有 `11.11`，年份在另一个节点里（`<i>2026</i>`）
      · auto —— 依次尝试三种（默认）
    """
    text = clean_text(node.text())

    if mode in ("auto", "ymd"):
        m = _YMD_RE.search(text)
        if m:
            got = _as_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if got:
                return got

    if mode in ("auto", "ym+d"):
        m = _YM_RE.search(text)
        if m:
            day = _small_int_in_nodes(node)
            if day:
                got = _as_date(int(m.group(1)), int(m.group(2)), day)
                if got:
                    return got

    if mode in ("auto", "md+y"):
        year = _year_in_nodes(node)
        if year is None:
            y = _YEAR_RE.search(text)
            year = int(y.group(1)) if y else None
        m = _MD_RE.search(text)
        if year and m:
            got = _as_date(year, int(m.group(1)), int(m.group(2)))
            if got:
                return got

    return None


def parse_list(
    html: str,
    base_url: str,
    link_pattern: str,
    *,
    date_mode: str = "auto",
    min_title: int = 4,
    limit: int = 50,
) -> list[ParsedItem]:
    """解析一个列表页，返回按页面顺序排列的条目（已按 URL 去重）。

    · base_url：相对链接的基准（一般给站点根，如 `https://www.cs.sdu.edu.cn/`）；
    · link_pattern：正则，只匹配正文链接 —— 这是过滤导航栏的关键；
    · date_mode：见 `extract_date()`；
    · limit：单页封顶，防止某站点分页异常时一次灌进上千条。
    """
    pattern = re.compile(link_pattern, re.I)
    root = _parse_html(html)
    containers = list(root.iter("li")) or [root]

    items: list[ParsedItem] = []
    seen: set[str] = set()

    for box in containers:
        anchor = None
        for candidate in box.iter("a"):
            if _is_content_link(_href_of(candidate), pattern):
                anchor = candidate
                break
        if anchor is None:
            continue

        title = _title_of(anchor)
        if len(title) < min_title:
            continue

        url = urljoin(base_url, _href_of(anchor))
        if url in seen:
            continue
        seen.add(url)

        items.append(ParsedItem(url=url, title=title, published_on=extract_date(box, date_mode)))
        if len(items) >= limit:
            break

    return items
