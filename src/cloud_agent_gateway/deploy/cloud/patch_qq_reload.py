#!/usr/bin/env python3
"""
Patch nanobot/channels/qq.py — auto-wait for account.json.

Mirrors patch_feishu_reload.py / patch_dingtalk_reload.py approach:
- In start(), when app_id/secret are empty, poll ~/.nanobot/qq/account.json
  every 5s instead of immediately returning.
- This allows the web bind page to provide credentials at runtime
  without restarting nanobot.

Usage: python3 -m cloud_agent_gateway.deploy.cloud.patch_qq_reload
"""

import os as _os
import sys as _sys


# ═══════════════════════════════════════════════════════════
# Patch: Auto-wait for account.json in QQChannel.start()
# ═══════════════════════════════════════════════════════════

def _replace_once(text: str, old: str, new: str) -> str:
    """Replace first exact occurrence of old with new."""
    if old not in text:
        raise RuntimeError("Anchor not found in source file")
    if text.count(old) > 1:
        print("WARNING: anchor appears multiple times — verify patch correctness")
    return text.replace(old, new, 1)


def apply_patch(source: str, _context: str = "") -> str:
    """Patch qq.py to auto-wait for account.json."""

    # ── Patch: Replace "no creds → return" with "poll for account.json" ──
    _anchor = (
        '        if not self.config.app_id or not self.config.secret:\n'
        '            self.logger.error("app_id and secret not configured")\n'
        '            return\n'
        '\n'
        '        self._running = True'
    )
    _replacement = (
        '        if not self.config.app_id or not self.config.secret:\n'
        '            # ── cloud-agent-gateway: auto-wait for QQ account.json ──\n'
        '            _import_os = __import__("os")\n'
        '            _import_asyncio = __import__("asyncio")\n'
        '            _import_json = __import__("json")\n'
        '            _account_base = _import_os.environ.get(\n'
        '                "NANOBOT_ACCOUNT_BASE", _import_os.path.expanduser("~/.nanobot")\n'
        '            )\n'
        '            _account_path = _import_os.path.join(\n'
        '                _account_base, "qq", "account.json"\n'
        '            )\n'
        '            self.logger.info(\n'
        '                "No QQ credentials configured. Waiting for account.json from web bind…"\n'
        '            )\n'
        '            while not self.config.app_id or not self.config.secret:\n'
        '                if _import_os.path.exists(_account_path):\n'
        '                    try:\n'
        '                        with open(_account_path) as _f:\n'
        '                            _acc = _import_json.load(_f)\n'
        '                        _aid = _acc.get("app_id", "")\n'
        '                        _sec = _acc.get("secret", "")\n'
        '                        if _aid and _sec:\n'
        '                            self.config.app_id = _aid\n'
        '                            self.config.secret = _sec\n'
        '                            self.logger.info("Loaded QQ credentials from account.json")\n'
        '                            break\n'
        '                    except Exception as _e:\n'
        '                        self.logger.warning(f"Failed to load account.json: {_e}")\n'
        '                self.logger.info("Waiting for QQ account.json …")\n'
        '                await _import_asyncio.sleep(5)\n'
        '\n'
        '        self._running = True'
    )
    source = _replace_once(source, _anchor, _replacement)

    return source


def verify_patch(source: str) -> None:
    """Check that expected patch markers exist in the file."""
    markers = [
        "Waiting for QQ account.json",
        "Loaded QQ credentials from account.json",
        "self._running = True",
    ]
    for m in markers:
        if m not in source:
            print(f"⚠  Verification failed: marker not found: {m}")
        else:
            print(f"✓  Marker found: {m}")


# ═══════════════════════════════════════════════════════════
# CLI entry point
# ═══════════════════════════════════════════════════════════

_PATHS = [
    "/usr/local/lib/python3.12/site-packages/nanobot/channels/qq.py",
    "/app/nanobot/channels/qq.py",
]


def main() -> int:
    applied = 0
    for p in _PATHS:
        if not _os.path.exists(p):
            print(f"⏭  {p} not found, skipping")
            continue
        with open(p) as f:
            source = f.read()
        try:
            patched = apply_patch(source, p)
        except RuntimeError as e:
            print(f"❌ {p}: {e}")
            continue
        with open(p, "w") as f:
            f.write(patched)
        print(f"✅ Patched: {p}")
        verify_patch(patched)
        applied += 1
    return 0 if applied > 0 else 1


if __name__ == "__main__":
    _sys.exit(main())
