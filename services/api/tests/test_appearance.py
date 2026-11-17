"""外观与皮肤模块的探针测试。

★ 只测 /health：皮肤的真相在浏览器（localStorage + CSS 变量），
  后端没有业务可测；这里守的是「模块能被内核挂上、探针可用」。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.appearance.router import router


def test_health() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/appearance")
    client = TestClient(app)

    resp = client.get("/api/v1/appearance/health")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
