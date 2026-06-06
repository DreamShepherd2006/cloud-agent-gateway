"""cloud_agent_gateway — Channel binding protocol + registry.

Data-driven channel binding: add a new channel by dropping a module
into deploy/cloud/channel_bindings/ that registers a BindingSpec.
Framework code never imports per-channel binding logic directly.

This mirrors the PlatformSpec / CloudPlatformProtocol pattern:
the framework defines the contract, deploy-layer modules implement it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
import json
import os


# ══════════════════════════════════════════════════
# Protocol
# ══════════════════════════════════════════════════

@dataclass
class BindingSpec:
    """Data-driven binding spec — add channels without touching framework.

    Each route entry is (path_suffix, method, handler_func).
    handler_func: async callable(Request) -> Response.
    """
    name: str           # "wechat"
    display: str        # "微信"
    icon: str           # "🐱"

    bind_page_html: str = ""

    # (path_suffix, method, handler)
    public_routes: list = field(default_factory=list)
    internal_routes: list = field(default_factory=list)

    is_bound: Callable[[], bool] = lambda: False


# ══════════════════════════════════════════════════
# Registry (mirrors PlatformSpec pattern)
# ══════════════════════════════════════════════════

_registry: dict[str, BindingSpec] = {}


def register(spec: BindingSpec) -> None:
    """Register a channel binding implementation."""
    _registry[spec.name] = spec


def discover() -> list[BindingSpec]:
    """Import deploy-layer binding modules and return all registered specs.

    Called once at oauth_proxy startup. The import side-effect triggers
    register() in each binding module.
    """
    try:
        import cloud_agent_gateway.deploy.cloud.channel_bindings  # noqa: F401
    except ImportError:
        pass
    return list(_registry.values())


def bind_status() -> dict:
    """Aggregated binding status across all registered channels.

    Returns: {"wechat": {"bound": true}, "dingtalk": {"bound": false}, ...}
    """
    result = {}
    for spec in _registry.values():
        result[spec.name] = {"bound": spec.is_bound()}
    return result


# ══════════════════════════════════════════════════
# Shared helpers (used by binding modules)
# ══════════════════════════════════════════════════

def nanobot_home() -> str:
    return os.path.expanduser("~/.nanobot")


def config_path() -> str:
    return os.path.join(nanobot_home(), "config.json")


def load_json(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}
