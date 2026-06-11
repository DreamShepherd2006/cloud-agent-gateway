#!/usr/bin/env python3
"""
Cloud platform detection and setup bridge.

Called by entrypoint.sh to:
1. Detect the current cloud platform
2. Run platform-specific initialisation (env unfreeze, dataset pull, …)
3. Print shell variable assignments for ``eval`` back into entrypoint.sh

All diagnostics go to stderr — stdout MUST contain only shell exports
so that ``eval "$(python3 platform_setup.py)"`` works correctly.
"""

from __future__ import annotations

import os
import sys

from cloud_agent_gateway.platforms import platform


def _map_relay_token() -> str | None:
    """Map platform-specific SQUAD_RELAY_TOKEN_* → SQUAD_RELAY_TOKEN.

    After platform.setup() unfreezes env vars from /proc/1/environ,
    look for any SQUAD_RELAY_TOKEN_{PLATFORM}_{space} var and map
    its value to the generic SQUAD_RELAY_TOKEN that oauth_proxy.py
    and gatekeeper.py expect.

    Skips placeholder values (var name == value) and already-set tokens.
    Returns the export string to print, or None.
    """
    if os.environ.get("SQUAD_RELAY_TOKEN"):
        return None  # already mapped by platform-specific setup

    for key in sorted(os.environ):
        if not key.startswith("SQUAD_RELAY_TOKEN_"):
            continue
        val = os.environ[key]
        if not val or val == key:
            continue  # empty or placeholder
        os.environ["SQUAD_RELAY_TOKEN"] = val
        sys.stderr.write(
            f"[platform_setup] \U0001f511 SQUAD_RELAY_TOKEN mapped from {key}\n"
        )
        return f"export SQUAD_RELAY_TOKEN='{val}'"

    sys.stderr.write(
        "[platform_setup] \u26a0\ufe0f no valid SQUAD_RELAY_TOKEN_* found\n"
    )
    return None


def main() -> None:
    sys.stderr.write(f"[platform_setup] detected: {platform.name}\n")
    sys.stderr.write(f"[platform_setup] data_root: {platform.data_root}\n")

    # Run platform-specific setup and print shell exports to stdout
    shell_exports = platform.setup()

    # Map relay token after platform setup unfreezes env vars.
    # Works for ALL platforms (Squad + Cloud Demo, HF + ModelScope).
    relay_export = _map_relay_token()

    if shell_exports or relay_export:
        print(f"export DEPLOY_PLATFORM='{platform.name}'")
        if shell_exports:
            print(shell_exports)
        if relay_export:
            print(relay_export)

    sys.stderr.write("[platform_setup] done\n")
    sys.stderr.flush()


if __name__ == "__main__":
    main()
