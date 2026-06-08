"""
ModelScope Studio Cloud Platform.

Handles ModelScope OAuth, filesystem paths, dataset-backed configuration,
and platform-specific initialisation.  Uses manual httpx OAuth flow to
bypass authlib nonce-validation failures on ModelScope.

Supports two modes, controlled by env:
  - CLOUD_DEMO_MODE=1  → single-agent with OAuth proxy, simple setup
  - (default)          → multi-agent squad, dataset-backed config
"""

from __future__ import annotations

import json as _json
import logging
import os
import re
import shutil
import subprocess
import sys
from typing import Any

import httpx
from fastapi import Request  # noqa: F401 — used by register_routes route annotations
from authlib.integrations.starlette_client import OAuth

from cloud_agent_gateway.platforms.base import CloudPlatformProtocol

logger = logging.getLogger("cloud.modelscope")

_MODELSCOPE_OIDC_CONFIG = "https://modelscope.cn/.well-known/openid-configuration"

# Mode flag — set by Dockerfile to 1 for single-agent OAuth-proxy deployment
_CLOUD_DEMO = os.environ.get("CLOUD_DEMO_MODE", "") == "1"


def _log(msg: str) -> None:
    sys.stderr.write(f"[modelscope] {msg}\n")
    sys.stderr.flush()


def _get_oauth_client() -> OAuth:
    """Create a minimal OAuth object for ModelScope.

    Registers the MS provider by hand because authlib's automatic OIDC
    discovery causes nonce-validation failures on ModelScope.
    """
    oauth = OAuth()
    oauth.register(
        name="modelscope",
        client_id=os.environ.get("OAUTH_CLIENT_ID", ""),
        client_secret=os.environ.get("OAUTH_CLIENT_SECRET", ""),
        server_metadata_url=_MODELSCOPE_OIDC_CONFIG,
        client_kwargs={
            "scope": "profile",  # avoid 'openid' → no nonce
            "token_endpoint_auth_method": "client_secret_post",
        },
    )
    return oauth


class ModelScopePlatform(CloudPlatformProtocol):
    """Platform implementation for ModelScope Studio."""

    name = "modelscope"

    # ── Filesystem ──

    @property
    def data_root(self) -> str:
        return "/mnt/workspace"

    def instance_path(self, name: str) -> str:
        return f"{self.data_root}/instances/{name}"

    # ── OAuth ──

    def register_oauth(self) -> Any:
        return _get_oauth_client()

    login_route_path = "/login"
    # Cloud Demo uses OAuth proxy on /api/auth/*; staging uses nanobot directly on /auth/*
    callback_route_path = os.environ.get("OAUTH_CALLBACK_PATH", "/api/auth/callback")

    async def exchange_token(self, request: Any) -> dict | None:
        """Manual OAuth token exchange — bypasses authlib nonce issues on MS."""
        code = request.query_params.get("code")
        if not code:
            return None

        client_id = os.environ.get("OAUTH_CLIENT_ID", "")
        client_secret = os.environ.get("OAUTH_CLIENT_SECRET", "")
        redirect_uri = str(request.url).split("?")[0]

        async with httpx.AsyncClient(timeout=15) as http:
            token_resp = await http.post(
                f"{_MODELSCOPE_OIDC_CONFIG.replace('.well-known/openid-configuration', '')}oauth/token",
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
                logger.warning(f"Token exchange failed: {token_resp.text[:200]}")
                return None

            token_data = token_resp.json()
            access_token = token_data.get("access_token")
            if not access_token:
                return None

            user_resp = await http.get(
                f"{_MODELSCOPE_OIDC_CONFIG.replace('.well-known/openid-configuration', '')}oauth/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if user_resp.status_code != 200:
                return None

            userinfo = user_resp.json()
            _log(f"userinfo: {_json.dumps(userinfo)}")
            return {"userinfo": userinfo, "access_token": access_token}

    # Note: single fetch_userinfo — uses cached userinfo from exchange_token.
    # Do NOT add a second static version; Python's last-def-wins would shadow this.
    async def fetch_userinfo(self, token: dict) -> dict | None:
        return token.get("userinfo") if token else None

    def extract_username(self, userinfo: dict) -> str:
        return (
            userinfo.get("preferred_username")
            or userinfo.get("username")
            or userinfo.get("nickname")
            or userinfo.get("name")
            or userinfo.get("user_nickname")
            or userinfo.get("nick_name")
            or userinfo.get("login")
            or userinfo.get("sub", "")
        )

    # ── Entrypoint setup ──

    @staticmethod
    def setup() -> str:
        """MS initialisation: unfreeze env, then either simple demo setup
        or full squad deployment with dataset-backed config."""
        if _CLOUD_DEMO:
            return ModelScopePlatform._setup_cloud_demo()
        else:
            return ModelScopePlatform._setup_squad()

    @staticmethod
    def _setup_cloud_demo() -> str:
        """Cloud Demo setup: unfreeze env, auto-detect SPACE_AUTHOR from git."""
        exports: list[str] = []
        proc_env = "/proc/1/environ"

        # ── Auto-detect owner from git remote URL ──
        owner = ""
        try:
            result = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                capture_output=True, text=True, cwd="/app", timeout=5,
            )
            url = result.stdout.strip()
            m = re.search(r'/studios/([^/]+)/', url)
            if m:
                owner = m.group(1)
            _log(f"git remote: {url!r} → owner={owner!r}")
        except Exception as exc:
            _log(f"git parse warning: {exc}")

        if owner:
            os.environ["SPACE_AUTHOR"] = owner
            exports.append(f"export SPACE_AUTHOR='{owner}'")

        # ── Unfreeze env vars from /proc/1/environ ──
        if os.path.exists(proc_env):
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
                    if name.startswith(("NANOBOT_", "OAUTH_", "DEEPSEEK_")):
                        exports.append(f"export {name}='{value}'")
                        os.environ[name] = value
            except Exception as exc:
                _log(f"env unfreeze failed: {exc}")

        # Ensure correct data root for ModelScope
        exports.append("export DATA_ROOT='/mnt/workspace'")
        return "\n".join(exports)

    @staticmethod
    def _setup_squad() -> str:
        """Squad setup: unfreeze env, pull dataset, copy templates, restore configs."""
        exports: list[str] = []
        proc_env = "/proc/1/environ"
        mount_path = "/mnt/workspace"

        # ── 1. Unfreeze env vars from /proc/1/environ ──
        if os.path.exists(proc_env):
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
                    if name.startswith(("NANOBOT_", "OAUTH_", "MODELSCOPE_")):
                        exports.append(f"export {name}='{value}'")
                        os.environ[name] = value
                        _log(f"unfroze: {name}")
            except Exception as exc:
                _log(f"env unfreeze failed: {exc}")

        # ── 2. Pull dataset from ModelScope ──
        dataset_dir = "/tmp/nanobot-instances"
        if not os.path.isdir(dataset_dir):
            ms_token = os.environ.get("NANOBOT_MODELSCOPE_TOKEN", "")
            if not ms_token:
                try:
                    with open(proc_env, "rb") as f:
                        for item in f.read().split(b"\0"):
                            if b"NANOBOT_MODELSCOPE_TOKEN" in item:
                                ms_token = item.decode("utf-8", errors="replace").split("=", 1)[1]
                                break
                except Exception:
                    pass
            if ms_token:
                _log("Pulling instance templates from dataset...")
                repo = os.environ.get("NANOBOT_DATASET_REPO",
                                      "DreamShepherd2006/nanobot-storage")
                url = f"https://oauth2:{ms_token}@www.modelscope.cn/datasets/{repo}.git"
                try:
                    subprocess.run(["git", "clone", "--depth=1", url, dataset_dir],
                                   check=True, capture_output=True, timeout=60)
                except Exception as exc:
                    _log(f"Dataset pull failed: {exc}")
            else:
                _log("No MS token — skipping dataset pull")

        # ── 3. Copy template from dataset (storage-first) ──
        os.makedirs(f"{mount_path}/instances", exist_ok=True)
        tmpl_dst = f"{mount_path}/instances/_template"
        if os.path.isdir(f"{dataset_dir}/_template"):
            if os.path.exists(tmpl_dst):
                shutil.rmtree(tmpl_dst)
            shutil.copytree(f"{dataset_dir}/_template", tmpl_dst)
            _log("Template synced from dataset")
        elif not os.path.exists(tmpl_dst):
            _log("No template available — agents will need manual config")

        # ── 4. Restore agent configs from dataset (only for missing/new agents) ──
        for item in os.listdir(dataset_dir):
            item_path = os.path.join(dataset_dir, item)
            cfg_file = os.path.join(item_path, "config.json")
            if (os.path.isdir(item_path)
                    and item not in ("_template", ".git")
                    and os.path.isfile(cfg_file)):
                dst_dir = f"{mount_path}/instances/{item}"
                dst_cfg = f"{dst_dir}/config.json"
                os.makedirs(dst_dir, exist_ok=True)
                if not os.path.isfile(dst_cfg):
                    shutil.copy2(cfg_file, dst_cfg)
                    _log(f"restored: {item}/config.json")

        return "\n".join(exports)

    # ── Header stripping ───────────────────────────────────────

    @property
    def proxy_header_blacklist(self) -> list[str]:
        """ModelScope injects x-ms-* headers that must be stripped before
        proxying to internal agents."""
        return super().proxy_header_blacklist + [
            "x-ms-client-request-id",
            "x-ms-client-principal",
            "x-ms-client-principal-name",
        ]

    @property
    def stripped_inbound_headers(self) -> list[str]:
        """ModelScope Studio proxy strips Authorization and Set-Cookie.

        This is why we use token-in-URL flow for WebSocket auth and manual
        httpx OAuth (bypassing session-cookie-based CSRF).
        """
        return ["authorization", "set-cookie"]

    # ── Session Middleware ──

    @property
    def session_kwargs(self) -> dict:
        return {
            "secret_key": os.environ.get(
                "SESSION_SECRET", "nanobot_modelscope_secret_k8s"
            ),
            "https_only": True,
            "same_site": "none",
        }


    # ── Squad Gatekeeper Methods (only used in non-Cloud-Demo mode) ──

    def __init__(self):
        self._webui_agent: str = ""
        self._squad_roster: dict[str, dict] = {}
        self._commander_whitelist: list[str] = []
        self._user_agent_map: dict[str, str] = {}

    def refresh_config(
        self,
        *,
        webui_agent: str = "",
        squad_roster: dict[str, dict] | None = None,
    ) -> None:
        """Load configuration — file-first (squad_config.json), env-fallback."""
        try:
            from squad_config_loader import (
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
            self._user_agent_map = {}
            raw = os.environ.get("USER_AGENT_MAP", "")
            if raw:
                try:
                    self._user_agent_map = _json.loads(raw)
                except _json.JSONDecodeError:
                    for pair in raw.split(","):
                        if ":" in pair:
                            k, v = pair.split(":", 1)
                            self._user_agent_map[k.strip()] = v.strip()
            self._webui_agent = webui_agent or os.environ.get("WEBUI_AGENT", "neo")

        self._squad_roster = squad_roster or {}

    def _public_callback_url(self, request: Any) -> str:
        """Build public-facing callback URL using .ms.show domain."""
        space_id = os.environ.get("SPACE_ID", "")
        if space_id:
            return f"https://{space_id}.ms.show/api/squad/auth/callback"
        host = request.headers.get("x-forwarded-host", "")
        if host:
            return f"https://{host}/api/squad/auth/callback"
        return str(request.url_for("modelscope_callback"))

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

    def _guess_username(self, request: Any) -> str:
        """Extract ModelScope-authenticated username from request context."""
        user = request.session.get("user", {})
        if isinstance(user, dict) and user.get("username"):
            return user["username"]
        forwarded = request.headers.get("x-forwarded-user", "")
        if forwarded:
            return forwarded
        return "guest"

    def create_auth_middleware(self):
        """Lightweight auth middleware — allow public paths, set guest user session."""
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.requests import Request as SR

        platform = self

        class _ModelScopeAuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: SR, call_next):
                path = request.url.path
                for prefix in platform.public_paths:
                    if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
                        return await call_next(request)
                if path.startswith("/api/squad/"):
                    return await call_next(request)
                if not request.session.get("user"):
                    username = platform._guess_username(request)
                    request.session["user"] = {"username": username, "name": username}
                return await call_next(request)

        return _ModelScopeAuthMiddleware

    def register_routes(self, app):
        """Register MS-specific routes: OAuth login + callback."""
        from starlette.requests import Request
        from starlette.responses import RedirectResponse

        platform = self
        _webui_ws_port = 0
        _agent_name = ""

        def _ensure_webui():
            nonlocal _webui_ws_port, _agent_name
            if _webui_ws_port > 0:
                return
            _agent_name = platform._webui_agent or "neo"
            peer = platform._squad_roster.get(_agent_name, {})
            _webui_ws_port = peer.get("ws_port", 20002)

        @app.get("/api/squad/auth/login")
        async def modelscope_login(request: Request):
            _ensure_webui()
            redirect_uri = platform._public_callback_url(request)
            client_id = os.environ.get("OAUTH_CLIENT_ID", "")
            auth_url = (
                f"https://modelscope.cn/oauth/authorize"
                f"?response_type=code"
                f"&client_id={client_id}"
                f"&redirect_uri={redirect_uri}"
                f"&scope=profile"
            )
            return RedirectResponse(url=auth_url)

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
            return RedirectResponse(url="/")
    # ── Squad authorisation methods ──

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

    def is_member(self, username: str) -> bool:
        return username.lower() in [k.lower() for k in self._user_agent_map]

    def check_relay_permission(self, sender: str, target: str) -> bool:
        if sender in self._commander_whitelist:
            return True
        if sender in self._user_agent_map:
            return True
        return False

    # ── OAuth routes ──
