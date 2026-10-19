#!/usr/bin/env python
"""生成聚合 OpenAPI 到 contracts/openapi.json。

用法（在 services/api 目录下）：
    python scripts/gen_openapi.py

聚合所有已挂载模块的路由（含 /api/v1/<id>/*），产物即全项目 API 契约。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 让脚本能 import core / modules（services/api 在 sys.path）
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.app import create_app  # noqa: E402

_OUT = _ROOT.parent.parent / "contracts" / "openapi.json"


def main() -> int:
    app = create_app()
    spec = app.openapi()
    spec.setdefault("info", {}).setdefault("title", "Alongside API")
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    paths = list(spec.get("paths", {}).keys())
    print(f"✅ 已生成 {_OUT}（{len(paths)} 个路径）")
    for p in sorted(paths):
        print("   -", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
