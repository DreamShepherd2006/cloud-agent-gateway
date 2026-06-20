#!/usr/bin/env python3
"""
Cloud OAuth proxy — sits in front of nanobot's WebSocket/WebUI port.

     User → :7860 (oauth_proxy)
               ├─ /login              → HTML page with login button
               ├─ /auth/start         → redirect to platform OAuth
               ├─ /auth/callback      → exchange code → token → redirect to /?nbtoken=TOKEN
               ├─ /health             → proxy to nanobot gateway
               ├─ other HTTP          → proxy to nanobot ws_port
               └─ WebSocket upgrade   → proxy to nanobot ws_port

Auth strategy:
- HF Spaces: standard session cookie (cookies work)
- ModelScope: token-in-URL (MS proxy strips Set-Cookie from responses)
  After OAuth callback, a random token is generated and embedded in the URL.
  HTML responses are patched to inject JS that monkey-patches fetch/WebSocket
  to carry the token on all subsequent requests via sessionStorage.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
import time
import uuid
from urllib.parse import parse_qs, urlencode

import httpx
import uvicorn
from contextlib import asynccontextmanager
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocket, WebSocketDisconnect

# ── Import path ─────────────────────────────────────────────────────
_cloud_dir = os.path.dirname(os.path.abspath(__file__))
if _cloud_dir not in sys.path:
    sys.path.insert(0, _cloud_dir)

from cloud_agent_gateway.platforms import platform as _platform
from cloud_agent_gateway.channel_binding import bind_status, discover

# Discover channel bindings at module load (triggers import of deploy-layer modules)
_bindings = discover()


def _log(msg: str) -> None:
    sys.stderr.write(f"[oauth_proxy {time.strftime('%H:%M:%S')}] {msg}\n")
    sys.stderr.flush()


# ── Config ──────────────────────────────────────────────────────────
NANOBOT_WS_PORT = int(os.environ.get("NANOBOT_WS_PORT", "7870"))
NANOBOT_GW_PORT = int(os.environ.get("NANOBOT_GW_PORT", "17860"))
UPSTREAM = f"http://127.0.0.1:{NANOBOT_WS_PORT}"
SESSION_TTL = int(os.environ.get("OAUTH_SESSION_TTL", "86400"))
SECRET = os.environ.get("SESSION_SECRET", secrets.token_urlsafe(32))

PLATFORM = _platform
LOGIN_PATH = getattr(PLATFORM, "login_route_path", "/login")
LOGIN_START_PATH = "/auth/start"
CALLBACK_PATH = getattr(PLATFORM, "callback_route_path", "/auth/callback")
AUTH_PROVIDER = getattr(PLATFORM, "auth_provider", "HuggingFace")
RELAY_TOKEN = os.environ.get("SQUAD_RELAY_TOKEN", "").strip()
RELAY_TIMEOUT = int(os.environ.get("SQUAD_RELAY_TIMEOUT", "120"))
_log(f"platform={PLATFORM.name}  upstream=:{NANOBOT_WS_PORT}  relay={'✓' if RELAY_TOKEN else '✗'}")

# Cookie settings (kept for HF Spaces; MS uses token-in-URL instead)
if PLATFORM.name == "modelscope":
    _cookie_same_site = "lax"
    _cookie_secure = False
else:
    _cookie_same_site = "none"
    _cookie_secure = True


# ═══════════════════════════════════════════════════════════════════
# Token store (used when cookies are unavailable, e.g. ModelScope)
# ═══════════════════════════════════════════════════════════════════

_token_store: dict[str, dict] = {}  # token → {username, login_at, expires_at}
_oauth_states: dict[str, float] = {}  # state → expiry_timestamp


def _generate_token(username: str) -> str:
    """Create a login token and store it in memory."""
    token = secrets.token_urlsafe(32)
    _token_store[token] = {
        "username": username,
        "login_at": time.time(),
        "expires_at": time.time() + SESSION_TTL,
    }
    _purge_tokens()
    return token


def _validate_token(token: str) -> str | None:
    """Return username if token is valid, None otherwise."""
    data = _token_store.get(token)
    if not data:
        return None
    if data["expires_at"] > time.time():
        return data["username"]
    del _token_store[token]
    return None


def _purge_tokens() -> None:
    now = time.time()
    for t in list(_token_store.keys()):
        if _token_store[t]["expires_at"] < now:
            del _token_store[t]


# ═══════════════════════════════════════════════════════════════════
# JS injection — monkey-patches fetch/WebSocket to carry nbtoken
# ═══════════════════════════════════════════════════════════════════

_INJECT_SCRIPT = """\
<script>(function(){
var p=new URLSearchParams(window.location.search);
var t=p.get('nbtoken');
if(t){window.__NBT__=t;sessionStorage.setItem('__nbt__',t);
var u=new URL(window.location);u.searchParams.delete('nbtoken');
window.history.replaceState({},'',u.toString());}
else{window.__NBT__=sessionStorage.getItem('__nbt__')||'';}
if(window.__NBT__){var _f=window.fetch;window.fetch=function(u,o){
var s=typeof u==='string'?u:u.url;
var x=new URL(s,window.location.origin);
if(x.origin===window.location.origin)x.searchParams.set('nbtoken',window.__NBT__);
return _f.call(this,typeof u==='string'?x.toString():new Request(x.toString(),u),o);};
var _W=window.WebSocket;window.WebSocket=function(u,p){
var x=new URL(u,window.location.origin);
if(x.hostname==='127.0.0.1'||x.hostname==='localhost'){
x.protocol=window.location.protocol==='https:'?'wss:':'ws:';
x.host=window.location.host;x.port='';
x.searchParams.set('nbtoken',window.__NBT__);}
if(x.origin===window.location.origin)x.searchParams.set('nbtoken',window.__NBT__);
return new _W(x.toString(),p);};
window.WebSocket.prototype=_W.prototype;
window.WebSocket.CONNECTING=_W.CONNECTING;
window.WebSocket.OPEN=_W.OPEN;
window.WebSocket.CLOSING=_W.CLOSING;
window.WebSocket.CLOSED=_W.CLOSED;
} })();</script>
"""


def _inject_token_script(content: bytes, content_type: str) -> bytes:
    """Inject nbtoken-carrying JS into HTML responses."""
    if "text/html" not in content_type:
        return content
    html = content.decode("utf-8", errors="replace")
    # Inject before </head> if present, otherwise before </html>
    if "</head>" in html:
        return html.replace("</head>", _INJECT_SCRIPT + "</head>", 1).encode("utf-8")
    if "</html>" in html:
        return html.replace("</html>", _INJECT_SCRIPT + "</html>", 1).encode("utf-8")
    return content


# ═══════════════════════════════════════════════════════════════════
# Login page
# ═══════════════════════════════════════════════════════════════════

LOGIN_PAGE = """\
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>登录</title>
<!-- CAG: workspace_scope fix v2 — real directory at data_root/BINDING_TITLE -->
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { display:flex; align-items:center; justify-content:center; min-height:100vh;
       font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       background:#0f0f0f; color:#e0e0e0; }
.card { text-align:center; padding:3rem 2rem; max-width:400px; }
h1 { font-size:1.8rem; margin-bottom:0.5rem; color:#fff; }
p { color:#999; margin-bottom:2rem; font-size:0.95rem; }
.btn { display:inline-block; padding:12px 32px; border-radius:8px;
       background:#3b82f6; color:#fff; font-size:1rem; font-weight:600;
       text-decoration:none; border:none; cursor:pointer; transition:background .2s; }
.btn:hover { background:#2563eb; }
</style>
</head>
<body>
<div class="card">
<h1>🔐 登录</h1>
<p>使用 """ + AUTH_PROVIDER + """ 账号登录以使用 AI 助手</p>
<button class="btn" onclick="location.href='""" + LOGIN_START_PATH + """'">
  用 """ + AUTH_PROVIDER + """ 登录
</button>
</div>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════════
# Auth middleware — check token (URL), then session (cookie)
# ═══════════════════════════════════════════════════════════════════

_AUTH_FREE = {LOGIN_PATH, LOGIN_START_PATH, CALLBACK_PATH,
              "/auth/callback", "/login/callback",
              "/health", "/-/health",
               "/api/squad/relay"}
for _b in _bindings:
    _AUTH_FREE.add(f"/bind/{_b.name}")
    for _path_suffix, _method, _handler in _b.public_routes:
        _AUTH_FREE.add(f"/bind/{_b.name}{_path_suffix}")
AUTH_FREE = _AUTH_FREE


class AuthMiddleware:
    """Check nbtoken (URL) or session (cookie); serve login page if neither."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        is_free = any(path == p or (p.endswith("/") and path.startswith(p))
                      for p in AUTH_FREE)
        if is_free:
            await self.app(scope, receive, send)
            return

        # --- Primary: nbtoken in URL query string ---
        qs = scope.get("query_string", b"").decode("utf-8", errors="replace")
        params = parse_qs(qs)
        token = (params.get("nbtoken") or [None])[0]
        if token:
            username = _validate_token(token)
            if username:
                if _check_owner(username):
                    scope["_auth_user"] = username
                    scope["_auth_token"] = token
                    await self.app(scope, receive, send)
                    return
                _log(f"⛔ token owner mismatch: {username} != {_get_owner()}")
            else:
                _log(f"⛔ invalid/expired nbtoken")

        # --- Fallback: session cookie (works on HF Spaces) ---
        request = Request(scope, receive)
        user = request.session.get("user") if hasattr(request, "session") else None
        if user:
            username = user.get("username", "")
            if _check_owner(username):
                scope["_auth_user"] = username
                await self.app(scope, receive, send)
                return
            _log(f"⛔ session owner mismatch: {username} != {_get_owner()}")

        # Not authenticated → login page
        response = HTMLResponse(LOGIN_PAGE)
        await response(scope, receive, send)


# ── App ────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app):
    """Startup: ensure channel-binding pinned chat exists after container restart."""
    _ensure_binding_session()
    yield


app = Starlette(
    lifespan=lifespan,
    middleware=[
        Middleware(
            SessionMiddleware,
            secret_key=SECRET,
            session_cookie="nanobot_session",
            max_age=SESSION_TTL,
            same_site=_cookie_same_site,
            https_only=_cookie_secure,
        ),
        Middleware(AuthMiddleware),
    ]
)


# ═══════════════════════════════════════════════════════════════════
# OAuth routes (auth-free per AUTH_FREE)
# ═══════════════════════════════════════════════════════════════════


async def login_page(request: Request) -> HTMLResponse:
    """Serve login page."""
    return HTMLResponse(LOGIN_PAGE)


async def login_start(request: Request) -> RedirectResponse:
    """Redirect to platform OAuth provider."""
    callback_path = "/login/callback" if PLATFORM.name != "modelscope" else CALLBACK_PATH
    redirect_uri = _build_redirect_uri(request, callback_path)
    _log(f"login → {redirect_uri}")

    state = secrets.token_urlsafe(16)
    _oauth_states[state] = time.time() + 300  # 5 min TTL for OAuth state
    # Purge expired states
    now = time.time()
    for s in list(_oauth_states.keys()):
        if _oauth_states[s] < now:
            del _oauth_states[s]

    # Also store in session for HF fallback
    try:
        request.session["oauth_state"] = state
    except Exception:
        pass

    if PLATFORM.name == "modelscope":
        auth_base = "https://modelscope.cn/oauth/authorize"
        scope = "openid profile"
    else:
        auth_base = "https://huggingface.co/oauth/authorize"
        scope = "openid profile"

    params = {
        "response_type": "code",
        "client_id": os.environ.get("OAUTH_CLIENT_ID", ""),
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
    }
    auth_url = f"{auth_base}?{urlencode(params)}"
    return RedirectResponse(auth_url, status_code=302)


BINDING_TITLE = "社交通道配置提示"

_rows = "\n".join(
    f"| {b.icon} {b.display} | [绑定{b.display}](/bind/{b.name}) |"
    for b in _bindings
)
BINDING_CHAT_CONTENT = f"""\
# 📱 社交通道配置

将 nanobot 连接到社交通道，随时随地对话。

| 通道 | 操作 |
|------|------|
{_rows}

👆 点击上方链接即可操作，无需在此聊天。"""


def _get_binding_chat_id() -> str | None:
    """Return the binding chat ID from sidebar-state, or None."""
    from cloud_agent_gateway.platforms import platform
    _agent = "default"
    _state = platform.read_sidebar_state(_agent)
    for _pk in _state.get("pinned_keys", []):
        if not isinstance(_pk, str) or not _pk.startswith("websocket:"):
            continue
        _cid = _pk.split(":", 1)[1]
        _lines = platform.read_session(_agent, _cid)
        if _lines and _lines[0].get("metadata", {}).get("title") == BINDING_TITLE:
            return _cid
    return None


def _ensure_binding_session():
    """Pre-create the binding chat session + pin so sidebar shows it on first load.

    Called from OAuth callback (before the page loads) and from setup_title()
    (after WebSocket connects). Idempotent — only creates if no pinned binding
    chat exists.
    """
    import uuid as _uuid, time as _time
    from cloud_agent_gateway.platforms import platform
    _agent = "default"
    _now = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())

    # Clean up ALL stale binding sessions (not just the first) — otherwise
    # old sessions from previous restarts accumulate as duplicate pinned chats.
    _state = platform.read_sidebar_state(_agent)
    _any_deleted = False
    for _pk in list(_state.get("pinned_keys", [])):
        if not isinstance(_pk, str) or not _pk.startswith("websocket:"):
            continue
        _cid = _pk.split(":", 1)[1]
        _lines = platform.read_session(_agent, _cid)
        if _lines and _lines[0].get("metadata", {}).get("title") == BINDING_TITLE:
            platform.delete_session(_agent, _cid)
            _state["pinned_keys"].remove(_pk)
            _any_deleted = True
            _log(f"deleted old binding session (cid={_cid[:12]})")
    if _any_deleted:
        _state["updated_at"] = _now
        platform.write_sidebar_state(_agent, _state)

    # Create a new binding chat
    _cid = str(_uuid.uuid4())
    _key = f"websocket:{_cid}"

    # Ensure the project_path directory exists so validate_workspace_scope_payload()
    # does not reject it (nanobot requires project_path to be an existing directory).
    import os as _os
    _project_dir = f"{platform.data_root}/{BINDING_TITLE}"
    _os.makedirs(_project_dir, exist_ok=True)

    platform.write_session(_agent, _cid, [
        {
            "_type": "metadata", "key": _key,
            "created_at": _now, "updated_at": _now,
            "metadata": {"title": BINDING_TITLE, "webui": True,
                        "workspace_scope": {"project_path": _project_dir}},
            "last_consolidated": 0,
        },
        {
            "role": "user", "content": BINDING_CHAT_CONTENT,
            "timestamp": _now,
        },
    ])

    # WebUI transcript
    platform.write_webui_transcript(_agent, _cid, [
        {"event": "delta", "text": BINDING_CHAT_CONTENT, "chat_id": _cid},
        {"event": "stream_end", "text": BINDING_CHAT_CONTENT, "chat_id": _cid},
        {"event": "turn_end", "chat_id": _cid},
    ])

    # Pin it
    _sidebar_state = platform.read_sidebar_state(_agent)
    _sidebar_state.setdefault("pinned_keys", []).insert(0, _key)
    _sidebar_state["updated_at"] = _now
    _sidebar_state.setdefault("schema_version", 1)
    platform.write_sidebar_state(_agent, _sidebar_state)

    _log(f"pre-created binding session + pin (cid={_cid[:12]})")


async def callback(request: Request) -> Response:
    """Exchange code for userinfo, generate token, redirect to /?nbtoken=TOKEN."""
    token_data = await PLATFORM.exchange_token(request)
    if not token_data:
        _log("OAuth exchange FAILED")
        return HTMLResponse("<p>❌ 登录失败</p>", status_code=400)

    userinfo = await PLATFORM.fetch_userinfo(token_data)
    username = PLATFORM.extract_username(userinfo or {})
    _log(f"login ✓  user={username}")

    # Auto-detect owner on first-ever login
    if not _get_owner() and username and username != "Unknown":
        os.environ["SPACE_AUTHOR"] = username
        _log(f"🎉 auto-detected owner: {username}")

    # Only the space owner may log in
    if not _check_owner(username):
        _log(f"⛔ rejected: {username} != owner {_get_owner()}")
        return HTMLResponse(
            "<p>⛔ 仅空间主人可使用此助手</p>"
            "<p style='color:#999'>请 fork 此 Space 并设置自己的 API Key</p>",
            status_code=403,
        )

    # Pre-create binding chat before redirect so sidebar pin is visible
    # on first page load (avoids needing a refresh to see the pin).
    try:
        _ensure_binding_session()
    except Exception as exc:
        _log(f"pre-create binding session failed: {exc}")

    # Generate token, set session (for HF), redirect with token
    nbtoken = _generate_token(username)
    try:
        request.session["user"] = {
            "username": username,
            "login_at": time.time(),
        }
    except Exception:
        pass  # session may not be available

    return RedirectResponse(f"/?nbtoken={nbtoken}", status_code=302)



# ═══════════════════════════════════════════════════════════════════
# Squad relay (single-agent mode)
# ═══════════════════════════════════════════════════════════════════


async def squad_relay(request: Request) -> Response:
    """POST /api/squad/relay — single-agent relay via WebSocket.

    Connects to the nanobot gateway WebSocket, sends the user message,
    collects the agent's text response, and returns it as JSON.
    """
    import websockets as ws_lib

    # Auth
    auth_header = request.headers.get("X-Squad-Token", "")
    if not RELAY_TOKEN or auth_header != RELAY_TOKEN:
        return JSONResponse(
            {"status": "unauthorized",
             "error": "invalid or missing X-Squad-Token"}, status_code=401)

    # Parse JSON body
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"status": "bad_request", "error": "invalid JSON"}, status_code=400)

    sender = (body.get("sender") or "").strip()
    target = (body.get("target") or "").strip()
    message = body.get("message") or ""
    commander = (body.get("commander") or "").strip()
    corr_id = body.get("correlation_id", f"sq-relay-{uuid.uuid4().hex[:8]}")

    if not sender or not message:
        return JSONResponse(
            {"status": "bad_request",
             "error": "missing sender or message"}, status_code=400)

    # Single-agent mode: target is informational, not used for routing
    if target:
        _log(f"[relay] {sender}→{target} (single-agent, ignoring target)")

    # Connect to nanobot gateway WebSocket
    ws_url = f"ws://127.0.0.1:{NANOBOT_WS_PORT}/"
    nanobot_token = os.environ.get("NANOBOT_TOKEN", "").strip()
    if nanobot_token:
        ws_url += f"?token={nanobot_token}"

    try:
        ws = await asyncio.wait_for(
            ws_lib.connect(ws_url, close_timeout=5), timeout=15)
        async with ws:
            # Wait for ready
            greeting_raw = await asyncio.wait_for(ws.recv(), timeout=10)
            greeting = json.loads(greeting_raw)
            if greeting.get("event") != "ready":
                _log(f"[relay] unexpected greeting: {greeting}")
                return JSONResponse({
                    "status": "protocol_error",
                    "error": f"expected 'ready', got {greeting.get('event')}",
                    "correlation_id": corr_id,
                }, status_code=502)

            chat_id = greeting.get("chat_id", "")
            _log(f"[relay] {sender} ws ok (cid={chat_id[:12] if chat_id else '?'})")

            # Build envelope
            envelope: dict = {
                "type": "message",
                "chat_id": chat_id,
                "content": message,
                "sender_id": f"agent:{sender}",
                "sender_name": sender,
            }
            if commander:
                envelope["commander_id"] = f"oauth:{commander}"
                envelope["commander_name"] = commander

            await ws.send(json.dumps(envelope))
            _log(f"[relay] {sender} → sent ({len(message)} chars)")

            # Collect response deltas until turn_end
            responses: list[str] = []
            try:
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=RELAY_TIMEOUT)
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        _log(f"[relay] non-JSON frame ({len(raw)}B)")
                        continue

                    event = data.get("event", "")

                    if event == "error":
                        detail = data.get("detail", "unknown")
                        _log(f"[relay] error: {detail}")
                        return JSONResponse({
                            "status": "framework_error",
                            "error": detail,
                            "correlation_id": corr_id,
                        }, status_code=502)

                    if event == "heartbeat":
                        continue

                    if event == "turn_end":
                        reply = "\n".join(responses) if responses else "(empty)"
                        _log(f"[relay] {sender} → done ({len(reply)} chars)")
                        return JSONResponse({
                            "status": "delivered",
                            "target_response": reply,
                            "correlation_id": corr_id,
                        })

                    if event == "delta":
                        text = data.get("text", "")
                        if text:
                            responses.append(text)
                        continue

                    if event == "stream_end":
                        continue

                    content_val = data.get("content")
                    if content_val and str(content_val).strip():
                        responses.append(str(content_val))

            except asyncio.TimeoutError:
                if responses:
                    reply = "\n".join(responses)
                    _log(f"[relay] timeout, partial ({len(reply)} chars)")
                    return JSONResponse({
                        "status": "partial",
                        "target_response": reply,
                        "correlation_id": corr_id,
                    })
                _log(f"[relay] timeout ({RELAY_TIMEOUT}s)")
                return JSONResponse({
                    "status": "timeout",
                    "error": f"no response within {RELAY_TIMEOUT}s",
                    "correlation_id": corr_id,
                }, status_code=504)

    except asyncio.TimeoutError:
        _log("[relay] connect timeout (15s)")
        return JSONResponse({
            "status": "connection_error",
            "error": "WebSocket connection timed out",
            "correlation_id": corr_id,
        }, status_code=502)
    except Exception as e:
        _log(f"[relay] error: {type(e).__name__}: {e}")
        return JSONResponse({
            "status": "connection_error",
            "error": f"{type(e).__name__}: {e}",
            "correlation_id": corr_id,
        }, status_code=502)


# ═══════════════════════════════════════════════════════════════════
# Health check
# ═══════════════════════════════════════════════════════════════════


async def health(request: Request) -> Response:
    """Proxy health check to nanobot gateway."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"http://127.0.0.1:{NANOBOT_GW_PORT}/health")
            return Response(content=resp.content, status_code=resp.status_code)
    except Exception:
        return Response(content=b"unhealthy", status_code=503)


# ═══════════════════════════════════════════════════════════════════
# Helpers for identity resolution
# ═══════════════════════════════════════════════════════════════════


def _identity(request: Request) -> str:
    """Resolve identity from scope (nbtoken or session)."""
    scope = request.scope
    username = scope.get("_auth_user", "")
    if not username:
        # Fallback to session
        user = request.session.get("user", {}) if hasattr(request, "session") else {}
        username = user.get("username", "")
    return f"oauth:{username}" if username else "oauth:anonymous"


# ═══════════════════════════════════════════════════════════════════
# Nanobot API token cache (bootstrap token, reused for API calls)
# ModelScope strips Authorization headers → we inject token via ?token=
# ═══════════════════════════════════════════════════════════════════

_nanobot_api_token: str | None = None
_nanobot_api_token_expiry: float = 0


def _get_or_refresh_nanobot_token() -> str | None:
    """Return a valid nanobot API token; refresh from local bootstrap if expired."""
    global _nanobot_api_token, _nanobot_api_token_expiry
    now = time.time()
    if _nanobot_api_token and now < _nanobot_api_token_expiry:
        return _nanobot_api_token
    # Fetch fresh token from nanobot's bootstrap (localhost — no auth needed)
    # Use synchronous urllib (called lazily, hits localhost, completes instantly)
    try:
        import urllib.request
        req = urllib.request.Request(
            f"http://127.0.0.1:{NANOBOT_WS_PORT}/webui/bootstrap",
            headers={"Host": "localhost"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            token = data.get("token")
            if token:
                _nanobot_api_token = token
                # Refresh at half the server-reported TTL (default 300s → 150s, min 30s)
                expires_in = int(data.get("expires_in", 300))
                refresh_ttl = max(expires_in // 2, 30)
                _nanobot_api_token_expiry = now + refresh_ttl
                _log(f"cached nanobot API token (refresh in {refresh_ttl}s)")
                return token
    except Exception as exc:
        _log(f"failed to refresh nanobot API token: {exc}")
    return _nanobot_api_token  # return stale token as fallback


# ═══════════════════════════════════════════════════════════════════
# HTTP proxy
# ═══════════════════════════════════════════════════════════════════


async def http_proxy(request: Request) -> Response:
    """Forward HTTP requests to nanobot, injecting token JS into HTML."""
    path = request.url.path
    upstream_url = f"{UPSTREAM}{path}"
    qs = request.url.query or ""

    # Inject nanobot API token for authenticated users on API endpoints.
    # ModelScope strips Authorization headers → pass token via ?token= instead.
    if path.startswith("/api/") and request.scope.get("_auth_user"):
        token = _get_or_refresh_nanobot_token()
        if token:
            params = parse_qs(qs) if qs else {}
            if "token" not in params:
                params["token"] = [token]
                qs = urlencode(params, doseq=True)
                _log(f"injected token into {path}")

    if qs:
        upstream_url += f"?{qs}"

    headers = dict(request.headers)
    headers["x-nanobot-identity"] = _identity(request)

    body = await request.body()
    async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
        resp = await client.request(
            method=request.method,
            url=upstream_url,
            headers=headers,
            content=body or None,
        )

    if path == "/api/sessions":
        try:
            sessions_data = json.loads(resp.content)
            ns = sessions_data.get("sessions", [])
            _log(f"GET /api/sessions → {resp.status_code} {len(ns)} sessions")
        except Exception:
            _log(f"GET /api/sessions → {resp.status_code}")

    content = resp.content

    # Fix ws_url in bootstrap response for platforms where the Host header
    # seen by nanobot is an internal proxy address (e.g., ModelScope PAI-EAS).
    # nanobot >= dbdb146f constructs ws_url from the Host header; we must
    # rewrite it to a relative path so the browser connects through this proxy.
    if path == "/webui/bootstrap" and resp.status_code == 200:
        try:
            data = json.loads(content)
            ws_path = data.get("ws_path", "")
            if ws_path:
                data["ws_url"] = ws_path
                content = json.dumps(data).encode("utf-8")
                _log("bootstrap ws_url → ws_path (Host header fix)")
        except Exception as exc:
            _log(f"bootstrap ws_url fix skipped: {exc}")

    content_type = resp.headers.get("content-type", "")
    if request.scope.get("_auth_token"):
        content = _inject_token_script(content, content_type)

    skip_headers = {"content-encoding", "content-length", "transfer-encoding"}
    return Response(
        content=content,
        status_code=resp.status_code,
        headers={k: v for k, v in resp.headers.items() if k.lower() not in skip_headers},
    )


# ═══════════════════════════════════════════════════════════════════
# WebSocket proxy
# ═══════════════════════════════════════════════════════════════════


async def ws_proxy(websocket: WebSocket) -> None:
    """Relay WebSocket connections; auth via nbtoken query param or session."""
    import websockets as ws_lib

    await websocket.accept()

    # Primary: nbtoken in WebSocket query string
    qs = websocket.url.query or ""
    params = parse_qs(qs)
    token = (params.get("nbtoken") or [None])[0]
    username = ""

    if token:
        username = _validate_token(token) or ""

    if not username:
        # Fallback: session
        try:
            user = websocket.session.get("user", {})
            username = user.get("username", "")
        except Exception:
            pass

    if not username:
        _log("WS rejected — no auth (token or session)")
        await websocket.close(code=4001, reason="Not authenticated")
        return

    identity = f"oauth:{username}"
    path = websocket.url.path
    ws_url = f"ws://127.0.0.1:{NANOBOT_WS_PORT}{path}"
    if websocket.url.query:
        ws_url += f"?{websocket.url.query}"

    _log(f"WS proxy → {username}")

    try:
        async with ws_lib.connect(
            ws_url,
            close_timeout=5,
            additional_headers={"x-nanobot-identity": identity},
        ) as upstream:
            async def c2u():
                while True:
                    try:
                        data = await websocket.receive_text()
                        # Inject sender_id/sender_name so nanobot
                        # recognizes the OAuth identity (cf. Legion
                        # gatekeeper identity propagation).
                        try:
                            envelope = json.loads(data)
                            if isinstance(envelope, dict):
                                envelope["sender_id"] = f"oauth:{username}"
                                envelope["sender_name"] = username
                                data = json.dumps(envelope)
                                _log(f"WS → neo: type={envelope.get('type','?')} cid={envelope.get('chat_id','?')[:12]}")

                                # Block messages to binding chat: don't forward to Neo.
                                _binding_cid = _get_binding_chat_id()
                                if _binding_cid and envelope.get("chat_id") == _binding_cid:
                                    if envelope.get("type") == "message":
                                        # User typed → static reply, no Neo
                                        _channels = "、".join(f"**绑定{b.display}**" for b in _bindings)
                                        _notice = f"👆 请点击上方链接绑定社交通道，无需在此聊天。\n\n点击 {_channels} 即可操作。"
                                        await websocket.send_text(json.dumps({
                                            "event": "delta", "data": _notice,
                                            "chat_id": _binding_cid,
                                            "sender_id": f"oauth:{username}",
                                            "sender_name": username,
                                        }))
                                        await websocket.send_text(json.dumps({
                                            "event": "stream_end", "chat_id": _binding_cid,
                                            "sender_id": f"oauth:{username}",
                                        }))
                                        await websocket.send_text(json.dumps({
                                            "event": "turn_end", "chat_id": _binding_cid,
                                            "sender_id": f"oauth:{username}",
                                        }))
                                        _log(f"WS → blocked binding chat msg, sent static reply")
                                        continue
                                    if envelope.get("type") == "attach":
                                        # User opened binding chat → send bulletin content
                                        await websocket.send_text(json.dumps({
                                            "event": "attached",
                                            "chat_id": _binding_cid,
                                        }))
                                        await websocket.send_text(json.dumps({
                                            "event": "delta", "data": BINDING_CHAT_CONTENT,
                                            "chat_id": _binding_cid,
                                            "sender_id": f"oauth:{username}",
                                            "sender_name": username,
                                        }))
                                        await websocket.send_text(json.dumps({
                                            "event": "stream_end", "chat_id": _binding_cid,
                                            "sender_id": f"oauth:{username}",
                                        }))
                                        await websocket.send_text(json.dumps({
                                            "event": "turn_end", "chat_id": _binding_cid,
                                            "sender_id": f"oauth:{username}",
                                        }))
                                        _log(f"WS → blocked binding chat attach, sent bulletin content")
                                        continue  # skip upstream.send()
                        except (json.JSONDecodeError, TypeError):
                            _log(f"WS → neo: non-JSON {data[:80]}")
                        await upstream.send(data)
                    except WebSocketDisconnect:
                        _log(f"WS c2u: client disconnected")
                        break
                    except Exception as exc:
                        _log(f"WS c2u error: {exc}")
                        break

            # ── Per-session state for chat init ──
            have_chat_id = asyncio.Event()
            current_chat_id: str | None = None

            async def u2c():
                nonlocal current_chat_id
                while True:
                    try:
                        data = await upstream.recv()
                        if isinstance(data, str):
                            try:
                                ev = json.loads(data)
                                # Detect "attached" → capture chat_id
                                if ev.get("event") == "attached":
                                    cid = ev.get("chat_id")
                                    if cid:
                                        current_chat_id = cid
                                        have_chat_id.set()
                                _log(f"WS ← neo: {ev.get('event', ev.get('type','?'))}")
                            except Exception:
                                _log(f"WS ← neo: raw {data[:60]}")
                        await websocket.send_text(data)
                    except ws_lib.exceptions.ConnectionClosed:
                        _log(f"WS u2c: neo disconnected")
                        break
                    except Exception as exc:
                        _log(f"WS u2c error: {exc}")
                        break

            async def setup_title():
                """Ensure a '社交通道配置提示' chat exists, is correctly titled, and pinned."""
                nonlocal current_chat_id
                _log(f"WS setup_title: started (username={username})")
                try:
                    import time as _time
                    from cloud_agent_gateway.platforms import platform
                    _agent = "default"
                    _now = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())

                    # ── Step 0: check for an existing pinned binding chat ──
                    _pinned_cid = None
                    _state = platform.read_sidebar_state(_agent)
                    for _pk in _state.get("pinned_keys", []):
                        if not isinstance(_pk, str) or not _pk.startswith("websocket:"):
                            continue
                        _cid = _pk.split(":", 1)[1]
                        _lines = platform.read_session(_agent, _cid)
                        if _lines and _lines[0].get("metadata", {}).get("title") == BINDING_TITLE:
                            _pinned_cid = _cid
                            break

                    if _pinned_cid:
                        # Existing pinned binding chat → redirect to it
                        current_chat_id = _pinned_cid
                        await upstream.send(json.dumps({
                            "type": "attach",
                            "chat_id": _pinned_cid,
                            "sender_id": f"oauth:{username}",
                            "sender_name": username,
                        }))
                        try:
                            await asyncio.wait_for(have_chat_id.wait(), timeout=5)
                        except asyncio.TimeoutError:
                            pass
                        _log(f"WS setup_title: reuse pinned chat (cid={current_chat_id[:12]})")
                    else:
                        # No existing binding chat → find or create
                        try:
                            await asyncio.wait_for(have_chat_id.wait(), timeout=4)
                            await asyncio.sleep(0.3)
                            _log(f"WS setup_title: got chat_id from client (cid={current_chat_id[:12] if current_chat_id else '?'})")
                        except asyncio.TimeoutError:
                            _log(f"WS setup_title: client didn't create chat in 4s, will create one")

                        if not current_chat_id:
                            await upstream.send(json.dumps({
                                "type": "new_chat",
                                "chat_id": str(uuid.uuid4()),
                                "sender_id": f"oauth:{username}",
                                "sender_name": username,
                            }))
                            try:
                                await asyncio.wait_for(have_chat_id.wait(), timeout=5)
                                await asyncio.sleep(0.2)
                            except asyncio.TimeoutError:
                                _log(f"WS setup_title: neo didn't attach new chat in 5s")

                    if current_chat_id:
                        _key = f"websocket:{current_chat_id}"

                        # ── Write/update session file via platform ──
                        _existing = platform.read_session(_agent, current_chat_id)
                        if _existing:
                            # Update: set title + replace first user message
                            _existing[0].setdefault("metadata", {})["title"] = BINDING_TITLE
                            _found = False
                            for _entry in _existing[1:]:
                                if _entry.get("role") == "user":
                                    _entry["content"] = BINDING_CHAT_CONTENT
                                    _entry["timestamp"] = _now
                                    _found = True
                                    break
                            if not _found:
                                _existing.append({
                                    "role": "user", "content": BINDING_CHAT_CONTENT,
                                    "timestamp": _now,
                                })
                            platform.write_session(_agent, current_chat_id, _existing)
                            _log(f"WS setup_title: updated session (cid={current_chat_id[:12]})")
                        else:
                            # Create new
                            platform.write_session(_agent, current_chat_id, [
                                {
                                    "_type": "metadata", "key": _key,
                                    "created_at": _now, "updated_at": _now,
                                    "metadata": {"title": BINDING_TITLE, "webui": True},
                                    "last_consolidated": 0,
                                },
                                {
                                    "role": "user", "content": BINDING_CHAT_CONTENT,
                                    "timestamp": _now,
                                },
                            ])
                            _log(f"WS setup_title: created session (cid={current_chat_id[:12]})")

                        # WebUI transcript (always overwrite)
                        platform.write_webui_transcript(_agent, current_chat_id, [
                            {"event": "delta", "text": BINDING_CHAT_CONTENT, "chat_id": current_chat_id},
                            {"event": "stream_end", "text": BINDING_CHAT_CONTENT, "chat_id": current_chat_id},
                            {"event": "turn_end", "chat_id": current_chat_id},
                        ])

                        # ── Pin to sidebar (idempotent) ──
                        _sidebar_state = platform.read_sidebar_state(_agent)
                        _pinned = _sidebar_state.setdefault("pinned_keys", [])
                        if _key not in _pinned:
                            _pinned.insert(0, _key)
                            _sidebar_state["updated_at"] = _now
                            _sidebar_state.setdefault("schema_version", 1)
                            platform.write_sidebar_state(_agent, _sidebar_state)
                            _log(f"WS setup_title: pinned chat (cid={current_chat_id[:12]})")

                        # ── Notify client ──
                        await websocket.send_text(json.dumps({
                            "event": "session_updated",
                            "chat_id": current_chat_id,
                            "scope": "updated",
                        }))
                        _log(f"WS → client: session_updated")
                except Exception as exc:
                    _log(f"WS setup_title error: {exc}")

            await asyncio.gather(c2u(), u2c(), setup_title(), return_exceptions=True)
    except Exception as exc:
        _log(f"WS proxy error: {exc}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


def _build_redirect_uri(request: Request, path: str) -> str:
    proto = request.headers.get("X-Forwarded-Proto", "https")
    host = request.headers.get("X-Forwarded-Host", request.headers.get("Host", ""))
    if not host and request.url.hostname:
        host = request.url.hostname
        if request.url.port and request.url.port not in (80, 443):
            host += f":{request.url.port}"
    return f"{proto}://{host}{path}"


def _get_owner() -> str:
    """Extract Space owner from environment."""
    owner = os.environ.get("SPACE_AUTHOR", "")
    if not owner:
        space_id = os.environ.get("SPACE_ID", "")
        owner = space_id.split("/")[0] if "/" in space_id else ""
    return owner


def _check_owner(username: str) -> bool:
    """True if username matches Space owner; True if owner detection fails (local dev)."""
    owner = _get_owner()
    if not owner:
        return True
    return username.lower() == owner.lower()


# ═══════════════════════════════════════════════════════════════════
# Channel binding — data-driven (routes + pages from BindingSpec registry)
# ═══════════════════════════════════════════════════════════════════


def _make_bind_page_handler(spec):
    """Closure: returns a handler that serves spec.bind_page_html."""
    async def _bind_page(request: Request) -> HTMLResponse:
        return HTMLResponse(spec.bind_page_html)
    return _bind_page


def _wrap_internal(handler):
    """Wrap a handler with localhost auth check."""
    async def _wrapper(request: Request) -> Response:
        _check_internal(request)
        return await handler(request)
    return _wrapper


async def _bind_status(request: Request) -> Response:
    """GET /api/bind/status — agent 查询绑定状态"""
    _check_internal(request)
    return Response(json.dumps(bind_status(), ensure_ascii=False), media_type="application/json")


def _check_internal(request: Request) -> None:
    """仅允许 localhost 内部调用。"""
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403)


# ═══════════════════════════════════════════════════════════════════
# Routes
# ═══════════════════════════════════════════════════════════════════

app.router.add_route(LOGIN_PATH, login_page, methods=["GET"])
app.router.add_route(LOGIN_START_PATH, login_start, methods=["GET"])
app.router.add_route(CALLBACK_PATH, callback, methods=["GET"])
app.router.add_route("/auth/callback", callback, methods=["GET"])
app.router.add_route("/login/callback", callback, methods=["GET"])
app.router.add_route("/health", health, methods=["GET"])
app.router.add_route("/api/squad/relay", squad_relay, methods=["POST"])

# Register binding routes from discovered specs
for _b in _bindings:
    # Bind page: GET /bind/<name>
    app.router.add_route(f"/bind/{_b.name}", _make_bind_page_handler(_b), methods=["GET"])
    # Public sub-routes: GET/POST /bind/<name>/<suffix>
    for _suffix, _method, _handler in _b.public_routes:
        app.router.add_route(f"/bind/{_b.name}{_suffix}", _handler, methods=[_method])
    # Internal sub-routes: GET/POST /api/bind/<name>/<suffix> (wrapped with auth check)
    for _suffix, _method, _handler in _b.internal_routes:
        app.router.add_route(f"/api/bind/{_b.name}{_suffix}", _wrap_internal(_handler), methods=[_method])

app.router.add_route("/api/bind/status", _bind_status, methods=["GET"])
app.router.add_route("/{path:path}", http_proxy, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
app.router.add_websocket_route("/{path:path}", ws_proxy)

# ═══════════════════════════════════════════════════════════════════
# Entry
# ═══════════════════════════════════════════════════════════════════


def main() -> None:
    port = int(os.environ.get("OAUTH_PROXY_PORT", "7860"))
    _log(f"listening on 0.0.0.0:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
