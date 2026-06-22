#!/usr/bin/env python3
"""Patch nanobot weixin channel: runtime token reload, auto-start, race guard.

Canonical source: cloud-agent-gateway/src/cloud_agent_gateway/deploy/cloud/patch_weixin_reload.py
Installed via: pip install cloud-agent-gateway
Invocation:    python3 -m cloud_agent_gateway.deploy.cloud.patch_weixin_reload

Patches nanobot/channels/weixin.py (both /app and site-packages copies).

Patches applied (5 total, simplified from original 9):
  P2 - chunked sleep during session pause (5s intervals)
  P4 - auto-wait for account.json on first start
  P5 - NANOBOT_ACCOUNT_BASE env var priority over config state_dir
  P7 - 3-field change detection (token/buf/ctx) in _session_pause_remaining_s
  P9 - _save_state() guard against overwriting external binding writes

Removed (unnecessary):
  P1/P1b/P3 - httpx >= 0.28 supports self._client.timeout = … reassignment
  P6 - redundant: P7 detects changes in both active and paused modes
  P8 - cosmetic: CancelledError means process is exiting anyway
"""

import re
import sys
from pathlib import Path


def _target_paths() -> list[Path]:
    """Return weixin.py copies that need patching, in priority order."""
    candidates: list[Path] = []

    # 1. /app/nanobot/ (PYTHONPATH takes precedence)
    app = Path("/app/nanobot/channels/weixin.py")
    if app.exists():
        candidates.append(app)

    # 2. site-packages (pip install)
    for p in Path("/usr/local/lib").rglob("**/nanobot/channels/weixin.py"):
        if p.exists() and p not in candidates:
            candidates.append(p)

    # 3. ~/.local fallback
    home = Path.home()
    for p in home.rglob(".local/**/nanobot/channels/weixin.py"):
        if p.exists() and p not in candidates:
            candidates.append(p)

    return candidates


def _replace_once(content: str, old: str, new: str) -> str:
    """Replace exactly one occurrence; raise on zero or multiple."""
    count = content.count(old)
    if count == 0:
        raise RuntimeError(f"Pattern not found (0 matches):\n  {repr(old[:80])}")
    if count > 1:
        raise RuntimeError(
            f"Pattern ambiguous ({count} matches):\n  {repr(old[:80])}\n"
            f"Add more context to narrow to 1 match."
        )
    return content.replace(old, new, 1)


def apply_patch(source: str) -> str:
    """Apply all weixin patches to source text, return patched."""

    # ── Patch 2: Chunked sleep during session pause ──────────────────
    # Replace single asyncio.sleep(remaining) with 5s chunks so P7's
    # change detection in _session_pause_remaining_s() gets a chance
    # to run during the pause period.
    source = _replace_once(
        source,
        "        remaining = self._session_pause_remaining_s()\n"
        "        if remaining > 0:\n"
        "            await asyncio.sleep(remaining)\n"
        "            return",
        "        remaining = self._session_pause_remaining_s()\n"
        "        if remaining > 0:\n"
        "            # Chunked sleep to detect token changes from web bind\n"
        "            while remaining > 0:\n"
        "                _chunk = min(5, remaining)\n"
        "                await asyncio.sleep(_chunk)\n"
        "                remaining = self._session_pause_remaining_s()\n"
        "            self._load_state()\n"
        "            return",
    )

    # ── Patch 4: Auto-wait for account.json in start() ──────────────
    # When token is empty and no saved state is found, poll every 5s
    # instead of immediately failing with QR login.
    _anchor_start = (
        '        if self.config.token:\n'
        '            self._token = self.config.token\n'
        '        elif not self._load_state():\n'
        '            if not await self._qr_login():\n'
        '                self.logger.error("login failed. Run \'nanobot channels login weixin\' to authenticate.")\n'
        '                self._running = False\n'
        '                return\n'
        '\n'
        '        self.logger.info("channel starting with long-poll...")'
    )
    _replacement_start = (
        '        if self.config.token:\n'
        '            self._token = self.config.token\n'
        '        elif not self._load_state():\n'
        '            # ── cloud-agent-gateway: auto-wait for account.json ──\n'
        '            self.logger.info("No token or saved state found. Waiting for account.json from web bind…")\n'
        '            while not self._load_state():\n'
        '                self.logger.info("Waiting for account.json …")\n'
        '                await asyncio.sleep(5)\n'
        '            self.logger.info("account.json found! Starting channel.")\n'
        '            if not self._load_state():\n'
        '                self.logger.error("login failed. Run \'nanobot channels login weixin\' to authenticate.")\n'
        '                self._running = False\n'
        '                return\n'
        '\n'
        '        self.logger.info("channel starting with long-poll...")'
    )
    source = _replace_once(source, _anchor_start, _replacement_start)

    # ── Patch 5: NANOBOT_ACCOUNT_BASE takes priority over state_dir ──
    # NANOBOT_ACCOUNT_BASE (set by entrypoint.sh / launch.sh) wins to
    # ensure nanobot reads credentials from PersistentStorageProtocol path.
    # config.json's state_dir acts as fallback when the env var is unset.
    _anchor_state_dir = (
        "        if self.config.state_dir:\n"
        '            d = Path(self.config.state_dir).expanduser()\n'
        "        else:\n"
        '            d = get_runtime_subdir("weixin")\n'
    )
    _replacement_state_dir = (
        "        _account_base = os.environ.get(\"NANOBOT_ACCOUNT_BASE\")\n"
        "        if _account_base:\n"
        '            d = Path(_account_base) / "weixin"\n'
        "        elif self.config.state_dir:\n"
        '            d = Path(self.config.state_dir).expanduser()\n'
        "        else:\n"
        '            d = get_runtime_subdir("weixin")\n'
    )
    source = _replace_once(source, _anchor_state_dir, _replacement_state_dir)

    # ── Patch 7: Detect account.json changes in _session_pause_remaining_s ──
    # Runs at the start of every poll cycle (both active and paused).
    # Compares 3 fields (token, get_updates_buf, context_tokens) to detect
    # external writes like web binding clearing session cache.
    # typing_tickets excluded: _save_state() often writes it out-of-sync.
    _anchor_pause_remaining = (
        "    def _session_pause_remaining_s(self) -> int:\n"
        "        remaining = int(self._session_pause_until - time.time())\n"
        "        if remaining <= 0:\n"
        "            self._session_pause_until = 0.0\n"
        "            return 0\n"
        "        return remaining\n"
    )
    _replacement_pause_remaining = (
        "    def _session_pause_remaining_s(self) -> int:\n"
        "        # cloud-agent-gateway: detect external changes to account.json\n"
        "        _rem = int(self._session_pause_until - time.time())\n"
        '        _sf = self._get_state_dir() / "account.json"\n'
        "        try:\n"
        "            if _sf.exists():\n"
        "                _mt = _sf.stat().st_mtime\n"
        "                _last = getattr(self, \"_cag_account_mtime\", 0)\n"
        "                if _mt != _last:\n"
        "                    self._cag_account_mtime = _mt\n"
        "                    _d = json.loads(_sf.read_text())\n"
        '                    _tk = _d.get("token", "")\n'
        "                    _diff_tk = (_tk and _tk != self._token)\n"
        '                    _diff_buf = _d.get("get_updates_buf", "") != getattr(self, "_get_updates_buf", "")\n'
        '                    _diff_ctx = _d.get("context_tokens", {}) != getattr(self, "_context_tokens", {})\n'
        "                    _changed = _diff_tk or _diff_buf or _diff_ctx\n"
        "                    if _changed:\n"
        '                        self.logger.info("account.json changed externally, clearing pause + reload")\n'
        "                        self._load_state()\n"
        "                        self._session_pause_until = 0.0\n"
        "                        return 0\n"
        "        except Exception:\n"
        "            pass\n"
        "        remaining = int(self._session_pause_until - time.time())\n"
        "        if remaining <= 0:\n"
        "            self._session_pause_until = 0.0\n"
        "            return 0\n"
        "        return remaining\n"
    )
    source = _replace_once(source, _anchor_pause_remaining, _replacement_pause_remaining)

    # ── Patch 9: _save_state() external change guard ─────────────────
    # During active polling, _save_state() writes every ~5s and can overwrite
    # binding writes (write_credential). Check mtime before writing; if token
    # changed externally, reload from disk instead of overwriting.
    _anchor_save_state = (
        "    def _save_state(self) -> None:\n"
        '        state_file = self._get_state_dir() / "account.json"\n'
        "        with suppress(Exception):\n"
        "            data = {\n"
        '                "token": self._token,\n'
        '                "get_updates_buf": self._get_updates_buf,\n'
        '                "context_tokens": self._context_tokens,\n'
        '                "typing_tickets": self._typing_tickets,\n'
        '                "base_url": self.config.base_url,\n'
        "            }\n"
        "            state_file.write_text(json.dumps(data, ensure_ascii=False))\n"
    )
    _replacement_save_state = (
        "    def _save_state(self) -> None:\n"
        '        _sf = self._get_state_dir() / "account.json"\n'
        '        # cloud-agent-gateway: detect binding write before overwriting\n'
        "        if hasattr(self, '_cag_last_save_mtime') and _sf.exists():\n"
        "            _disk_mtime = _sf.stat().st_mtime\n"
        "            if _disk_mtime != self._cag_last_save_mtime:\n"
        "                with suppress(Exception):\n"
        "                    _disk = json.loads(_sf.read_text())\n"
        '                    _disk_tk = _disk.get("token", "")\n'
        "                    if _disk_tk and _disk_tk != self._token:\n"
        '                        self.logger.info("account.json externally modified, reloading")\n'
        "                        self._load_state()\n"
        "                        self._cag_last_save_mtime = _sf.stat().st_mtime\n"
        "                        return\n"
        "        with suppress(Exception):\n"
        "            data = {\n"
        '                "token": self._token,\n'
        '                "get_updates_buf": self._get_updates_buf,\n'
        '                "context_tokens": self._context_tokens,\n'
        '                "typing_tickets": self._typing_tickets,\n'
        '                "base_url": self.config.base_url,\n'
        "            }\n"
        "            _sf.write_text(json.dumps(data, ensure_ascii=False))\n"
        "            self._cag_last_save_mtime = _sf.stat().st_mtime\n"
    )
    source = _replace_once(source, _anchor_save_state, _replacement_save_state)

    return source


def verify_patch(source: str) -> None:
    """Check that expected patch markers exist in the file."""
    markers = [
        "Waiting for account.json from web bind",       # P4
        'os.environ.get("NANOBOT_ACCOUNT_BASE")',      # P5
        "_cag_account_mtime",                           # P7
        "_cag_last_save_mtime",                         # P9
        # Verify removed patches are NOT present
    ]
    for m in markers:
        if m not in source:
            print(f"⚠  Verification failed: marker not found: {m}")
            sys.exit(1)
    # Verify unwanted markers are absent
    removed = [
        "timeout: float | None = None",   # P1 removed
        "_last_token = self._token",      # P6 removed
        "except BaseException:",          # P8 removed
    ]
    for m in removed:
        if m in source:
            print(f"⚠  Verification failed: removed patch marker still present: {m}")
            sys.exit(1)
    print("✓ All patch markers verified (5 patches: P2, P4, P5, P7, P9)")


def main() -> None:
    """Find and patch all weixin.py copies."""
    targets = _target_paths()
    if not targets:
        print("✗ No weixin.py found — nothing to patch")
        sys.exit(1)

    print(f"Found {len(targets)} weixin.py target(s):")
    for t in targets:
        print(f"  {t}")

    for target in targets:
        print(f"\n── Patching {target} ──")
        original = target.read_text(encoding="utf-8")
        patched = apply_patch(original)
        verify_patch(patched)
        target.write_text(patched, encoding="utf-8")
        print(f"✓ Patched {target}")

    print("\n✓ All weixin patches applied successfully")


if __name__ == "__main__":
    main()
