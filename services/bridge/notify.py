"""本机通知投递（T24）。

★ 用途：Alongside 云侧或本机调度经桥请求 → Windows 桌面通知。
★ 通道优先级：
   1. winotify（若已安装，体验最好）
   2. PowerShell + Windows.UI.Notifications Toast（系统自带，零新依赖）
   3. 后备：仅记日志（测试/非 Windows/两者都不可用时仍返回结构化结果）
★ 安全：不打印完整通知正文到日志（只记长度与标题前缀）。
"""
from __future__ import annotations

import logging
import platform
import subprocess
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("bridge.notify")

DEFAULT_APP_NAME = "Alongside"


@dataclass
class NotifyResult:
    ok: bool
    channel: str  # winotify | powershell | log | disabled
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "channel": self.channel, "detail": self.detail}


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _notify_winotify(title: str, body: str, app_id: str) -> NotifyResult:
    from winotify import Notification, audio  # type: ignore

    toast = Notification(
        app_id=app_id,
        title=title,
        msg=body,
        duration="default",
    )
    toast.set_audio(audio.Default, loop=False)
    toast.show()
    return NotifyResult(ok=True, channel="winotify")


def _notify_powershell(title: str, body: str, app_id: str) -> NotifyResult:
    # WinRT 类型加载行是 PowerShell 单行语句（不许换行），太长 → 抽成常量用隐式拼接保持源行 <100
    _winrt_toast = (
        "[Windows.UI.Notifications.ToastNotificationManager,"
        " Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null"
    )
    _winrt_xml = (
        "[Windows.Data.Xml.Dom.XmlDocument,"
        " Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null"
    )
    t = _xml_escape(title)
    b = _xml_escape(body)
    a = _xml_escape(app_id)
    # ToastGeneric 模板；AppId 需与注册的 AUMID 一致，未注册时系统仍可能显示
    ps = f"""
{_winrt_toast}
{_winrt_xml}
$template = @"
<toast>
  <visual>
    <binding template="ToastGeneric">
      <text>{t}</text>
      <text>{b}</text>
    </binding>
  </visual>
</toast>
"@
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = New-Object Windows.UI.Notifications.ToastNotification($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("{a}").Show($toast)
"""
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:200]
        return NotifyResult(ok=False, channel="powershell", detail=err or f"exit={proc.returncode}")
    return NotifyResult(ok=True, channel="powershell")


def send_windows_notification(
    title: str,
    body: str,
    *,
    app_id: str | None = None,
    channel: str = "auto",
) -> NotifyResult:
    """发送一条桌面通知。channel: auto | winotify | powershell | log。"""
    app = app_id or DEFAULT_APP_NAME
    title = (title or "").strip() or "Alongside"
    body = (body or "").strip()
    if not body:
        return NotifyResult(ok=False, channel="log", detail="body 为空")

    if channel == "log":
        log.info("notify(log): %s | %s…", title, body[:40])
        return NotifyResult(ok=True, channel="log")

    if channel == "auto":
        if platform.system() != "Windows":
            log.info("notify(non-windows fallback): %s | %s…", title, body[:40])
            return NotifyResult(ok=True, channel="log", detail="non-windows")
        try:
            return _notify_winotify(title, body, app)
        except Exception as exc:  # noqa: BLE001 — 通道降级
            log.warning("winotify 不可用，降级 PowerShell: %s", exc)
            try:
                return _notify_powershell(title, body, app)
            except Exception as exc2:  # noqa: BLE001
                log.warning("powershell toast 不可用，降级 log: %s", exc2)
                log.info("notify(log-fallback): %s | %s…", title, body[:40])
                return NotifyResult(ok=True, channel="log", detail=str(exc2)[:200])

    if channel == "winotify":
        return _notify_winotify(title, body, app)
    if channel == "powershell":
        return _notify_powershell(title, body, app)
    return NotifyResult(ok=False, channel="log", detail=f"未知 channel={channel}")
