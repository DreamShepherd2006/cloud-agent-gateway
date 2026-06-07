"""DingTalk credential-based channel binding.

Registered via cloud_agent_gateway.channel_binding.register().

Pattern: same as feishu/wechat — validate creds, write config.json + account.json,
then the auto-reload patch polls account.json to start the channel at runtime.
"""

from __future__ import annotations

import json
import os

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
# Persistent account.json path (mirrors feishu/wechat pattern)
# ══════════════════════════════════════════════════

def _account_path() -> str:
    return os.path.join(
        os.path.expanduser("~/.nanobot"), "dingtalk", "account.json"
    )


def _load_account() -> dict:
    ap = _account_path()
    if os.path.exists(ap):
        try:
            with open(ap) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_account(client_id: str, client_secret: str) -> None:
    ap = _account_path()
    os.makedirs(os.path.dirname(ap), exist_ok=True)
    with open(ap, "w") as f:
        json.dump(
            {"client_id": client_id, "client_secret": client_secret},
            f,
            ensure_ascii=False,
        )
    os.chmod(ap, 0o600)


# ══════════════════════════════════════════════════
# DingTalk binding logic
# ══════════════════════════════════════════════════

async def _bind(client_id: str, client_secret: str) -> dict:
    """验证凭证，写入 config.json 和 account.json。

    Returns:
        {"ok": True, "message": "..."}  成功
        {"error": "..."}                  失败
    """
    if not client_id or not client_id.strip():
        return {"error": "Client ID (AppKey) 不能为空"}
    if not client_secret or not client_secret.strip():
        return {"error": "Client Secret (AppSecret) 不能为空"}

    client_id = client_id.strip()
    client_secret = client_secret.strip()

    # 验证凭证：尝试获取 access_token
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            resp = await c.post(
                "https://api.dingtalk.com/v1.0/oauth2/accessToken",
                json={"appKey": client_id, "appSecret": client_secret},
            )
            if resp.status_code != 200:
                data = resp.json()
                err_msg = data.get("message") or data.get("errmsg") or "凭证无效"
                return {"error": f"钉钉凭证无效: {err_msg}"}
    except Exception as e:
        return {"error": f"无法连接钉钉 API: {e}"}

    # 写入 config.json
    cp = config_path()
    cfg = load_json(cp)
    if "channels" not in cfg:
        cfg["channels"] = {}
    cfg["channels"]["dingtalk"] = {
        "enabled": True,
        "client_id": client_id,
        "client_secret": client_secret,
        "allow_from": ["*"],
    }
    with open(cp, "w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.chmod(cp, 0o600)

    # 写入 account.json（持久化，供 nanobot 通道轮询使用）
    _save_account(client_id, client_secret)

    return {"ok": True, "message": "钉钉已绑定"}


def _is_bound() -> bool:
    """Check if dingtalk is already configured (account.json is source of truth)."""
    acc = _load_account()
    if acc.get("client_id") and acc.get("client_secret"):
        return True
    # Fallback: check config.json for pre-existing binding
    cfg = load_json(config_path())
    dt = cfg.get("channels", {}).get("dingtalk", {})
    return dt.get("enabled", False) and bool(dt.get("client_id"))


# ══════════════════════════════════════════════════
# Route handlers (Request → Response)
# ══════════════════════════════════════════════════

async def _submit_handler(request: Request) -> Response:
    """Submit DingTalk credentials (public + internal)."""
    try:
        body = json.loads(await request.body())
    except Exception:
        return Response(
            json.dumps({"error": "invalid JSON"}, ensure_ascii=False),
            media_type="application/json",
            status_code=400,
        )
    data = await _bind(body.get("client_id", ""), body.get("client_secret", ""))
    return Response(json.dumps(data, ensure_ascii=False), media_type="application/json")


# ══════════════════════════════════════════════════
# Bind page HTML
# ══════════════════════════════════════════════════

DINGTALK_BIND_PAGE = """\
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>绑定钉钉</title>
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
 .hint a{color:#3b82f6;font-size:.85rem}
 form{text-align:left}
label{display:block;margin:.8rem 0 .3rem;color:#ccc;font-size:.9rem}
input{width:100%;padding:10px 12px;border-radius:6px;border:1px solid #333;
       background:#1a1a1a;color:#e0e0e0;font-size:.95rem}
input:focus{outline:none;border-color:#3b82f6}
.btn{display:block;width:100%;margin-top:1.2rem;padding:10px;
     border-radius:6px;background:#3b82f6;color:#fff;font-size:1rem;
     font-weight:600;border:none;cursor:pointer}
.btn:hover{background:#2563eb}
#msg{margin-top:1rem;text-align:center;font-size:.9rem}
#msg.success{color:#07c160}
#msg.error{color:#ef4444}
.back{margin-top:1.5rem;text-align:center}
.back a{color:#666;font-size:.85rem;text-decoration:none}
.back a:hover{color:#999}
</style>
</head>
<body>
<div class="card">
<h1>📎 绑定钉钉</h1>
<p>输入钉钉应用凭证完成绑定</p>
 <div class="hint">
  从 <a href="https://open-dev.dingtalk.com/" target="_blank">钉钉开发者后台</a> 获取凭证：<br>
  1. 创建应用 → 添加「机器人」能力<br>
  2. 消息接收模式选 <b>Stream 模式</b><br>
  3. 在「凭证与基础信息」中复制 AppKey 和 AppSecret<br>
  4. 发布应用
 </div>
<form id="bind-form">
<label for="client_id">AppKey</label>
<input id="client_id" name="client_id" placeholder="dingxxxxxxxx" required>
<label for="client_secret">AppSecret</label>
<input id="client_secret" name="client_secret" type="password"
       placeholder="xxxxxxxx" required>
<button type="submit" class="btn">绑定</button>
</form>
<div id="msg"></div>
<div class="back"><a href="/">← 返回对话</a></div>
</div>
<script>
document.getElementById('bind-form').addEventListener('submit',async function(e){
  e.preventDefault();
  let msg=document.getElementById('msg');
  msg.textContent='绑定中...';msg.className='';
  try{
    let r=await fetch('/bind/dingtalk/submit',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        client_id:document.getElementById('client_id').value,
        client_secret:document.getElementById('client_secret').value
      })
    });
    let d=await r.json();
     if(d.ok){
       msg.className='success';msg.textContent='✅ '+d.message;
     }else{
       msg.className='error';msg.textContent='❌ '+(d.error||'绑定失败');
     }
  }catch(e){msg.className='error';msg.textContent='网络错误: '+e}
});
</script>
</body>
</html>"""


# ══════════════════════════════════════════════════
# Registration
# ══════════════════════════════════════════════════

spec = BindingSpec(
    name="dingtalk",
    display="钉钉",
    icon="📎",
    bind_page_html=DINGTALK_BIND_PAGE,
    public_routes=[
        ("/submit", "POST", _submit_handler),
    ],
    internal_routes=[
        ("/submit", "POST", _submit_handler),
    ],
    is_bound=_is_bound,
)
register(spec)
