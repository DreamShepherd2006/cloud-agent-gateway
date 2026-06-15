"""WeChat QR-based channel binding.

Uses the official nanobot WeixinChannel for ilink API calls
(_fetch_qr_code, _api_get_with_base, _make_headers).

Registered via cloud_agent_gateway.channel_binding.register().
"""

from __future__ import annotations

import base64
import io
import json
import os

import httpx
import qrcode
from cloud_agent_gateway.adapters.nanobot_weixin import WeixinChannel, WeixinConfig
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from cloud_agent_gateway.channel_binding import (
    BindingSpec,
    nanobot_home,
    register,
    read_config_cloud,
    read_credential_cloud,
    write_config_cloud,
    write_credential_cloud,
)

# ══════════════════════════════════════════════════
# In-memory QR sessions
# ══════════════════════════════════════════════════

_pending: dict[str, dict] = {}

# ══════════════════════════════════════════════════
# State paths
# ══════════════════════════════════════════════════

def _weixin_state_dir() -> str:
    d = os.path.join(nanobot_home(), "weixin")
    os.makedirs(d, exist_ok=True)
    return d


# ══════════════════════════════════════════════════
# Shared WeixinChannel instance for ilink HTTP calls
# ══════════════════════════════════════════════════

_binding_channel: WeixinChannel | None = None


def _get_binding_channel() -> WeixinChannel:
    """获取或创建用于绑定的轻量 WeixinChannel 实例（复用官方的 ilink 调用）。"""
    global _binding_channel
    if _binding_channel is None:
        cfg = WeixinConfig(state_dir=_weixin_state_dir())
        _binding_channel = WeixinChannel(cfg, bus=None)  # type: ignore[arg-type]
        _binding_channel._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30, connect=30),
            follow_redirects=True,
        )
    return _binding_channel


# ══════════════════════════════════════════════════
# WeChat QR login（复用 WeixinChannel._fetch_qr_code）
# ══════════════════════════════════════════════════

async def _fetch_qr() -> dict:
    """获取微信登录二维码。返回 {qrcode_id, qrcode_img}。"""
    ch = _get_binding_channel()
    try:
        qrcode_id, qrcode_img_content = await ch._fetch_qr_code()
    except Exception as e:
        return {"error": f"获取微信二维码失败: {e}"}

    if not qrcode_id:
        return {"error": "微信 API 未返回二维码"}

    # ilink API changed: qrcode_img_content is now a liteapp URL, not base64.
    # Generate our own QR code from the URL when needed.
    if qrcode_img_content.startswith("http"):
        img = _url_to_base64_qr(qrcode_img_content)
    else:
        img = qrcode_img_content

    _pending[qrcode_id] = {
        "status": "waiting",
        "sender_id": "",
        "token": "",
        "base_url": ch.config.base_url,
    }
    return {"qrcode_id": qrcode_id, "qrcode_img": img}


def _url_to_base64_qr(url: str) -> str:
    """Generate a PNG QR code from a URL, return base64-encoded string."""
    qr_img = qrcode.make(url)
    buf = io.BytesIO()
    qr_img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


async def _check_status(qrcode_id: str) -> dict:
    """轮询微信扫码状态。"""
    bind = _pending.get(qrcode_id)
    if not bind:
        return {"status": "expired", "message": "二维码已过期"}

    ch = _get_binding_channel()
    try:
        data = await ch._api_get_with_base(
            base_url=bind.get("base_url", ch.config.base_url),
            endpoint="ilink/bot/get_qrcode_status",
            params={"qrcode": qrcode_id},
            auth=False,
        )
    except Exception as e:
        return {"status": "error", "message": str(e)}

    if not isinstance(data, dict):
        return {"status": "waiting", "message": "等待扫码..."}

    status = data.get("status", "wait")

    if status == "confirmed":
        token = data.get("bot_token", "")
        base_url = data.get("baseurl", "")
        if not token:
            return {"status": "error", "message": "未返回 token"}

        bind["status"] = "confirmed"
        bind["token"] = token

        # 写入 account.json（通过 PersistentStorageProtocol）
        # ⚠️ 必须清除旧的 context_tokens / typing_tickets / get_updates_buf，
        # 否则频道重启后用旧会话状态轮询 ilink 会立即 errcode -14（session expired）
        # 导致进入 60 分钟休眠，新 token 也无法使用。
        existing = read_credential_cloud("weixin")
        existing["token"] = token
        if base_url:
            existing["base_url"] = base_url
        existing["context_tokens"] = {}
        existing["typing_tickets"] = {}
        existing["get_updates_buf"] = ""
        write_credential_cloud("weixin", existing)

        return {"status": "confirmed", "message": "微信已绑定"}

    elif status in ("scaned", "scaned_but_redirect"):
        if status == "scaned_but_redirect":
            rh = str(data.get("redirect_host", "") or "").strip()
            if rh:
                if not (rh.startswith("http://") or rh.startswith("https://")):
                    rh = f"https://{rh}"
                bind["base_url"] = rh
        return {"status": "scanned", "message": "已扫码，等待确认..."}

    elif status == "expired":
        del _pending[qrcode_id]
        return {"status": "expired", "message": "二维码已过期"}

    return {"status": "waiting", "message": "等待扫码..."}


# ══════════════════════════════════════════════════
# Bound status check
# ══════════════════════════════════════════════════

def _is_bound() -> bool:
    """Check if weixin is already bound (account.json with token exists)."""
    try:
        data = read_credential_cloud("weixin")
        return bool(data.get("token"))
    except Exception:
        return False


# ══════════════════════════════════════════════════
# Route handlers (Request → Response)
# ══════════════════════════════════════════════════

async def _qr_handler(request: Request) -> Response:
    """Get WeChat QR code (public + internal)."""
    data = await _fetch_qr()
    return Response(json.dumps(data, ensure_ascii=False), media_type="application/json")


async def _status_handler(request: Request) -> Response:
    """Poll WeChat scan status (public + internal)."""
    qid = request.query_params.get("qrcode", "")
    if not qid:
        return Response(
            json.dumps({"error": "missing qrcode"}, ensure_ascii=False),
            media_type="application/json",
            status_code=400,
        )
    data = await _check_status(qid)
    return Response(json.dumps(data, ensure_ascii=False), media_type="application/json")


# ══════════════════════════════════════════════════
# Bind page HTML
# ══════════════════════════════════════════════════

WECHAT_BIND_PAGE = """\
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>绑定微信</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{display:flex;align-items:center;justify-content:center;min-height:100vh;
     font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     background:#0f0f0f;color:#e0e0e0}
.card{text-align:center;padding:2rem;max-width:400px}
h1{font-size:1.6rem;margin-bottom:.5rem;color:#fff}
p{color:#999;margin-bottom:1rem;font-size:.95rem}
.qr{max-width:240px;border-radius:12px;margin:1rem 0;border:3px solid #333}
.status{margin-top:1rem;color:#999;font-size:.9rem}
.status.success{color:#07c160}
.status.error{color:#ef4444}
.back{margin-top:1.5rem}
.back a{color:#666;font-size:.85rem;text-decoration:none}
.back a:hover{color:#999}
</style>
</head>
<body>
<div class="card">
<h1>🐱 绑定微信</h1>
<p>请用微信扫描下方二维码</p>
<div id="qr-container">⏳ 加载中...</div>
<div id="status" class="status"></div>
<div class="back"><a href="/">← 返回对话</a></div>
</div>
<script>
async function start(){
  try{
    let r=await fetch('/bind/wechat/qr');
    let d=await r.json();
    if(d.error){document.getElementById('qr-container').textContent='❌ '+d.error;return}
    let img=document.createElement('img');
    img.src='data:image/png;base64,'+d.qrcode_img;
    img.className='qr';
    document.getElementById('qr-container').innerHTML='';
    document.getElementById('qr-container').appendChild(img);
    let qid=d.qrcode_id,st=document.getElementById('status');
    let poll=setInterval(async()=>{
      let r2=await fetch('/bind/wechat/status?qrcode='+qid);
      let s=await r2.json();
      if(s.status==='scanned'){st.className='status';st.textContent='已扫码，请在手机上确认...'}
      else if(s.status==='confirmed'){st.className='status success';st.textContent='✅ 微信绑定成功！';clearInterval(poll)}
      else if(s.status==='expired'){st.className='status error';st.textContent='二维码已过期，请刷新页面';clearInterval(poll)}
    },2000)
  }catch(e){document.getElementById('qr-container').textContent='加载失败: '+e}
}
start();
</script>
</body>
</html>"""


# ══════════════════════════════════════════════════
# Registration
# ══════════════════════════════════════════════════

spec = BindingSpec(
    name="wechat",
    display="微信",
    icon="🐱",
    bind_page_html=WECHAT_BIND_PAGE,
    public_routes=[
        ("/qr", "GET", _qr_handler),
        ("/status", "GET", _status_handler),
    ],
    internal_routes=[
        ("/qr", "POST", _qr_handler),
        ("/status", "GET", _status_handler),
    ],
    is_bound=_is_bound,
)
register(spec)
