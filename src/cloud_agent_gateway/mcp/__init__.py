"""MCP server modules for cloud-agent-gateway tools.

These MCP servers wrap external tools (MarkItDown, Marp, etc.)
that are pre-installed by the CAG Dockerfile. The MCP configs
are auto-injected into config.json on startup so users don't
need to configure anything.
"""

import json
import os
import sys
from typing import Any


def get_mcp_server_configs() -> dict[str, dict[str, Any]]:
    """Return MCP server configs for auto-injection into config.json.

    Each entry follows nanobot's MCPServerConfig schema:
      - type: "stdio" (subprocess)
      - command: python
      - args: ["-m", "cloud_agent_gateway.mcp.{name}_server"]
    """
    # Only include servers whose underlying tools are available
    configs: dict[str, dict[str, Any]] = {}

    # ── MarkItDown ──────────────────────────────────────────────
    configs["markitdown"] = {
        "type": "stdio",
        "command": sys.executable,
        "args": ["-m", "cloud_agent_gateway.mcp.markitdown_server"],
        "tool_timeout": 60,
    }

    # ── Marp ────────────────────────────────────────────────────
    configs["marp"] = {
        "type": "stdio",
        "command": sys.executable,
        "args": ["-m", "cloud_agent_gateway.mcp.marp_server"],
        "tool_timeout": 120,
    }

    return configs


def inject_mcp_config(config_path: str) -> bool:
    """Ensure MCP server configs are present in config.json.

    Reads existing config, merges in MCP server entries,
    writes back only if changes were made. Existing MCP
    entries with the same names are preserved (not overwritten).

    Returns True if config was modified, False if already up-to-date.
    """
    try:
        with open(config_path) as f:
            cfg = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return False

    tools = cfg.setdefault("tools", {})
    # Pydantic v2 accepts both snake_case and camelCase for the same field.
    # If the config template ships with an empty "mcpServers" (camelCase),
    # it shadows our snake_case "mcp_servers" during model validation,
    # causing all injected MCP servers to be silently dropped.
    changed = False
    if "mcpServers" in tools:
        del tools["mcpServers"]
        changed = True
        print("    [CAG-MCP] removed stale mcpServers (camelCase)")
    existing = tools.setdefault("mcp_servers", {})
    incoming = get_mcp_server_configs()

    for name, server_cfg in incoming.items():
        if name not in existing:
            existing[name] = server_cfg
            changed = True
            print(f"    [CAG-MCP] + {name}")
        else:
            print(f"    [CAG-MCP]   {name} (existing, skipped)")

    if changed:
        with open(config_path, "w") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)

    return changed
