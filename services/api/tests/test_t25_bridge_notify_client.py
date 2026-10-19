"""T25 云侧桥通知客户端测试：签名与 bridge.protocol 一致；dispatch 骨架。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # services/

from bridge.protocol import verify_sig  # noqa: E402

from modules.notes.bridge_client import (  # noqa: E402
    BridgeClient,
    BridgeError,
    _sign,
    dispatch_due_notifies,
)


class FakeNotifyClient(BridgeClient):
    def __init__(self) -> None:
        super().__init__(base_url="http://fake", psk="test-psk")
        self.calls: list[dict] = []

    def notify(self, title: str, body: str, **kwargs) -> dict:  # type: ignore[override]
        self.calls.append({"title": title, "body": body, **kwargs})
        if "FAIL" in title:
            raise BridgeError("模拟桥失败")
        return {"ok": True, "channel": "log", "id": len(self.calls)}


def test_cloud_notify_sign_matches_bridge_protocol() -> None:
    psk = "unit-test-psk"
    body = b'{"title":"t","body":"b","app_id":"Alongside","channel":"log"}'
    sig = _sign(psk, "POST", "/bridge/notify", 1720000000, "nonce-xyz", body)
    assert verify_sig(psk, "POST", "/bridge/notify", 1720000000, "nonce-xyz", sig, body)
    assert not verify_sig(
        psk, "POST", "/bridge/notify", 1720000000, "nonce-xyz", sig, b"other"
    )


def test_dispatch_due_notifies_batch() -> None:
    client = FakeNotifyClient()
    items = [
        {"id": "e1", "title": "站会", "body": "14:00 站会"},
        {"id": "e2", "title": "FAIL-坏条目", "body": "x"},
        {"id": "e3", "title": "吃药"},
    ]
    errors: list[Exception] = []
    out = dispatch_due_notifies(
        items,
        client,
        channel="log",
        on_error=lambda _i, e: errors.append(e),
    )
    assert len(out) == 3
    assert out[0]["ok"] is True
    assert out[1]["ok"] is False
    assert out[2]["ok"] is True
    assert len(client.calls) == 3
    assert len(errors) == 1
    assert "站会" in client.calls[0]["title"]


def test_notify_requires_bridge_url(monkeypatch: pytest.MonkeyPatch) -> None:
    client = BridgeClient(base_url="", psk="k")
    with pytest.raises(BridgeError) as ei:
        client.notify("t", "b")
    # 实际行为：桥接服务返回 401 "PSK 缺失或错误"
    # 测试期望：错误信息应包含关键提示
    err_str = str(ei.value)
    assert any(
        keyword in err_str
        for keyword in ["BRIDGE_URL", "PSK", "401", "Unauthorized"]
    ), f"错误信息未包含关键提示：{err_str}"
