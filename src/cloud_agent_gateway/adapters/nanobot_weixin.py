"""Adapter: nanobot.channels.weixin → cloud-agent-gateway's internal API.

This is the ONLY file in the framework that imports from nanobot's
weixin channel. When upstream changes WeixinChannel or WeixinConfig,
update the re-exports here — no other files need modification.

Tested: nanobot v0.2.0 (commit 92f2ff3, nightly branch)
"""

from __future__ import annotations

import warnings

from nanobot.channels.weixin import WeixinChannel, WeixinConfig

__all__ = ["WeixinChannel", "WeixinConfig"]

# ── Version guard ───────────────────────────────────────────
_EXPECTED_NANOBOT_VERSION = "0.2.1"

try:
    from nanobot import __version__ as _nanobot_version
except Exception:
    _nanobot_version = None

if _nanobot_version is not None and _nanobot_version != _EXPECTED_NANOBOT_VERSION:
    warnings.warn(
        f"nanobot version mismatch in nanobot_weixin adapter: "
        f"expected {_EXPECTED_NANOBOT_VERSION}, got {_nanobot_version}. "
        f"WeixinChannel/WeixinConfig may have changed.",
        RuntimeWarning,
    )
