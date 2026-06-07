"""Feishu (飞书) credential-based channel binding.

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
# Feishu binding logic
# ══════════════════════════════════════════════════

async def _bind(app_id: str, app_secret: str) -> dict:
    """验证并写入飞书凭证。"""
    if not app_id or not app_secret:
        return {"error": "App ID 和 App Secret 不能为空"}

    # 验证凭证可用性（飞书获取 tenant_access_token）
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            resp = await c.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
            )
            data = resp.json()
            if data.get("code") != 0:
                return {"error": f"飞书凭证无效: {data.get('msg', 'unknown error')}"}
    except Exception as e:
        return {"error": f"无法连接飞书 API: {e}"}

    # 写入 config.json
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

    return {"ok": True, "message": "飞书已绑定"}


def _is_bound() -> bool:
    """Check if feishu is already configured."""
    cfg = load_json(config_path())
    fs = cfg.get("channels", {}).get("feishu", {})
    return fs.get("enabled", False) and bool(fs.get("app_id"))


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
    return Response(json.dumps(data, ensure_ascii=False), media_type="application/json")


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
.card{text-align:center;padding:2rem;max-width:400px}
h1{font-size:1.6rem;margin-bottom:.5rem;color:#fff}
p{color:#999;margin-bottom:1rem;font-size:.9rem}
.hint{color:#666;font-size:.8rem;margin-bottom:1rem;text-align:left;
      background:#1a1a1a;border-radius:6px;padding:.8rem;line-height:1.6}
.hint a{color:#00c4ff;font-size:.85rem}
form{text-align:left}
label{display:block;margin:.8rem 0 .3rem;color:#ccc;font-size:.9rem}
input{width:100%;padding:10px 12px;border-radius:6px;border:1px solid #333;
       background:#1a1a1a;color:#e0e0e0;font-size:.95rem}
input:focus{outline:none;border-color:#00c4ff}
.btn{display:block;width:100%;margin-top:1.2rem;padding:10px;
     border-radius:6px;background:#00c4ff;color:#fff;font-size:1rem;
     font-weight:600;border:none;cursor:pointer}
.btn:hover{background:#009fd4}
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
<h1>🕊️ 绑定飞书</h1>
<p>输入飞书应用凭证完成绑定</p>
<div class="hint">
从 <a href="https://open.feishu.cn/app" target="_blank">飞书开放平台</a> 获取凭证：<br>
1. 创建企业自建应用<br>
2. 在「凭证与基础信息」中复制 App ID 和 App Secret<br>
3. 开启应用所需权限并发布
</div>
<form id="bind-form">
<label for="app_id">App ID</label>
<input id="app_id" name="app_id" placeholder="cli_xxxxxxxx" required>
<label for="app_secret">App Secret</label>
<input id="app_secret" name="app_secret" type="password"
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
    let r=await fetch('/bind/feishu/submit',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        app_id:document.getElementById('app_id').value,
        app_secret:document.getElementById('app_secret').value
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
    name="feishu",
    display="飞书",
    icon="🕊️",
    bind_page_html=FEISHU_BIND_PAGE,
    public_routes=[
        ("/submit", "POST", _submit_handler),
    ],
    internal_routes=[
        ("/submit", "POST", _submit_handler),
    ],
    is_bound=_is_bound,
)
register(spec)
