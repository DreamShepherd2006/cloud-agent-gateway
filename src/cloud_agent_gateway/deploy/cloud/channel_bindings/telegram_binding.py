"""Telegram bot token channel binding.

Registered via cloud_agent_gateway.channel_binding.register().
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
# Telegram binding logic
# ══════════════════════════════════════════════════

async def _bind(bot_token: str) -> dict:
    """验证并写入 Telegram Bot Token。"""
    if not bot_token or not bot_token.strip():
        return {"error": "Bot Token 不能为空"}

    # 验证 token 可用性
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            resp = await c.get(f"https://api.telegram.org/bot{bot_token}/getMe")
            data = resp.json()
            if not data.get("ok"):
                return {"error": f"Telegram Token 无效: {data.get('description', 'unknown error')}"}
            bot_name = data["result"]["username"]
    except Exception as e:
        return {"error": f"无法连接 Telegram API: {e}"}

    # 写入 config.json
    cp = config_path()
    cfg = load_json(cp)
    if "channels" not in cfg:
        cfg["channels"] = {}
    cfg["channels"]["telegram"] = {
        "enabled": True,
        "token": bot_token.strip(),
        "allow_from": ["*"],
    }
    with open(cp, "w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.chmod(cp, 0o600)

    return {"ok": True, "message": f"Telegram 已绑定 (@{bot_name})"}


def _is_bound() -> bool:
    """Check if telegram is already configured."""
    cfg = load_json(config_path())
    tg = cfg.get("channels", {}).get("telegram", {})
    return tg.get("enabled", False) and bool(tg.get("token"))


# ══════════════════════════════════════════════════
# Route handlers (Request → Response)
# ══════════════════════════════════════════════════

async def _submit_handler(request: Request) -> Response:
    """Submit Telegram bot token (public + internal)."""
    try:
        body = json.loads(await request.body())
    except Exception:
        return Response(
            json.dumps({"error": "invalid JSON"}, ensure_ascii=False),
            media_type="application/json",
            status_code=400,
        )
    data = await _bind(body.get("token", ""))
    return Response(json.dumps(data, ensure_ascii=False), media_type="application/json")


# ══════════════════════════════════════════════════
# Bind page HTML
# ══════════════════════════════════════════════════

TELEGRAM_BIND_PAGE = """\
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>绑定 Telegram</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{display:flex;align-items:center;justify-content:center;min-height:100vh;
     font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     background:#0f0f0f;color:#e0e0e0}
.card{text-align:center;padding:2rem;max-width:400px}
h1{font-size:1.6rem;margin-bottom:.5rem;color:#fff}
p{color:#999;margin-bottom:1rem;font-size:.9rem}
.hint{color:#666;font-size:.8rem;margin-bottom:1rem;text-align:left;
      background:#1a1a1a;border-radius:6px;padding:.8rem;line-height:1.6}
.hint code{color:#3b82f6;font-size:.85rem}
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
<h1>✈️ 绑定 Telegram</h1>
<p>输入 Bot Token 完成绑定</p>
<div class="hint">
从 <code>@BotFather</code> 获取 Token：<br>
1. 在 Telegram 中搜索 <code>@BotFather</code><br>
2. 发送 <code>/newbot</code> 创建机器人<br>
3. 复制返回的 API Token 粘贴到下方
</div>
<form id="bind-form">
<label for="token">Bot Token</label>
<input id="token" name="token" placeholder="123456:ABC-DEF1234gh" required>
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
    let r=await fetch('/bind/telegram/submit',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        token:document.getElementById('token').value
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
    name="telegram",
    display="Telegram",
    icon="✈️",
    bind_page_html=TELEGRAM_BIND_PAGE,
    public_routes=[
        ("/submit", "POST", _submit_handler),
    ],
    internal_routes=[
        ("/submit", "POST", _submit_handler),
    ],
    is_bound=_is_bound,
)
register(spec)
