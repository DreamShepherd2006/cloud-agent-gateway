"""
ModelScope Studio Platform.

Handles ModelScope-specific OAuth, configuration, routing, and
authorisation.  Uses ``squad_config_loader`` for file-first config
with env-var fallback — necessary because ModelScope hides env values
after saving.

OAuth uses a manual HTTP flow (bypassing authlib nonce issues on MS)
with routes at ``/api/squad/auth/*`` to avoid MS platform interception.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
from authlib.integrations.starlette_client import OAuth
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from cloud_agent_gateway.platforms.base import CloudPlatformProtocol as PlatformProtocol
from cloud_agent_gateway.platforms._credentials import read_oauth_json

logger = logging.getLogger("gatekeeper.modelscope")

_MODELSCOPE_OIDC_CONFIG = "https://modelscope.cn/.well-known/openid-configuration"


# ── OAuth helpers ────────────────────────────────────────────


def _get_oauth_client() -> OAuth:
    """Create a minimal OAuth object for ModelScope.

    Registers the MS provider by hand because authlib's automatic OIDC
    discovery causes nonce-validation failures on ModelScope.
    """
    oauth = OAuth()
    client_id, client_secret = read_oauth_json()
    oauth.register(
        name="modelscope",
        client_id=client_id,
        client_secret=client_secret,
        server_metadata_url=_MODELSCOPE_OIDC_CONFIG,
        client_kwargs={
            "scope": "profile",  # avoid 'openid' → no nonce
            "token_endpoint_auth_method": "client_secret_post",
        },
    )
    return oauth


# ═══════════════════════════════════════════════════════════════
# Platform Implementation
# ═══════════════════════════════════════════════════════════════


class ModelScopePlatform(PlatformProtocol):
    """Platform implementation for ModelScope Studio (Squad)."""

    name = "modelscope"

    def __init__(self):
        super().__init__()
        self._webui_agent: str = ""
        self._squad_roster: dict[str, dict] = {}
        self._commander_whitelist: list[str] = []
        self._user_agent_map: dict[str, str] = {}

    # ═══════════════════════════════════════════════════════════════
    # Configuration
    # ═══════════════════════════════════════════════════════════════

    def refresh_config(
        self,
        *,
        webui_agent: str = "",
        squad_roster: dict[str, dict] | None = None,
    ) -> None:
        """Load configuration — file-first (squad_config.json), env-fallback."""
        try:
            from nanobot_legion.squad_config_loader import (
                get_commander_whitelist,
                get_peers,
                get_user_agent_map,
                get_webui_agent,
            )
            self._commander_whitelist = get_commander_whitelist()
            self._user_agent_map = get_user_agent_map()
            self._webui_agent = webui_agent or get_webui_agent()
            if squad_roster is None:
                squad_roster = get_peers()
        except ImportError:
            logger.warning("squad_config_loader not available — falling back to env vars")
            self._commander_whitelist = [
                u.strip()
                for u in os.environ.get("COMMANDER_WHITELIST", "").split(",")
                if u.strip()
            ]
            # USER_AGENT_MAP: flat dict from env
            self._user_agent_map = {}
            raw = os.environ.get("USER_AGENT_MAP", "")
            if raw:
                try:
                    self._user_agent_map = json.loads(raw)
                except json.JSONDecodeError:
                    for pair in raw.split(","):
                        if ":" in pair:
                            k, v = pair.split(":", 1)
                            self._user_agent_map[k.strip()] = v.strip()
            self._webui_agent = webui_agent or os.environ.get("WEBUI_AGENT", "neo")

        self._squad_roster = squad_roster or {}
        logger.info(
            "ModelScope config: webui=%s  whitelist=%s  peers=%s",
            self._webui_agent,
            self._commander_whitelist,
            list(self._squad_roster.keys()),
        )

    # ═══════════════════════════════════════════════════════════════
    # OAuth
    # ═══════════════════════════════════════════════════════════════

    @property
    def login_route_path(self) -> str:
        return "/api/squad/auth/login"

    @property
    def callback_route_path(self) -> str:
        return "/api/squad/auth/callback"

    def _public_callback_url(self, request: Request) -> str:
        """Override redirect_uri to use the public .ms.show domain instead of
        the internal VPC address that ``request.url_for(...)`` would produce.

        Priority: SPACE_ID env → X-Forwarded-Host header → Referer header → fallback
        """
        callback = self.callback_route_path

        # 1. SPACE_ID env (if unfreeze worked)
        space_id = os.environ.get("SPACE_ID", "")
        logger.info(f"[OAuth] SPACE_ID = {space_id!r}")
        if "/" in space_id:
            owner, repo = space_id.split("/", 1)
            url = f"https://{owner.lower()}-{repo}.ms.show{callback}"
            logger.info(f"[OAuth] redirect_uri (SPACE_ID)  = {url}")
            return url

        # 2. X-Forwarded-Host (MS proxy may set the public host)
        fwd_host = request.headers.get("X-Forwarded-Host", "")
        logger.info(f"[OAuth] X-Forwarded-Host = {fwd_host!r}")
        if ".ms.show" in fwd_host or "modelscope.cn" in fwd_host:
            url = f"https://{fwd_host}{callback}"
            logger.info(f"[OAuth] redirect_uri (XFH)    = {url}")
            return url

        # 3. Referer header (browser sends public URL when clicking Login)
        referer = request.headers.get("Referer", "")
        logger.info(f"[OAuth] Referer          = {referer!r}")
        if ".ms.show" in referer or "modelscope.cn" in referer:
            from urllib.parse import urlparse
            parsed = urlparse(referer)
            url = f"{parsed.scheme}://{parsed.hostname}{callback}"
            logger.info(f"[OAuth] redirect_uri (Ref) = {url}")
            return url

        # 4. X-Forwarded-For / Host reconstruction
        host = request.headers.get("host", "")
        logger.info(f"[OAuth] Host              = {host!r}")

        # 5. Last resort: request.url_for (will produce VPC URL — broken)
        url = str(request.url_for("modelscope_callback"))
        logger.warning(f"[OAuth] FALLBACK redirect_uri = {url}")
        return url.replace("http://", "https://", 1)

    def register_oauth(self) -> OAuth:
        return _get_oauth_client()

    async def exchange_token(self, request: Request) -> dict | None:
        """Manual OAuth token exchange for ModelScope.

        Bypasses authlib's nonce validation (which fails on MS).
        Uses direct HTTP POST to ``/oauth/token`` + GET ``/oauth/userinfo``.
        """
        code = request.query_params.get("code")
        if not code:
            logger.warning("No authorisation code in callback")
            return None

        client_id, client_secret = read_oauth_json()
        redirect_uri = self._public_callback_url(request)

        async with httpx.AsyncClient(timeout=15) as client:
            # Step 1 — exchange code for access token
            token_resp = await client.post(
                "https://modelscope.cn/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                },
                headers={"Accept": "application/json"},
            )
            if token_resp.status_code != 200:
                logger.error("Token exchange failed: %s", token_resp.text[:200])
                return None

            token_data = token_resp.json()
            access_token = token_data.get("access_token")
            if not access_token:
                logger.error("No access_token in response: %s", token_data)
                return None

            # Step 2 — fetch userinfo
            user_resp = await client.get(
                "https://modelscope.cn/oauth/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if user_resp.status_code != 200:
                logger.error("Userinfo failed: %s", user_resp.text[:200])
                return None

            userinfo = user_resp.json()
            return {"userinfo": userinfo, "access_token": access_token}

    async def fetch_userinfo(self, token: dict) -> dict | None:
        """Re-fetch userinfo from ModelScope."""
        access_token = token.get("access_token")
        if not access_token:
            return None
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://modelscope.cn/oauth/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if resp.status_code == 200:
                return resp.json()
        return None

    def extract_username(self, userinfo: dict) -> str:
        return (
            userinfo.get("preferred_username")
            or userinfo.get("username")
            or userinfo.get("nickname")
            or userinfo.get("name", "")
        )

    # ═══════════════════════════════════════════════════════════════
    # Authorisation
    # ═══════════════════════════════════════════════════════════════

    def get_commander_whitelist(self) -> list[str]:
        return self._commander_whitelist

    def get_user_agent_map(self) -> dict[str, str]:
        return self._user_agent_map

    def get_agent_for_user(self, username: str) -> str:
        if not username or username == "guest":
            return self._webui_agent
        if username in self._commander_whitelist:
            return self._webui_agent
        peer_key = self._user_agent_map.get(username, "")
        if peer_key.startswith("NANOBOT_PEER_"):
            agent = peer_key[len("NANOBOT_PEER_"):].lower()
            if agent in self._squad_roster:
                return agent
            return self._webui_agent
        if peer_key:
            return peer_key
        return self._webui_agent

    def is_commander(self, session_user: Any) -> bool:
        if session_user is None:
            return False
        username = (
            session_user.get("username", "")
            if isinstance(session_user, dict)
            else str(session_user)
        )
        return username in self._commander_whitelist



    def check_relay_permission(self, sender: str, target: str) -> bool:
        if sender in self._commander_whitelist:
            return True
        if sender in self._user_agent_map:
            return True
        return False

    # ═══════════════════════════════════════════════════════════════
    # Routing
    # ═══════════════════════════════════════════════════════════════

    @property
    def public_paths(self) -> list[str]:
        return [
            "/health",
            "/api/squad/auth/login",
            "/api/squad/auth/callback",
            "/api/squad/relay",
            "/api/squad/tasks",
            "/api/squad/sessions",
        ]

    def _guess_username(self, request: Request) -> str:
        """Extract ModelScope-authenticated username from request context."""
        user = request.session.get("user", {})
        if isinstance(user, dict) and user.get("username"):
            return user["username"]
        forwarded = request.headers.get("x-forwarded-user", "")
        if forwarded:
            return forwarded
        return "guest"

    def create_auth_middleware(self) -> BaseHTTPMiddleware:
        """Lightweight auth for ModelScope.

        ModelScope platform does NOT inject OAuth at proxy level;
        the gatekeeper handles login page injection at the root route.
        This middleware only ensures a session user is set (guest fallback).
        """
        platform = self  # capture for closure

        class _ModelScopeAuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                path = request.url.path

                # Public paths + /api/squad/* bypass namespace
                for prefix in platform.public_paths:
                    if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
                        return await call_next(request)
                if path.startswith("/api/squad/"):
                    return await call_next(request)

                # Ensure session user exists
                if not request.session.get("user"):
                    username = platform._guess_username(request)
                    request.session["user"] = {"username": username, "name": username}

                return await call_next(request)

        return _ModelScopeAuthMiddleware

    def register_routes(self, app: FastAPI) -> None:
        """Register MS-specific routes: OAuth + sessions proxy + catch-all."""
        platform = self

        # Deferred webui agent resolution (populated during first request)
        _webui_ws_port: int = 0
        _agent_name: str = ""

        def _ensure_webui():
            nonlocal _webui_ws_port, _agent_name
            if _webui_ws_port > 0:
                return
            _agent_name = platform._webui_agent or "neo"
            peer = platform._squad_roster.get(_agent_name, {})
            _webui_ws_port = peer.get("ws_port", 20002)
            logger.info(
                "Modelscope routes ready: webui_agent=%s ws_port=%s",
                _agent_name,
                _webui_ws_port,
            )

        # ── OAuth Login ──────────────────────────────────────

        @app.get("/api/squad/auth/login")
        async def modelscope_login(request: Request):
            _ensure_webui()
            redirect_uri = platform._public_callback_url(request)
            client_id, _ = read_oauth_json()
            auth_url = (
                f"https://modelscope.cn/oauth/authorize"
                f"?response_type=code"
                f"&client_id={client_id}"
                f"&redirect_uri={redirect_uri}"
                f"&scope=profile"
            )
            return RedirectResponse(url=auth_url)

        # ── OAuth Callback ───────────────────────────────────

        @app.get("/api/squad/auth/callback", name="modelscope_callback")
        async def modelscope_callback(request: Request):
            _ensure_webui()
            result = await platform.exchange_token(request)
            if result is None:
                return RedirectResponse(url="/?error=auth_failed")

            userinfo = result.get("userinfo", {})
            username = platform.extract_username(userinfo)
            request.session["user"] = {
                "username": username,
                "name": userinfo.get("nickname") or userinfo.get("name") or username,
            }
            logger.info("OAuth success → %s", username)
            return RedirectResponse(url="/")

        # ═══════════════════════════════════════════════════
        # NOTE: Catch-all HTTP proxy is registered in gatekeeper.py
        # (after all specific routes, including /api/squad/relay and
        # /api/squad/tasks).  Platform module must NOT register its own
        # catch-all or it will intercept gatekeeper's squad API routes.
        # ═══════════════════════════════════════════════════

    # ═══════════════════════════════════════════════════════════════
    # Lifecycle
    # ═══════════════════════════════════════════════════════════════

    async def startup(self) -> None:
        """Pre-fetch OIDC metadata to avoid blocking event loop later."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(_MODELSCOPE_OIDC_CONFIG)
                if resp.status_code == 200:
                    logger.info("OIDC metadata pre-fetched from ModelScope")
                else:
                    logger.warning("OIDC metadata fetch returned %s", resp.status_code)
        except Exception as e:
            logger.warning("OIDC metadata pre-fetch failed: %s", e)

    @property
    def session_kwargs(self) -> dict:
        return {
            "secret_key": os.environ.get(
                "SESSION_SECRET", "nanobot_modelscope_secret_k8s"
            ),
            "https_only": True,
            "same_site": "none",
        }

    # ═══════════════════════════════════════════════════════════════
    # Filesystem — inherited from base (reads data_root from squad_config.json)
    # ═══════════════════════════════════════════════════════════════

    # ═══════════════════════════════════════════════════════════════
    # WebSocket commander message — identity injection + guest blocking
    # ═══════════════════════════════════════════════════════════════



    # ═══════════════════════════════════════════════════════════════
    # Entrypoint setup — env unfreeze, relay token mapping
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def setup() -> str:
        """MS-specific initialisation: unfreeze env, map relay token."""
        import os as _os
        import sys as _sys

        def _log(msg: str) -> None:
            _sys.stderr.write(msg + "\n")
            _sys.stderr.flush()

        exports: list[str] = []

        # 1. Unfreeze env vars from /proc/1/environ
        proc_env = "/proc/1/environ"
        if _os.path.exists(proc_env):
            try:
                with open(proc_env, "rb") as f:
                    raw = f.read().split(b"\0")
                for item in raw:
                    if not item:
                        continue
                    try:
                        name, value = item.decode("utf-8", errors="replace").split("=", 1)
                    except ValueError:
                        continue
                    if name.startswith(("NANOBOT_TOKEN", "NANOBOT_PEER_",
                                        "NANOBOT_Staging",
                                        "SQUAD_LEGION", "SQUAD_RELAY_TOKEN")):
                        exports.append(f"export {name}='{value}'")
                        _os.environ[name] = value
                        _log(f"   >> 已解冻: {name}")
            except Exception as exc:
                _log(f"   ⚠️ env unfreeze failed: {exc}")

        # 2. Map SQUAD_RELAY_TOKEN_MS_NanobotNightly → SQUAD_RELAY_TOKEN
        #    New convention: SQUAD_RELAY_TOKEN_{PLATFORM}_{INSTANCE}
        #    Legacy fallback: SQUAD_RELAY_TOKEN_modelscope
        for ms_key in ("SQUAD_RELAY_TOKEN_MS_NanobotNightly",
                       "SQUAD_RELAY_TOKEN_modelscope"):
            if _os.environ.get(ms_key) and not _os.environ.get("SQUAD_RELAY_TOKEN"):
                tok = _os.environ[ms_key]
                exports.append(f"export SQUAD_RELAY_TOKEN='{tok}'")
                _os.environ["SQUAD_RELAY_TOKEN"] = tok
                _log(f"   🔑 SQUAD_RELAY_TOKEN mapped from {ms_key}")
                break

        return "\n".join(exports)


# ── PlatformSpec registration (data-driven — mirrors ProviderSpec) ──

from cloud_agent_gateway.platforms.base import PlatformSpec  # PlatformProtocol imported at top

PLATFORM_SPEC = PlatformSpec(
    name="modelscope-squad",
    display_name="ModelScope Studio (Squad)",
    detect_env="MODELSCOPE_ENVIRONMENT",
    detect_env_value="studio",
    module=".modelscope_squad",
    priority=5,
)
