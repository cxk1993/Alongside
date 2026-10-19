"""pi-agent 测试配置（★ 插件自持，不污染项目级 pyproject.toml）。

`live` marker = 真机测试（需已装 pi + alongside 路由可达）。
默认（不带 -m）**不会**跑 live 测试，避免 CI/无网环境误报。
"""
from __future__ import annotations


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: 真机测试（需 pi 可执行 + alongside 模型路由可达）",
    )
