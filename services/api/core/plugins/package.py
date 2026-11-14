"""第三方插件包（zip）的解包与落地校验（r5 热插拔 · 层1.3）。

    POST /api/v1/plugins/upload  →  本模块  →  data_dir/plugins/<id>/

★ 为什么是「上传 zip」而不是「注册一个已落盘目录」
-------------------------------------------------
「注册已落盘目录」听起来更简单（现有 `/install` 就是这个形状），
但它要求用户先想办法把文件放到服务器上 —— 而插件管理面是给**网页界面**
用的，界面背后的人不一定有 shell。那等于把「装插件」变成只有运维能做。
⇒ 上传是一条完整的通路，也是这个功能存在的意义。

★ 落点：`data_dir/plugins/<id>`，与包层 `data/packs/` 对称
---------------------------------------------------------
容器部署只挂 `./data`（`docker-compose.yml`）⇒ 装进仓库根 `plugins/` 的东西
**重启即丢**。落进数据目录才能活过重启。

★ 四道闸，顺序不可换（先便宜后昂贵、先无副作用后有副作用）
---------------------------------------------------------
  1. 尺寸闸      —— 不解压就判（zip 里声明的大小 + 实际流入量）
  2. 形状闸      —— 解包前逐个 entry 判：绝对路径 / `..` / 符号链接 / 嵌套过深
  3. 契约闸      —— 解到临时目录后对照 schema 校验 manifest（复用 validate_manifest）
  4. 落地闸      —— `os.replace` 原子换名；目标已存在 ⇒ 409，绝不覆盖

★ 为什么必须先解到**临时目录**再 rename
--------------------------------------
直接往 `data_dir/plugins/<id>` 解，解到一半失败（磁盘满、zip 损坏）会留下
**半个插件**：它有目录、可能还有 manifest，于是被 discover 扫到 ——
而它的代码是残缺的。临时目录 + 原子 rename 让「出现」这件事只有两种结果：
完整出现，或完全不出现。临时目录必须与目标**同一个文件系统**
（rename 不能跨设备），故放在同一父目录下、以点号开头（discover 只扫非隐藏目录）。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.errors import ManifestError, ValidationError
from core.logging import get_logger
from core.plugins.validate import validate_manifest

log = get_logger("kernel.plugins.package")

#: 上传包体积上限（32 MiB）。插件是代码+少量静态资源，超过这个量级基本是误传。
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024

#: 解压后总体积上限（128 MiB）——防 zip bomb：压缩比可以做到 1000:1。
MAX_EXTRACTED_BYTES = 128 * 1024 * 1024

#: 条目数上限——防「十万个空文件」把 inode 打满。
MAX_ENTRIES = 4096

#: 单文件路径深度上限（相对 zip 根）。
MAX_DEPTH = 8

#: 落地时必须存在的文件。
REQUIRED_FILES = ("manifest.json",)


@dataclass
class UnpackResult:
    """一次成功的解包结果。"""

    plugin_id: str
    directory: Path
    manifest: dict[str, Any] = field(default_factory=dict)


class PluginPackageError(ValidationError):
    """插件包不合法（尺寸/形状/契约）。

    ★ 继承 `ValidationError`（→ 422）而不是 500：
      这是**输入**不合格，不是服务器出错。用户能据此改包重传。
    """


def _safe_member_target(tmp_root: Path, name: str) -> Path:
    """算出 zip 条目该解到哪儿；任何越界形状 ⇒ 抛。

    ★ 四类形状必须在**解压前**判掉（解压后判等于已经写进去了）：
      · 绝对路径 `/etc/cron.d/x`、盘符 `C:\\…`、UNC `\\\\server\\share`
      · `..` 段（zip slip：`../../../.ssh/authorized_keys`）
      · 反斜杠分隔（Windows 造出来的 zip，`..\\..\\` 与 `../` 等价）
      · 空/纯目录条目之外的可疑名字（NUL 字节）
    """
    if not name or "\x00" in name:
        raise PluginPackageError("插件包里存在非法文件名（空名或含 NUL 字节）")
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("//"):
        raise PluginPackageError(f"插件包含绝对路径条目：{name!r}（zip slip）")
    if len(normalized) >= 2 and normalized[1] == ":":
        raise PluginPackageError(f"插件包含盘符条目：{name!r}")
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise PluginPackageError(f"插件包含上跳路径条目：{name!r}（zip slip）")
    if len(parts) > MAX_DEPTH:
        raise PluginPackageError(f"插件包目录层级过深（>{MAX_DEPTH}）：{name!r}")
    target = tmp_root.joinpath(*parts)
    # 双保险：规范化后仍须在临时根内（挡符号链接等 resolve 才会暴露的形态）
    try:
        if not target.resolve().is_relative_to(tmp_root.resolve()):
            raise PluginPackageError(f"插件包条目越出解压根：{name!r}")
    except (OSError, RuntimeError) as exc:
        raise PluginPackageError(f"插件包条目路径无法解析：{name!r}（{exc}）") from exc
    return target


def _check_archive_shape(zf: zipfile.ZipFile) -> None:
    """解压前的形状闸：条目数、声明体积、符号链接、路径形状。"""
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES:
        raise PluginPackageError(
            f"插件包条目数 {len(infos)} 超过上限 {MAX_ENTRIES}"
        )
    declared = sum(i.file_size for i in infos)
    if declared > MAX_EXTRACTED_BYTES:
        raise PluginPackageError(
            f"插件包解压后体积 {declared} 字节超过上限 {MAX_EXTRACTED_BYTES}"
        )
    for info in infos:
        # 符号链接：zip 用外部属性存 unix mode，S_IFLNK = 0xA000
        mode = (info.external_attr >> 16) & 0xF000
        if mode == 0xA000:
            raise PluginPackageError(
                f"插件包含符号链接条目：{info.filename!r}"
                "（可指向包外任意位置，一律拒收）"
            )


def _extract(zf: zipfile.ZipFile, tmp_root: Path) -> None:
    """安全解压：逐条算目标路径、逐条写，累计写入量超限即中止。"""
    written = 0
    for info in zf.infolist():
        target = _safe_member_target(tmp_root, info.filename)
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as dst:
            while True:
                chunk = src.read(1 << 16)
                if not chunk:
                    break
                written += len(chunk)
                # ★ 实际写入量也要卡：zip 头里的 file_size 是**声明值**，
                #   恶意包可以谎报（声明 1KB、实际吐 1GB）。
                if written > MAX_EXTRACTED_BYTES:
                    raise PluginPackageError(
                        f"插件包解压体积超过上限 {MAX_EXTRACTED_BYTES} 字节（疑似 zip bomb）"
                    )
                dst.write(chunk)


def _locate_plugin_root(tmp_root: Path) -> Path:
    """找到 manifest.json 所在的那一层作为插件根。

    ★ 允许两种包形状：
      · 平铺：`manifest.json` 在包根（`zip -r x.zip .`）
      · 单层包裹：`<id>/manifest.json`（`zip -r x.zip <id>/`，GitHub 下载默认形状）
      后者太常见了，不收就等于逼用户手工重打一次包。
    ★ 但不做「递归找」：多一层猜测就多一种「我以为你指的是那个」，
      而装错插件比让用户重打包更糟。
    """
    if (tmp_root / "manifest.json").is_file():
        return tmp_root
    subdirs = [p for p in tmp_root.iterdir() if p.is_dir() and not p.name.startswith(".")]
    if len(subdirs) == 1 and (subdirs[0] / "manifest.json").is_file():
        return subdirs[0]
    raise PluginPackageError(
        "插件包形状不合法：manifest.json 必须在包根，或在唯一的顶层目录下"
    )


def _read_manifest(root: Path) -> dict[str, Any]:
    import json

    mpath = root / "manifest.json"
    try:
        raw = json.loads(mpath.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PluginPackageError(f"manifest.json 不是合法 JSON：{exc}") from exc
    except OSError as exc:
        raise PluginPackageError(f"读取 manifest.json 失败：{exc}") from exc
    if not isinstance(raw, dict):
        raise PluginPackageError("manifest.json 根必须是对象")
    return raw


def unpack_plugin_zip(
    archive: bytes,
    dest_root: Path,
    *,
    expected_id: str | None = None,
) -> UnpackResult:
    """把一个插件 zip 落到 `dest_root/<id>`，返回结果。

    参数：
      archive      —— zip 字节流
      dest_root    —— 插件根（`data_dir/plugins`）
      expected_id  —— 断言插件 id（可选；上传端点用它防「说装 A 结果装了 B」）

    失败一律抛 `PluginPackageError`，且**不留任何残留**（临时目录在 finally 里清）。
    """
    if not archive:
        raise PluginPackageError("上传内容为空")
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise PluginPackageError(
            f"插件包体积 {len(archive)} 字节超过上限 {MAX_ARCHIVE_BYTES}"
        )

    dest_root = Path(dest_root)
    dest_root.mkdir(parents=True, exist_ok=True)

    # ★ 临时目录必须与目标同文件系统（rename 不能跨设备）⇒ 放在 dest_root 下，
    #   且以点号开头：发现层只扫非隐藏目录，做到一半的包不会被扫到。
    tmp_root = Path(tempfile.mkdtemp(prefix=".upload-", dir=str(dest_root)))
    try:
        try:
            with zipfile.ZipFile(_bytes_reader(archive)) as zf:
                _check_archive_shape(zf)
                _extract(zf, tmp_root)
        except zipfile.BadZipFile as exc:
            raise PluginPackageError(f"不是合法的 zip 包：{exc}") from exc

        plugin_root = _locate_plugin_root(tmp_root)
        manifest = _read_manifest(plugin_root)

        # ── 契约闸：复用内核的 manifest 校验（单一真相，不另写一套）──
        # ★ 必须把 ManifestError 转成 PluginPackageError（422）：
        #   ManifestError 的默认状态码是 500（「内核自身出错」），
        #   而这里不合格的是**用户上传的输入**，不是服务器故障。
        #   实测旧行为：上传一份缺 kernelApi 的 manifest ⇒ 422 变 500，
        #   用户看到「内部错误」，无从知道是自己包写错了。
        #   ⇒ 输入问题一律 422，且错误信息保留（精确到字段）。
        try:
            validate_manifest(manifest)
        except ManifestError as exc:
            raise PluginPackageError(str(exc)) from exc

        plugin_id = manifest.get("id")

        # ── 形状闸补一条：声明的 api 必须有 router.py ──
        # ★ 为什么在上传期就拒，而不是等 install 再炸：
        #   第三方插件的挂载路径是 `api/router.py`（discover.load_plugin_router），
        #   没有一个 router 文件的包**永远装不上**（install 会 500）。
        #   在上传期给出「缺 api/router.py」比在安装期抛内部错误诚实得多，
        #   而且此时还没有副作用（没落盘、没建表）。
        #   ★ 只对声明了 api 的包要求：container 型本就可以没有 api。
        if manifest.get("kind") == "third-party" and manifest.get("api") is not None:
            if not (plugin_root / "api" / "router.py").is_file():
                raise PluginPackageError(
                    "插件包缺少 api/router.py：第三方插件靠它提供路由，"
                    "没有这个文件装上也无法挂载"
                )
        dir_name = plugin_root.name
        # ★ id 与目录名一致：discover 层也这么要求（`_read_manifest`）。
        #   平铺包里目录名是临时名，故这里以 manifest.id 为准重建目录名。
        if expected_id is not None and plugin_id != expected_id:
            raise PluginPackageError(
                f"插件包声明的 id={plugin_id!r} 与请求的 {expected_id!r} 不一致"
            )

        # kind 闸：上传通道只服务第三方插件（与 /install 同口径）
        if manifest.get("kind") in ("core", "builtin"):
            raise PluginPackageError(
                f"插件包 kind={manifest.get('kind')!r}：core/builtin 随系统发布，"
                "不能通过上传安装（只有 third-party / container 可以）"
            )

        target = dest_root / str(plugin_id)
        # ★ 已有同 id ⇒ 拒，不覆盖（v1 验收不含原地升级，见范围决策文件）。
        #   覆盖会让「升级失败」直接变成「插件没了」。
        if target.exists():
            raise PluginPackageError(
                f"插件 {plugin_id!r} 已存在于 {target}："
                "请先卸载旧版本再安装（本版本不支持原地升级）"
            )

        # 平铺包：临时目录**本身**就是插件根，直接把它 rename 成目标目录。
        #
        # ★★ 这里修过一个真 bug（r5 · 2026-11-15）：
        #   旧写法是 `os.replace(tmp_root, tmp_root / f".staged-{id}")` ——
        #   把目录**移进它自己**。Windows 报 `OSError: [WinError 87] 参数错误`，
        #   POSIX 的 rename 同样拒（EINVAL）。后果是**平铺包根本装不上**，
        #   而平铺恰恰是 `zip -r x.zip .` 的天然形状（用户最可能的操作），
        #   单层包裹（`zip -r x.zip <id>/`）反而能过 ⇒ 测试也漏了这条
        #   （existing 用例只覆盖包裹形状）。
        #   ⇒ 正确做法：临时目录与目标目录**同为 dest_root 的子项**，
        #     直接一个同文件系统的原子 rename 即可，不需要中间名。
        staged = plugin_root
        moved_root = staged == tmp_root
        if not moved_root and staged.name != str(plugin_id):
            # 包裹层目录名与 id 不一致（如 GitHub 下载的 `<repo>-main/`）：
            # 先在临时目录内改名，再整体 rename 出去（同一文件系统，安全）。
            renamed = tmp_root / f".staged-{plugin_id}"
            os.replace(staged, renamed)
            staged = renamed

        os.replace(staged, target)
        log.info(
            "插件包已落地", extra={"plugin_id": plugin_id, "directory": str(target)}
        )
        return UnpackResult(plugin_id=str(plugin_id), directory=target, manifest=manifest)
    finally:
        # ★ finally 清临时目录：成功路径上它已空或已被整个 rename 走，
        #   失败路径上它装着半个插件 —— 两种都必须清掉。
        #   （rename 走的情况：路径已不存在，rmtree(ignore_errors=True) 是空操作。）
        shutil.rmtree(tmp_root, ignore_errors=True)


def _bytes_reader(data: bytes) -> Any:
    import io

    return io.BytesIO(data)


__all__ = [
    "MAX_ARCHIVE_BYTES",
    "MAX_ENTRIES",
    "MAX_EXTRACTED_BYTES",
    "PluginPackageError",
    "REQUIRED_FILES",
    "UnpackResult",
    "unpack_plugin_zip",
]
