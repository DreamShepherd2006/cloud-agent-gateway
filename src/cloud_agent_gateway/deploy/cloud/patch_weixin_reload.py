#!/usr/bin/env python3
"""Patch nanobot weixin channel: runtime token reload, timeout fix, auto-start.

Canonical source: cloud-agent-gateway/src/cloud_agent_gateway/deploy/cloud/patch_weixin_reload.py
Installed via: pip install cloud-agent-gateway
Invocation:    python3 -m cloud_agent_gateway.deploy.cloud.patch_weixin_reload

Patches nanobot/channels/weixin.py (both /app and site-packages copies).
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

    # ── Patch 1: Add timeout param to _api_post ──────────────────
    # Anchor: last line of _api_post signature
    source = _replace_once(
        source,
        "        auth: bool = True,\n"
        "    ) -> dict:",
        "        auth: bool = True,\n"
        "        timeout: float | None = None,\n"
        "    ) -> dict:",
    )

    # Patch 1b: pass timeout to httpx
    source = _replace_once(
        source,
        "headers=self._make_headers(auth=auth))",
        "headers=self._make_headers(auth=auth), timeout=timeout)",
    )

    # ── Patch 2: Reload state after pause in _poll_once ──────────
    # After sleep(remaining), reload state to clear stale tokens
    source = _replace_once(
        source,
        "            await asyncio.sleep(remaining)\n"
        "            return",
        "            await asyncio.sleep(remaining)\n"
        "            self._load_state()\n"
        "            return",
    )

    # ── Patch 3: Remove httpx.Timeout assignment (broken on some versions) ──
    # Delete the self._client.timeout = httpx.Timeout(...) line
    source = _replace_once(
        source,
        "        # Adjust httpx timeout to match the current poll timeout\n"
        "        assert self._client is not None\n"
        '        self._client.timeout = httpx.Timeout(self._next_poll_timeout_s + 10, connect=30)\n'
        "\n"
        '        data = await self._api_post("ilink/bot/getupdates", body)',
        "        # Adjust httpx timeout to match the current poll timeout\n"
        "        assert self._client is not None\n"
        "        # (httpx.Timeout assignment removed — cloud-agent-gateway patch)\n"
        "\n"
        '        data = await self._api_post("ilink/bot/getupdates", body,'
        ' timeout=self._next_poll_timeout_s + 10)',
    )

    # ── Patch 4: Auto-wait for account.json in start() ────────────
    # When token is empty and no saved state is found, poll every 5s
    # instead of immediately failing.
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

    # ── Patch 6: Auto-reload on account.json mtime change ─────────
    # When gatekeeper writes a new account.json after web binding, the
    # channel's long-poll loop detects the mtime change and reloads state
    # without needing a restart.
    _anchor_mtime = (
        '        self.logger.info("channel starting with long-poll...")\n'
        '\n'
        '        consecutive_failures = 0\n'
        '        while self._running:\n'
    )
    _replacement_mtime = (
        '        self.logger.info("channel starting with long-poll...")\n'
        '\n'
        '        _state_file = self._get_state_dir() / "account.json"\n'
        '        _account_mtime = _state_file.stat().st_mtime if _state_file.exists() else 0\n'
        '\n'
        '        consecutive_failures = 0\n'
        '        while self._running:\n'
        '            # ── cloud-agent-gateway: detect account.json update for hot-reload ──\n'
        '            if _state_file.exists():\n'
        '                _cur_mtime = _state_file.stat().st_mtime\n'
        '                if _cur_mtime != _account_mtime:\n'
        '                    self.logger.info("account.json changed, reloading state...")\n'
        '                    self._load_state()\n'
        '                    _account_mtime = _cur_mtime\n'
    )
    source = _replace_once(source, _anchor_mtime, _replacement_mtime)

    return source


def verify_patch(source: str) -> None:
    """Check that expected patch markers exist in the file."""
    markers = [
        "timeout: float | None = None",
        "headers=self._make_headers(auth=auth), timeout=timeout",
        "Waiting for account.json from web bind",
        "(httpx.Timeout assignment removed",
        'os.environ.get("NANOBOT_ACCOUNT_BASE")',  # _get_state_dir patch
        "self._load_state()",  # after asyncio.sleep
        "_account_mtime = _state_file.stat().st_mtime",  # Patch 6: mtime reload
    ]
    for m in markers:
        if m not in source:
            print(f"⚠  Verification failed: marker not found: {m}")
            sys.exit(1)
    print("✓ All patch markers verified")


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
