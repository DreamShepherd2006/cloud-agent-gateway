"""
ModelScope Studio Cloud Platform — Cloud Demo (single-agent, nanobot engine).

Handles ModelScope OAuth, filesystem paths, and platform-specific
initialisation.  Uses manual httpx OAuth flow to bypass authlib
nonce-validation failures on ModelScope.

For Squad (multi-agent) ModelScope support, see ``modelscope_squad.py``.
"""

from __future__ import annotations

import json as _json
import logging
import os
import re
import subprocess
import sys
from typing import Any

import httpx
from authlib.integrations.starlette_client import OAuth

from cloud_agent_gateway.platforms.base import CloudPlatformProtocol

logger = logging.getLogger("cloud.modelscope")

_MODELSCOPE_OIDC_CONFIG = "https://modelscope.cn/.well-known/openid-configuration"


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
        """MS Cloud Demo initialisation: unfreeze env, auto-detect owner."""
        return ModelScopePlatform._setup_cloud_demo()

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



