"""
Cloud Platform Protocol — abstract interface for cloud-space deployment.

Each platform (HF Spaces, ModelScope, etc.) implements this protocol.
The core entrypoint depends only on this interface, never on platform specifics.

PlatformSpec (dataclass) follows the same data-driven registry pattern as
``nanobot.providers.registry.ProviderSpec`` — detection rules are pure data
so the registry can evaluate matches without importing platform implementations.

Note: The protocol includes optional Squad Legion extensions for
multi-agent orchestration (auth middleware, relay, commander).
Platform implementations may override these as needed; base stubs
return safe defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware


# ── PlatformSpec — data-driven registry entry (mirrors ProviderSpec) ──


@dataclass(frozen=True)
class PlatformSpec:
    """Metadata for one deployment platform.

    Like ``ProviderSpec`` for LLM providers, this is pure data — the registry
    evaluates detection rules without importing the platform implementation.
    """

    name: str
    display_name: str = ""
    module: str = ""  # relative import path, e.g. ".hf_spaces"

    # ── 三维坐标 (platform × engine × squad) ──
    platform: str = ""   # "hf" | "ms" | "docker" | "" (auto/any)
    engine: str = ""     # "nanobot" | "openclaw" | "" (any)
    squad: bool = False  # True = Squad Legion overlay present

    # Detection rules — evaluated in priority order by ``matches()``.
    detect_env: str = ""        # env var that must be set (e.g. "HF_SPACE")
    detect_env_value: str = ""  # optional exact value match for detect_env
    detect_env_alt: str = ""    # alternative env var (e.g. "SPACE_ID")
    detect_url_contains: str = ""  # substring in OIDC_CONFIG_URL
    detect_url_env: str = "OIDC_CONFIG_URL"
    detect_empty_url_is_match: bool = False

    priority: int = 50
    is_fallback: bool = False

    @property
    def label(self) -> str:
        return self.display_name or self.name

    def matches(self) -> bool:
        """Evaluate detection rules against the current environment.

        Detection layers:
        0) ``DEPLOY_PLATFORM`` explicit override
        1) Platform detection (env vars, URL patterns)
        2) Engine filter
        3) Squad filter
        """
        if self.is_fallback:
            return False

        # 0) Explicit override
        _deploy = os.environ.get("DEPLOY_PLATFORM", "")
        if _deploy:
            return self.name == _deploy

        # 1) Platform detection
        if not self._platform_matches():
            return False

        # 2) Engine filter
        if self.engine and not _detect_engine(self.engine):
            return False

        # 3) Squad filter
        if self.squad != _detect_squad():
            return False

        return True

    def _platform_matches(self) -> bool:
        """Check platform-level detection rules (env vars, URL patterns)."""
        # Structured env detection (with optional exact-value match)
        if self.detect_env:
            raw = os.environ.get(self.detect_env)
            if raw is not None:
                if self.detect_env_value:
                    return raw == self.detect_env_value
                return True

        # Alternative env detection
        if self.detect_env_alt and os.environ.get(self.detect_env_alt):
            return True

        # URL-based detection
        if self.detect_url_contains:
            url = os.environ.get(self.detect_url_env, "")
            if self.detect_url_contains.lower() in url.lower():
                return True
            if self.detect_empty_url_is_match and not url:
                return True

        return False


# ── CloudPlatformProtocol — cloud platform interface + Squad extensions ──


class CloudPlatformProtocol(Protocol):
    """Contract every cloud platform module must fulfil.

    Core methods (identity, filesystem, OAuth) are cloud-deployment concerns.
    Squad Legion extensions (auth middleware, relay, commander) are optional
    stubs that return safe defaults if not overridden.
    """

    # ── Identity ──
    @property
    def name(self) -> str:
        """Human-readable platform name (e.g. 'hf-spaces', 'modelscope')."""
        ...

    # ── OAuth ──
    def register_oauth(self) -> Any:
        """Initialise and return an OAuth client for this platform."""
        ...

    @property
    def login_route_path(self) -> str:
        """URL path for the login endpoint, e.g. '/login'."""
        ...

    @property
    def callback_route_path(self) -> str:
        """URL path for the OAuth callback, e.g. '/auth/callback'."""
        ...

    async def fetch_userinfo(self, token: dict) -> dict | None:
        """Fetch user profile from the identity provider's userinfo endpoint.

        Default: extract userinfo already embedded in token response.
        Override for platforms that need to re-fetch via HTTP.
        """
        return token.get("userinfo") if token else None

    def extract_username(self, userinfo: dict) -> str:
        """Extract the canonical username from a userinfo dict.

        Default (HF): preferred_username → username → name → "Unknown".
        Override for platforms with different field priorities.
        """
        return (
            userinfo.get("preferred_username")
            or userinfo.get("username")
            or userinfo.get("name")
            or "Unknown"
        )

    # ── Authorisation (Squad Legion extension) ──
    def get_commander_whitelist(self) -> list[str]:
        """Commander (admin) username whitelist."""
        ...

    def get_user_agent_map(self) -> dict[str, str]:
        """User → agent mapping dict."""
        ...

    def get_agent_for_user(self, username: str) -> str:
        """Resolve which agent a user should be routed to."""
        ...

    def is_commander(self, session_user: Any) -> bool:
        """Check whether the given session user has Commander privileges."""
        ...

    def check_relay_permission(self, sender: str, target: str) -> bool:
        """Validate whether *sender* is authorised to relay to *target*."""
        ...

    def is_member(self, username: str) -> bool:
        """Check whether *username* is a registered squad member.

        Default: check if username is a key in the user_agent_map.
        Returns False for non-Squad platforms without user_agent_map.
        """
        try:
            user_map = self.get_user_agent_map()
        except (AttributeError, NotImplementedError):
            return False
        if not user_map:
            return False
        return username.lower() in [k.lower() for k in user_map]

    # ── Routing (Squad Legion extension) ──
    @property
    def public_paths(self) -> list[str]:
        """Paths that do NOT require authentication."""
        ...

    def create_auth_middleware(self) -> BaseHTTPMiddleware:
        """Build and return the force-auth middleware for this platform."""
        ...

    def register_routes(self, app: FastAPI) -> None:
        """Register platform-specific HTTP routes (login, callback, logout)."""
        ...

    # ── Lifecycle ──
    async def startup(self) -> None:
        """Optional async startup hook (e.g. OIDC metadata pre-fetch)."""
        ...

    @property
    def session_kwargs(self) -> dict:
        """Keyword arguments for starlette SessionMiddleware."""
        ...

    # ── Filesystem ──
    @property
    def data_root(self) -> str:
        """Persistent data root for this platform."""
        try:
            from squad_config_loader import load_config
            return load_config().get("data_root", "/data")
        except Exception:
            return "/data"

    def instance_path(self, name: str) -> str:
        """Filesystem path for a named instance's persistent workspace."""
        return f"{self.data_root}/instances/{name}"

    # ── Persistent storage writes ──

    def write_credential(self, agent_id: str, channel: str, data: dict) -> None:
        """Write channel credential (account.json) to persistent storage.

        Implementations may override ``_on_persistent_write`` to add
        platform-specific sync (e.g. push to dataset git repo).
        """
        import json
        account_dir = f"{self.instance_path(agent_id)}/channels/{channel}"
        os.makedirs(account_dir, exist_ok=True)
        account_path = f"{account_dir}/account.json"
        with open(account_path, "w") as f:
            json.dump(data, f)
        os.chmod(account_path, 0o600)
        self._on_persistent_write()

    def write_config(self, agent_id: str, config: dict) -> None:
        """Write agent config.json to persistent storage.

        Implementations may override ``_on_persistent_write`` to add
        platform-specific sync (e.g. push to dataset git repo).
        """
        import json
        cfg_path = f"{self.instance_path(agent_id)}/config.json"
        with open(cfg_path, "w") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        os.chmod(cfg_path, 0o600)
        self._on_persistent_write()

    def _on_persistent_write(self) -> None:
        """Hook: called after ``write_credential`` or ``write_config``.

        Subclasses that need to sync persistent storage with an external
        view (e.g. ModelScope dataset) override this method.
        Default: no-op.
        """
        pass

    # ── WebSocket commander message processing (Squad Legion extension) ──

    def process_commander_message(
        self, data: str, username: str, real_name: str, is_commander: bool
    ) -> tuple[str | None, str | None]:
        """Process a Commander WS message before forwarding to neo.

        Default Squad behaviour: inject OAuth identity into envelope,
        block messages from unauthorized non-guest users.

        Returns ``(processed_data, blocked_reason)``.
        If *blocked_reason* is not None, the message is blocked.
        """
        import json

        authorized = is_commander or self.is_member(username)
        if not authorized and username != "guest":
            return (None, "🔒 只读模式: 请登录后再发送消息")

        ident = f"{real_name} (oauth:{username})" if real_name != "Guest" else "Guest"
        sender_id = f"oauth:{username}" if authorized else "guest"

        try:
            envelope = json.loads(data)
        except Exception:
            envelope = {}
        if not isinstance(envelope, dict):
            envelope = {}

        envelope["sender_name"] = ident
        envelope["sender_id"] = sender_id

        if is_commander:
            old_content = envelope.get("content", "")
            prefix = f"[{real_name}]: "
            if isinstance(old_content, str) and not old_content.startswith(prefix):
                envelope["content"] = prefix + old_content

        return (json.dumps(envelope), None)

    # ── Entrypoint setup ──

    @staticmethod
    def setup() -> str:
        """Platform-specific initialization before agent launch.

        Called by entrypoint.sh via ``platform_setup.py``. Operates directly
        on the filesystem/process environment and returns shell variable
        assignments to ``eval`` back into entrypoint.sh.

        Default: no-op (returns empty string).
        """
        return ""

    # ── Header stripping (cloud platform proxy adaptation) ──

    @property
    def proxy_header_blacklist(self) -> list[str]:
        """Headers to strip when gatekeeper proxies to downstream agents.

        These are headers injected by the platform reverse proxy that
        should NOT be forwarded to internal services (they are either
        platform-specific or recalculated by httpx).

        Common entries: ``host``, ``content-length``, ``x-forwarded-*``.
        """
        return ["host", "content-length", "x-forwarded-proto",
                "x-forwarded-for", "x-forwarded-host", "x-real-ip"]

    @property
    def stripped_inbound_headers(self) -> list[str]:
        """Headers the platform proxy strips from inbound client requests.

        Informational — used for diagnostics and documentation.
        The platform's OAuth implementation already handles the workaround
        (e.g. token-in-URL flow when ``authorization`` is stripped).

        Typical entries for restrictive platforms:
        ``authorization``, ``set-cookie``, ``cookie``.
        """
        return []


# ── Engine & Squad detection helpers ──


def _detect_engine(engine: str) -> bool:
    """Check if *engine* is available in the current environment."""
    if not engine:
        return True  # any engine
    if engine == "nanobot":
        return True  # always installed in our deployments
    # Future: check for openclaw, etc.
    return False


def _detect_squad() -> bool:
    """Check if Squad Legion overlay is present.

    The authoritative signal is the existence of ``squad_config.json``,
    which is only deployed on Squad spaces (copied from the platform-specific
    seed by ``launch.sh`` before ``cloud-gateway-setup`` runs).
    Cloud Demo spaces have no launch.sh and no squad_config.json.
    """
    # squad_config.json is seeded to /app/squad_config.json by launch.sh
    # Persistent copy lives at {MOUNT_PATH}/squad_config.json
    for p in ("/app/squad_config.json",
              os.path.join(os.environ.get("MOUNT_PATH", "/data"), "squad_config.json")):
        if os.path.exists(p):
            return True
    return False
