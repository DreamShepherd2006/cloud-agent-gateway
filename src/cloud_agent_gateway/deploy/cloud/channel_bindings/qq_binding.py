"""QQ Bot credential-based channel binding.

Registered via cloud_agent_gateway.channel_binding.register().

Pattern: same as feishu/dingtalk — validate creds, write config.json + account.json,
then the auto-reload patch polls account.json to start the channel at runtime.

QQ uses the qq-botpy SDK with WebSocket (same pattern as dingtalk Stream Mode).
Credentials: AppID (机器人ID) + Secret (机器人密钥).
"""

from __future__ import annotations

import json
import os

import httpx
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from cloud_agent_gateway.channel_binding import (
    BindingSpec,
    register,
    read_config_cloud,
    read_credential_cloud,
    write_config_cloud,
    write_credential_cloud,
)


# ══════════════════════════════════════════════════
# QQ binding logic
# ══════════════════════════════════════════════════

async def _bind(app_id: str, secret: str) -> dict:
    """验证凭证，写入 config.json 和 account.json（通过 PersistentStorageProtocol）。

    Returns:
        {"ok": True, "message": "..."}  成功
        {"error": "..."}                  失败
    """
    if not app_id or not app_id.strip():
        return {"error": "AppID (机器人ID) 不能为空"}
    if not secret or not secret.strip():
        return {"error": "Secret (机器人密钥) 不能为空"}

    app_id = app_id.strip()
    secret = secret.strip()

    # 验证凭证：尝试获取 access_token
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            resp = await c.post(
                "https://bots.qq.com/app/getAppAccessToken",
                json={"appId": app_id, "clientSecret": secret},
            )
            data = resp.json()
            if "access_token" not in data:
                err_msg = data.get("message", "凭证无效")
                return {"error": f"QQ 凭证无效: {err_msg}"}
    except Exception as e:
        return {"error": f"无法连接 QQ API: {e}"}

    # 写入 config.json + account.json（通过平台持久化接口）
    cfg = read_config_cloud()
    if "channels" not in cfg:
        cfg["channels"] = {}
    cfg["channels"]["qq"] = {
        "enabled": True,
        "app_id": app_id,
        "secret": secret,
        "allow_from": ["*"],
    }
    write_config_cloud(cfg)
    write_credential_cloud("qq", {"app_id": app_id, "secret": secret})

    return {"ok": True, "message": "QQ 已绑定"}


def _is_bound() -> bool:
    """Check if QQ is already configured (credential file is source of truth)."""
    acc = read_credential_cloud("qq")
    if acc.get("app_id") and acc.get("secret"):
        return True
    # Fallback: check config.json for pre-existing binding
    cfg = read_config_cloud()
    q = cfg.get("channels", {}).get("qq", {})
    return q.get("enabled", False) and bool(q.get("app_id"))


# ══════════════════════════════════════════════════
# Route handlers (Request → Response)
# ══════════════════════════════════════════════════

async def _submit_handler(request: Request) -> Response:
    """Submit QQ credentials (public + internal)."""
    try:
        body = json.loads(await request.body())
    except Exception:
        return Response(
            json.dumps({"error": "invalid JSON"}, ensure_ascii=False),
            media_type="application/json",
            status_code=400,
        )
    data = await _bind(body.get("app_id", ""), body.get("secret", ""))
    return Response(json.dumps(data, ensure_ascii=False), media_type="application/json")


# ══════════════════════════════════════════════════
# Bind page HTML
# ══════════════════════════════════════════════════

QQ_BIND_PAGE = """\
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>绑定 QQ</title>
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
<h1>🐧 绑定 QQ</h1>
<p>输入 QQ 机器人凭证完成绑定</p>
 <div class="hint">
  从 <a href="https://q.qq.com/qqbot/#/developer/sandbox" target="_blank">QQ 机器人开放平台</a> 获取凭证：<br>
  1. 创建机器人 → 沙箱 或 正式环境<br>
  2. 在「开发设置」中复制 <b>BotAppID</b> 和 <b>机器人密钥</b>
 </div>
<form id="bind-form">
<label for="app_id">AppID（机器人ID）</label>
<input id="app_id" name="app_id" placeholder="10xxxxxxxxx" required>
<label for="secret">Secret（机器人密钥）</label>
<input id="secret" name="secret" type="password"
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
    let r=await fetch('/bind/qq/submit',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        app_id:document.getElementById('app_id').value,
        secret:document.getElementById('secret').value
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
    name="qq",
    display="QQ",
    icon="🐧",
    bind_page_html=QQ_BIND_PAGE,
    public_routes=[
        ("/submit", "POST", _submit_handler),
    ],
    internal_routes=[
        ("/submit", "POST", _submit_handler),
    ],
    is_bound=_is_bound,
)
register(spec)
