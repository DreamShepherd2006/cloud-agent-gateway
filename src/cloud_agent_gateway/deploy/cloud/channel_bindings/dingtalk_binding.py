"""DingTalk credential-based channel binding.

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
# DingTalk binding logic
# ══════════════════════════════════════════════════

async def _bind(client_id: str, client_secret: str) -> dict:
    """验证并写入钉钉凭证。"""
    if not client_id or not client_secret:
        return {"error": "client_id 和 client_secret 不能为空"}

    # 验证凭证可用性
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            resp = await c.post(
                "https://api.dingtalk.com/v1.0/oauth2/accessToken",
                json={"appKey": client_id, "appSecret": client_secret},
            )
            if resp.status_code != 200:
                return {"error": f"钉钉凭证无效 ({resp.status_code})"}
    except Exception as e:
        return {"error": f"无法连接钉钉 API: {e}"}

    # 写入 config.json（与 nanobot dingtalk channel 配置兼容）
    cp = config_path()
    cfg = load_json(cp)
    if "channels" not in cfg:
        cfg["channels"] = {}
    cfg["channels"]["dingtalk"] = {
        "enabled": True,
        "clientId": client_id,
        "clientSecret": client_secret,
        "allowFrom": ["*"],
    }
    with open(cp, "w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.chmod(cp, 0o600)

    return {"ok": True, "message": "钉钉已绑定"}


def _is_bound() -> bool:
    """Check if dingtalk is already configured."""
    cfg = load_json(config_path())
    dt = cfg.get("channels", {}).get("dingtalk", {})
    return dt.get("enabled", False) and bool(dt.get("clientId"))


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
.card{text-align:center;padding:2rem;max-width:400px}
h1{font-size:1.6rem;margin-bottom:.5rem;color:#fff}
p{color:#999;margin-bottom:1rem;font-size:.9rem}
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
    if(d.errcode===0||d.status==='ok'){
      msg.className='success';msg.textContent='✅ 钉钉绑定成功！';
    }else{
      msg.className='error';msg.textContent='❌ '+(d.error||d.errmsg||'绑定失败');
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
