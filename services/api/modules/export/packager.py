"""· 搬家包导出（V1：manifest+checksum，密钥绝不落包）。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

PROFILES = {
    "full": "数据 + 文档树",
    "data-only": "仅业务数据 JSONL 摘要",
    "docs-only": "仅 docs 树路径清单",
}

SECRET_KEYS = {
    "SECRET_KEY",
    "ADMIN_PASSWORD_HASH",
    "TOTP_SECRET",
    "PUSH_VAPID_PRIVATE_KEY",
    "BRIDGE_PSK",
    "BEECOUNT_MCP_TOKEN",
    "CF_API_TOKEN",
}


def redact_settings(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in raw.items():
        if k in SECRET_KEYS or any(s in k.upper() for s in ("SECRET", "PASSWORD", "TOKEN", "PSK")):
            out[k] = "***REDACTED***"
        else:
            out[k] = v
    return out


def build_manifest(profile: str, module_ids: list[str], counts: dict[str, int]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile,
        "modules": module_ids,
        "counts": counts,
        "secret_policy": "omit+redact",
        "checksum": "sha256-of-payload-files",
    }


def checksum_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def summarize_payload(files: dict[str, str]) -> dict[str, Any]:
    """files: path -> text；返回逐文件 sha256 清单（供回滚校验）。"""
    return {path: checksum_of(text) for path, text in sorted(files.items())}


def package_preview(profile: str, db_summary: dict[str, Any]) -> dict[str, Any]:
    """只读预览包结构（不写盘）；真正 zip 落盘候。"""
    if profile not in PROFILES:
        raise KeyError(profile)
    files: dict[str, str] = {
        "README.txt": "Alongside export\nsecret_policy=omit+redact\n",
        "manifest.json": json.dumps(
            build_manifest(profile, sorted(db_summary), {k: 0 for k in db_summary}),
            ensure_ascii=False,
            indent=2,
        ),
        "data/summary.json": json.dumps(redact_settings(db_summary), ensure_ascii=False),
    }
    return {
        "profile": profile,
        "files": sorted(files),
        "checksums": summarize_payload(files),
        "note": "V1 预览；zip 落盘候派（不自动写备份目录）",
    }
