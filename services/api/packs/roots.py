"""包目录的解析 —— 「一个包 id 对应哪个目录」的**唯一答案**。

★ 为什么需要这个文件
-------------------
协议 §6 把包分成四类，其中「官方包」的来源是**随发行版提供**：
它不在用户的数据目录里，而是跟着发行版走的。

而「本地包」「第三方包」是用户自己放进数据目录的。

⇒ 于是「包在哪」有**两个答案**：
  · **发行版包根**（项目根 `packs/`）—— 随发行版走，read-only 语义
  · **数据包根**（`data/packs/`）—— 用户的，read-write 语义

★ 为什么必须有**一个**解析函数
-----------------------------
改之前有三处各自算「包目录在哪」：路由的 `_pack_dir`、
装配器的 `packs_root`、作者命令面的入参。

三处各算各的 ⇒ 迟早分叉（某个地方改了另外两个没改），
表现是「界面能看见这个包、点装配却找不到」——
而这种 bug 极难查：包确实在那儿，只是两处算出了两个路径。

⇒ 解析只有一处：`resolve_pack_dir()`。

★ 优先级：数据目录优先
----------------------
同一个 id 同时存在于两处时，**用户自己放的那份赢**。

理由：用户装了本地包，说明他要的就是本地那份；
发行版那份是「默认提供的」，不是「用户选的」。

★ 为什么发行版包根不写进 Settings
-------------------------------
它是**发行结构**，不是运行期配置——
改配置就能改变「官方包在哪」的话，发行版就不再是发行版了。

★ 与插件层的对照
----------------
插件层也有一对「内置目录 / 第三方目录」
（`core/plugins/discover.py` 的 `_DEFAULT_MODULES_DIR` / `_DEFAULT_PLUGINS_DIR`），
同样的道理：路径是**发行结构**，写死在代码里，不做成配置。
"""

from __future__ import annotations

from pathlib import Path

#: `services/api/packs/roots.py` → `services/api/` 是 parents[1]，
#: 再上两级才是**项目根**（`services/api` → `services` → 项目根）。
_API_ROOT = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = _API_ROOT.parent.parent

#: 发行版自带包的根目录名（相对项目根）。
BUNDLED_DIRNAME = "packs"

#: 用户数据里包目录的子目录名（相对数据目录）。
DATA_DIRNAME = "packs"

#: 包清单文件名——一个目录**是不是包**，看它有没有这个文件。
MANIFEST_NAME = "pack.json"


def bundled_root() -> Path:
    """发行版自带的包根目录（项目根 `packs/`）。

    ★ 不存在时返回的路径**照样可用**（调用方 `.is_dir()` 判空即可）——
      「发行版没带任何官方包」是合法状态，不该抛异常。
    """
    return _PROJECT_ROOT / BUNDLED_DIRNAME


def data_root(data_dir: Path) -> Path:
    """用户数据里的包根目录（`data/packs/`）。"""
    return Path(data_dir) / DATA_DIRNAME


def resolve_pack_dir(
    pack_id: str,
    data_dir: Path,
    *,
    extra_roots: tuple[Path, ...] = (),
) -> Path | None:
    """按 id 找包目录。**数据目录优先**（用户自己放的赢）。

    返回 `None` 表示「哪儿都没有这个包」——不是异常：
    「找不到」是**正常状态**（用户还没装），不该让整个列表挂掉。

    ★ `extra_roots` 是**显式注入的根**，优先级最高：
      装配器用它在测试与定制部署里指定额外的查找位置
      （例如「这个部署的包在 `/opt/packs/`」）。
      它排在数据目录**之前**——因为那是调用方**明确说**的，
      而数据目录只是默认约定。
      显式 > 默认，这个顺序不能反。
    """
    if not pack_id:
        return None
    roots: list[Path] = [Path(r) for r in extra_roots]
    roots.append(data_root(data_dir))
    roots.append(bundled_root())
    for root in roots:
        candidate = root / pack_id
        if (candidate / MANIFEST_NAME).is_file():
            return candidate
    return None


def discover_pack_dirs(data_dir: Path) -> list[tuple[str, Path, str]]:
    """列出所有**可发现**的包。

    返回 `(pack_id, 目录, 来源)` 的列表，来源取 `local` / `bundled`。

    ★ 「可发现」的判据是**有 `pack.json`**——不是「有目录」。
      数据目录里还躺着 `registry.json` 与各包的 `state.json` /
      `pack.assembly.json` / `pack.json.<version>` 留档，
      那些都不是包。

    ★ 数据目录优先：同一个 id 两处都有时，只列数据目录那份
      （与 `resolve_pack_dir` 的优先级一致——**两处必须同口径**，
      否则「列表里有」与「能装配」会指向两个不同的目录）。
    """
    out: list[tuple[str, Path, str]] = []
    seen: set[str] = set()
    for root, source in (
        (data_root(data_dir), "local"),
        (bundled_root(), "bundled"),
    ):
        if not root.is_dir():
            continue
        for child in sorted(p for p in root.iterdir() if p.is_dir()):
            if child.name in seen:
                continue
            if not (child / MANIFEST_NAME).is_file():
                continue
            seen.add(child.name)
            out.append((child.name, child, source))
    return out


__all__ = [
    "BUNDLED_DIRNAME",
    "DATA_DIRNAME",
    "MANIFEST_NAME",
    "bundled_root",
    "data_root",
    "discover_pack_dirs",
    "resolve_pack_dir",
]
