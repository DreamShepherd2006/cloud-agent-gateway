"""Manual config bindings for channels that require config.json editing.

These channels don't have interactive login/QR flows in the official nanobot source,
so they need manual credential configuration via config.json.

Registered via cloud_agent_gateway.channel_binding.register().
"""

from __future__ import annotations

import json
import os

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from cloud_agent_gateway.channel_binding import (
    BindingSpec,
    config_path,
    load_json,
    register,
)


# ══════════════════════════════════════════════════
# Generic manual config page
# ══════════════════════════════════════════════════

def _make_manual_page(name: str, display: str, icon: str) -> str:
    """生成手动配置说明页面."""
    return f"""\
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>配置 {display}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{display:flex;align-items:center;justify-content:center;min-height:100vh;
     font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     background:#0f0f0f;color:#e0e0e0}}
.card{{text-align:center;padding:2rem;max-width:440px}}
h1{{font-size:1.6rem;margin-bottom:.5rem;color:#fff}}
p{{color:#999;margin-bottom:1rem;font-size:.9rem}}
.info{{color:#ccc;font-size:.85rem;margin-bottom:1rem;text-align:left;
       background:#1a1a1a;border-radius:6px;padding:1rem;line-height:1.7}}
.info code{{color:#f59e0b;font-size:.85rem}}
.info pre{{background:#0a0a0a;padding:.8rem;border-radius:4px;overflow-x:auto;
           font-size:.8rem;color:#a0a0a0;margin:.5rem 0}}
.back{{margin-top:1.5rem;text-align:center}}
.back a{{color:#666;font-size:.85rem;text-decoration:none}}
.back a:hover{{color:#999}}
</style>
</head>
<body>
<div class="card">
<h1>{icon} 配置 {display}</h1>
<p>{display} 频道需手动编辑配置文件</p>
<div class="info">
<p>请在 <code>config.json</code> 的 <code>channels.{name}</code> 中添加配置：</p>
<pre>"channels": {{
  "{name}": {{
    "enabled": true,
    ...  // 填入 {display} 所需凭证
    "allow_from": ["*"]
  }}
}}</pre>
<p>配置完成后重启空间即可生效。</p>
<p>详细参数请参考 <a href="https://docs.nanobot.space/channels/{name}" style="color:#3b82f6">nanobot 频道文档</a></p>
</div>
<div class="back"><a href="/">← 返回对话</a></div>
</div>
</body>
</html>"""


# ══════════════════════════════════════════════════
# Channel definitions
# ══════════════════════════════════════════════════

_MANUAL_CHANNELS = [
    # (name, display, icon, key_field_for_is_bound)
    ("whatsapp", "WhatsApp", "📲", "bridge_token"),
    ("qq", "QQ", "🐧", "app_id"),
    ("wecom", "企业微信", "💼", "bot_id"),
    ("napcat", "Napcat (QQ)", "🐱", "ws_url"),
    ("mochat", "Mochat", "💬", "base_url"),
    ("msteams", "Microsoft Teams", "🏢", "app_id"),
    ("matrix", "Matrix", "🔗", "user_id"),
    ("signal", "Signal", "🔐", "number"),
    ("email", "Email", "📧", "imap_username"),
]


def _make_is_bound(ch_name: str, key_field: str):
    """Create is_bound function for a manual channel."""
    def _is_bound() -> bool:
        cfg = load_json(config_path())
        ch = cfg.get("channels", {}).get(ch_name, {})
        return ch.get("enabled", False) and bool(ch.get(key_field))
    return _is_bound


# ══════════════════════════════════════════════════
# Register all manual channels
# ══════════════════════════════════════════════════

for _name, _display, _icon, _key in _MANUAL_CHANNELS:
    _html = _make_manual_page(_name, _display, _icon)
    spec = BindingSpec(
        name=_name,
        display=_display,
        icon=_icon,
        bind_page_html=_html,
        public_routes=[],
        internal_routes=[],
        is_bound=_make_is_bound(_name, _key),
    )
    register(spec)
