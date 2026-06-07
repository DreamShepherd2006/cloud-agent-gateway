"""Feishu (飞书) credential binding with OAuth installation flow.

Registered via cloud_agent_gateway.channel_binding.register().

Flow:
  1. 用户输入 App ID + App Secret → 保存到 config.json
  2. 尝试获取 tenant_access_token 验证凭证
  3. 如果应用已安装 → 直接完成
  4. 如果应用未安装 → 显示「安装到飞书」OAuth 按钮
  5. OAuth 跳转飞书 → 管理员授权 → 应用被安装
  6. 回调 → 重新验证 tenant_access_token → 完成

飞书 OAuth 授权 URL (user authorization):
  https://accounts.feishu.cn/open-apis/authen/v1/authorize
    ?client_id={APP_ID}
    &response_type=code
    &redirect_uri={REDIRECT_URI}
    &scope=...
    &state={STATE}
"""

from __future__ import annotations

import json
import os
import secrets
import time
import urllib.parse

import httpx
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from cloud_agent_gateway.channel_binding import (
    BindingSpec,
    config_path,
    load_json,
    register,
)


# ══════════════════════════════════════════════════
# OAuth state management (in-memory, shared within process)
# ══════════════════════════════════════════════════

_OAUTH_STATES: dict[str, dict] = {}
_STATE_MAX_AGE = 600  # 10 minutes


def _generate_state(app_id: str) -> str:
    """Generate CSRF state token for OAuth flow."""
    token = secrets.token_urlsafe(32)
    _OAUTH_STATES[token] = {
        "app_id": app_id,
        "created_at": time.time(),
    }
    return token


def _cleanup_states() -> None:
    """Remove expired state tokens."""
    now = time.time()
    expired = [
        k for k, v in _OAUTH_STATES.items()
        if now - v["created_at"] > _STATE_MAX_AGE
    ]
    for k in expired:
        _OAUTH_STATES.pop(k, None)


# ══════════════════════════════════════════════════
# Feishu binding logic
# ══════════════════════════════════════════════════

async def _bind(app_id: str, app_secret: str) -> dict:
    """验证飞书凭证，写入 config.json。

    Returns:
        {"ok": True, "installed": True,  "message": ...}  凭证正确且应用已安装
        {"ok": True, "installed": False, "message": ...}  凭证已保存但未安装
        {"error": "..."}                                   凭证错误或其他失败
    """
    if not app_id or not app_id.strip():
        return {"error": "App ID 不能为空"}
    if not app_secret or not app_secret.strip():
        return {"error": "App Secret 不能为空"}

    app_id = app_id.strip()
    app_secret = app_secret.strip()

    # ═══ 验证凭证：尝试获取 tenant_access_token ═══
    installed = False
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            resp = await c.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
            )
            data = resp.json()
            err_code = data.get("code")
            if err_code == 0:
                installed = True
            elif err_code in (20018, 20029):
                # 20018: app is not enabled (应用存在但未安装到企业)
                # 20029: app version not created / admin not authorized
                installed = False
            elif err_code == 10002:
                return {"error": "App ID 或 App Secret 不正确，请检查后重试"}
            else:
                return {
                    "error": f"飞书 API 错误: {data.get('msg', 'unknown')} (code={err_code})"
                }
    except Exception as e:
        return {"error": f"无法连接飞书 API: {e}"}

    # ═══ 写入 config.json ═══
    cp = config_path()
    cfg = load_json(cp)
    if "channels" not in cfg:
        cfg["channels"] = {}
    cfg["channels"]["feishu"] = {
        "enabled": True,
        "app_id": app_id,
        "app_secret": app_secret,
        "allow_from": ["*"],
    }
    with open(cp, "w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.chmod(cp, 0o600)

    if installed:
        return {"ok": True, "installed": True, "message": "飞书已绑定，应用已安装 ✅"}
    else:
        return {
            "ok": True,
            "installed": False,
            "message": "凭证已保存，但应用尚未安装到企业",
        }


def _is_bound() -> bool:
    """Check if feishu is already configured."""
    cfg = load_json(config_path())
    fs = cfg.get("channels", {}).get("feishu", {})
    return fs.get("enabled", False) and bool(fs.get("app_id"))


async def _verify_installed() -> tuple[bool, str]:
    """Re-verify tenant_access_token with saved credentials.

    Returns:
        (True, "ok message") if installed
        (False, "error message") if not
    """
    cfg = load_json(config_path())
    fs = cfg.get("channels", {}).get("feishu", {})
    app_id = fs.get("app_id", "")
    app_secret = fs.get("app_secret", "")

    if not app_id or not app_secret:
        return False, "未找到已保存的飞书凭证，请重新提交"

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            resp = await c.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
            )
            data = resp.json()
            if data.get("code") == 0:
                return True, "飞书应用已安装，绑定完成 ✅"
            else:
                return False, f"应用尚未安装: {data.get('msg', 'unknown')}"
    except Exception as e:
        return False, f"无法验证: {e}"


# ══════════════════════════════════════════════════
# Route handlers (Request → Response)
# ══════════════════════════════════════════════════

async def _submit_handler(request: Request) -> Response:
    """Submit Feishu credentials (public + internal)."""
    try:
        body = json.loads(await request.body())
    except Exception:
        return Response(
            json.dumps({"error": "invalid JSON"}, ensure_ascii=False),
            media_type="application/json",
            status_code=400,
        )

    data = await _bind(body.get("app_id", ""), body.get("app_secret", ""))

    # 如果凭证已保存但应用未安装，返回 OAuth 授权 URL
    if data.get("ok") and not data.get("installed"):
        app_id = body.get("app_id", "").strip()
        host = request.headers.get("host", "")
        scheme = request.url.scheme
        if host:
            redirect_uri = f"{scheme}://{host}/bind/feishu/callback"
        else:
            # Fallback: construct from request.url
            redirect_uri = (
                f"{request.url.scheme}://{request.url.netloc}/bind/feishu/callback"
            )

        state = _generate_state(app_id)
        auth_url = (
            "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
            f"?client_id={urllib.parse.quote(app_id, safe='')}"
            "&response_type=code"
            f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
            f"&scope={urllib.parse.quote('contact:contact.base:readonly', safe='')}"
            f"&state={urllib.parse.quote(state, safe='')}"
            "&prompt=consent"
        )
        data["auth_url"] = auth_url
        data["redirect_uri"] = redirect_uri

    return Response(json.dumps(data, ensure_ascii=False), media_type="application/json")


async def _callback_handler(request: Request) -> Response:
    """Handle OAuth callback from Feishu.

    The user comes back from Feishu's authorization page.
    We re-verify tenant_access_token to confirm installation.
    """
    _cleanup_states()

    state = request.query_params.get("state", "")
    _ = request.query_params.get("code", "")  # Not used; we verify via tenant_access_token

    # Verify state
    if not state:
        return HTMLResponse(
            FEISHU_CALLBACK_ERROR_HTML.format(
                error="缺少 state 参数，请重新开始绑定流程"
            ),
            status_code=400,
        )

    state_data = _OAUTH_STATES.pop(state, None)
    if not state_data:
        return HTMLResponse(
            FEISHU_CALLBACK_ERROR_HTML.format(
                error="state 无效或已过期（10 分钟），请重新开始绑定流程"
            ),
            status_code=400,
        )

    # Re-verify: try tenant_access_token
    ok, msg = await _verify_installed()
    if ok:
        return HTMLResponse(FEISHU_CALLBACK_SUCCESS_HTML.format(message=msg))
    else:
        return HTMLResponse(
            FEISHU_CALLBACK_ERROR_HTML.format(error=msg),
            status_code=400,
        )


# ══════════════════════════════════════════════════
# Bind page HTML
# ══════════════════════════════════════════════════

FEISHU_BIND_PAGE = """\
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>绑定飞书</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{display:flex;align-items:center;justify-content:center;min-height:100vh;
     font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     background:#0f0f0f;color:#e0e0e0}
.card{text-align:center;padding:2rem;max-width:440px;width:100%}
h1{font-size:1.6rem;margin-bottom:.5rem;color:#fff}
p{color:#999;margin-bottom:1rem;font-size:.9rem}
.hint{color:#666;font-size:.8rem;margin-bottom:1rem;text-align:left;
      background:#1a1a1a;border-radius:6px;padding:.8rem;line-height:1.6}
.hint a{color:#00c4ff;font-size:.85rem}
.hint code{color:#07c160;font-size:.8rem;word-break:break-all}
form{text-align:left}
label{display:block;margin:.8rem 0 .3rem;color:#ccc;font-size:.9rem}
input{width:100%;padding:10px 12px;border-radius:6px;border:1px solid #333;
       background:#1a1a1a;color:#e0e0e0;font-size:.95rem}
input:focus{outline:none;border-color:#00c4ff}
.btn{display:block;width:100%;margin-top:1.2rem;padding:10px;
     border-radius:6px;background:#00c4ff;color:#fff;font-size:1rem;
     font-weight:600;border:none;cursor:pointer;text-decoration:none;text-align:center}
.btn:hover{background:#009fd4}
.btn.oauth{background:#3370ff;margin-top:.8rem}
.btn.oauth:hover{background:#245bdb}
#msg{margin-top:1rem;text-align:center;font-size:.9rem}
#msg.success{color:#07c160}
#msg.info{color:#00c4ff}
#msg.error{color:#ef4444}
#oauth-section{display:none;margin-top:1rem}
#oauth-section p{color:#999;font-size:.85rem}
#oauth-section code{color:#00c4ff;font-size:.75rem}
.back{margin-top:1.5rem;text-align:center}
.back a{color:#666;font-size:.85rem;text-decoration:none}
.back a:hover{color:#999}
</style>
</head>
<body>
<div class="card">
<h1>🕊️ 绑定飞书</h1>
<p>输入飞书应用凭证，一键安装到企业</p>
<div class="hint">
  从 <a href="https://open.feishu.cn/app" target="_blank">飞书开放平台</a> 获取凭证：<br>
  1. 创建「企业自建应用」<br>
  2. 在「凭证与基础信息」中复制 App ID 和 App Secret<br>
  3. 在「安全设置」中添加重定向 URL：<br>
  &nbsp;&nbsp;<code id="redirect-url-display">（提交后自动生成）</code>
</div>
<form id="bind-form">
<label for="app_id">App ID</label>
<input id="app_id" name="app_id" placeholder="cli_xxxxxxxx" required>
<label for="app_secret">App Secret</label>
<input id="app_secret" name="app_secret" type="password"
       placeholder="xxxxxxxx" required>
<button type="submit" class="btn" id="submit-btn">保存凭证</button>
</form>
<div id="msg"></div>
<div id="oauth-section">
  <p>凭证已保存。现在将应用安装到你的飞书企业：</p>
  <a id="oauth-link" class="btn oauth" href="#" target="_self">
    🚀 安装到飞书
  </a>
  <p style="margin-top:.8rem;font-size:.75rem;color:#666">
    将跳转到飞书授权页面，请用企业管理员身份完成授权
  </p>
</div>
<div class="back"><a href="/">← 返回对话</a></div>
</div>
<script>
var authUrl = '';

document.getElementById('bind-form').addEventListener('submit',async function(e){
  e.preventDefault();
  let msg=document.getElementById('msg');
  let oauth=document.getElementById('oauth-section');
  let submitBtn=document.getElementById('submit-btn');
  oauth.style.display='none';
  msg.textContent='验证中...';msg.className='';
  submitBtn.disabled=true;submitBtn.textContent='验证中...';

  try{
    let r=await fetch('/bind/feishu/submit',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        app_id:document.getElementById('app_id').value.trim(),
        app_secret:document.getElementById('app_secret').value.trim()
      })
    });
    let d=await r.json();
    if(d.ok){
      if(d.installed){
        msg.className='success';msg.textContent='✅ '+d.message;
      }else if(d.auth_url){
        // 凭证正确，但应用未安装 → 显示 OAuth 按钮
        msg.className='info';
        msg.textContent='📋 '+d.message;
        authUrl=d.auth_url;
        document.getElementById('redirect-url-display').textContent=
          d.redirect_uri||'(自动生成)';
        oauth.style.display='block';
        // 隐藏表单，只留基本信息
        document.getElementById('bind-form').style.display='none';
      }else{
        msg.className='success';msg.textContent='✅ '+d.message;
      }
    }else{
      msg.className='error';msg.textContent='❌ '+(d.error||'绑定失败');
    }
  }catch(e){
    msg.className='error';msg.textContent='网络错误: '+e;
  }
  submitBtn.disabled=false;submitBtn.textContent='保存凭证';
});

document.getElementById('oauth-link').addEventListener('click',function(e){
  e.preventDefault();
  if(authUrl){
    window.location.href=authUrl;
  }
});
</script>
</body>
</html>"""


# ══════════════════════════════════════════════════
# Callback page HTML
# ══════════════════════════════════════════════════

FEISHU_CALLBACK_SUCCESS_HTML = """\
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>飞书绑定成功</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{display:flex;align-items:center;justify-content:center;min-height:100vh;
     font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     background:#0f0f0f;color:#e0e0e0}
.card{text-align:center;padding:2rem;max-width:400px}
h1{font-size:1.6rem;margin-bottom:1rem;color:#fff}
p{color:#999;margin-bottom:1.5rem;font-size:.9rem}
.btn{display:inline-block;padding:10px 24px;border-radius:6px;
     background:#00c4ff;color:#fff;font-size:1rem;font-weight:600;
     text-decoration:none}
.btn:hover{background:#009fd4}
</style>
</head>
<body>
<div class="card">
<h1>✅ 飞书绑定完成</h1>
<p>{message}</p>
<a class="btn" href="/">返回对话</a>
</div>
</body>
</html>"""

FEISHU_CALLBACK_ERROR_HTML = """\
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>飞书绑定失败</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{display:flex;align-items:center;justify-content:center;min-height:100vh;
     font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     background:#0f0f0f;color:#e0e0e0}
.card{text-align:center;padding:2rem;max-width:400px}
h1{font-size:1.6rem;margin-bottom:1rem;color:#ef4444}
p{color:#999;margin-bottom:1.5rem;font-size:.9rem;line-height:1.6}
.hint{color:#666;font-size:.8rem;background:#1a1a1a;border-radius:6px;
      padding:.8rem;text-align:left;line-height:1.6;margin-bottom:1.5rem}
.btn{display:inline-block;padding:10px 24px;border-radius:6px;
     background:#00c4ff;color:#fff;font-size:1rem;font-weight:600;
     text-decoration:none;margin:0 .3rem}
.btn:hover{background:#009fd4}
</style>
</head>
<body>
<div class="card">
<h1>❌ 绑定失败</h1>
<p>{error}</p>
<div class="hint">
  请确保：<br>
  1. 已在飞书开放平台创建应用版本并发布<br>
  2. 企业管理员在飞书管理后台完成安装授权<br>
  3. 应用的「安全设置」中已添加重定向 URL<br>
  4. App ID 和 App Secret 无误
</div>
<a class="btn" href="/bind/feishu">重新绑定</a>
<a class="btn" href="/">返回对话</a>
</div>
</body>
</html>"""


# ══════════════════════════════════════════════════
# Registration
# ══════════════════════════════════════════════════

spec = BindingSpec(
    name="feishu",
    display="飞书",
    icon="🕊️",
    bind_page_html=FEISHU_BIND_PAGE,
    public_routes=[
        ("/submit", "POST", _submit_handler),
        ("/callback", "GET", _callback_handler),
    ],
    internal_routes=[
        ("/submit", "POST", _submit_handler),
        ("/callback", "GET", _callback_handler),
    ],
    is_bound=_is_bound,
)
register(spec)
