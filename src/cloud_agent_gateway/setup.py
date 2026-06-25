#!/usr/bin/env python3
"""
Cloud Agent Gateway — 首次配置引导页 (setup.py)

通过 ``python3 -m cloud_agent_gateway.setup`` 启动。
在空白空间第一次启动时运行，提供配置表单，
用户填写后生成 config.json 并退出，容器重启后 CAG 接管。

平台自动检测（MS / HF），数据根目录：
  - ModelScope: /mnt/workspace
  - HF Spaces:   /data/instances/{space_id}
"""

from __future__ import annotations

import json
import os
import sys

# ── provider presets ────────────────────────────────────────────────
PROVIDERS = {
    "deepseek": {
        "label": "DeepSeek",
        "api_base": "https://api.deepseek.com",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "default_model": "deepseek-chat",
    },
    "openai": {
        "label": "OpenAI",
        "api_base": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "o4-mini"],
        "default_model": "gpt-4o-mini",
    },
    "siliconflow": {
        "label": "SiliconFlow (硅基流动)",
        "api_base": "https://api.siliconflow.cn/v1",
        "models": [
            "deepseek-ai/DeepSeek-V3",
            "deepseek-ai/DeepSeek-R1",
            "Qwen/Qwen3-235B-A22B",
        ],
        "default_model": "deepseek-ai/DeepSeek-V3",
    },
    "zhipu": {
        "label": "智谱AI (GLM)",
        "api_base": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4-plus", "glm-4-flash", "glm-4-air"],
        "default_model": "glm-4-flash",
    },
    "dashscope": {
        "label": "阿里云百炼 (Qwen)",
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen3-235b-a22b", "qwen-max", "qwen-plus"],
        "default_model": "qwen-plus",
    },
    "custom": {
        "label": "自定义 (OpenAI 兼容)",
        "api_base": "",
        "models": [],
        "default_model": "",
    },
}

# ── platform detection ──────────────────────────────────────────────
def _detect_data_root() -> str:
    """Determine the persistent data root for the current platform."""
    if os.environ.get("MODELSCOPE_ENVIRONMENT") == "studio":
        return "/mnt/workspace"
    if os.environ.get("HF_SPACE") == "1" or os.environ.get("SPACE_ID"):
        space_id = os.environ.get("SPACE_ID", "default")
        return f"/data/instances/{space_id}"
    # Docker / unknown fallback
    return "/data"


# ── config builder ───────────────────────────────────────────────────
def _build_config(form: dict[str, str]) -> dict:
    """Build a minimal CAG config.json from user form input."""
    provider_key = form["provider"]
    presets = PROVIDERS[provider_key]
    api_base = form.get("api_base", "").strip() or presets["api_base"]
    model = form.get("model", "").strip() or presets["default_model"]

    return {
        "gateway": {
            "host": "0.0.0.0",
            "port": 17860,
        },
        "agents": {
            "defaults": {
                "instructions": "You are a helpful AI assistant.",
                "model": model,
                "provider": provider_key,
                "max_tokens": 8192,
                "temperature": 0.7,
            },
        },
        "providers": {
            provider_key: {
                "api_key": form["api_key"].strip(),
                "api_base": api_base,
            },
        },
        "channels": {
            "websocket": {
                "enabled": True,
                "port": 7870,
                "host": "127.0.0.1",
                "token": "",
                "websocket_requires_token": False,
            },
            "weixin": {
                "enabled": True,
                "allow_from": ["*"],
                "token": "",
                "state_dir": "/home/nanobot/.nanobot/weixin",
            },
            "feishu": {
                "enabled": True,
                "app_id": "",
                "app_secret": "",
                "allow_from": ["*"],
            },
            "dingtalk": {
                "enabled": True,
                "client_id": "",
                "client_secret": "",
                "allow_from": ["*"],
            },
            "qq": {
                "enabled": True,
                "app_id": "",
                "secret": "",
                "allow_from": ["*"],
            },
        },
        "tools": {
            "ssrf_whitelist": ["127.0.0.1/32", "::1/128"],
            "exec": {"enabled": True, "allowed_env_keys": []},
            "web": {"enabled": True},
        },
    }


# ── HTML ─────────────────────────────────────────────────────────────
SETUP_HTML = """\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>⚙️ 初始化配置</title>
<style>
  * { box-sizing:border-box; margin:0; padding:0 }
  body { font-family:-apple-system,system-ui,Helvetica,Arial,sans-serif;
         background:#f5f5f5; color:#333; display:flex; min-height:100vh }
  .container { max-width:480px; margin:40px auto; padding:24px }
  .card { background:#fff; border-radius:12px; padding:32px; box-shadow:0 2px 12px rgba(0,0,0,.08) }
  h1 { font-size:22px; margin-bottom:4px }
  .sub { color:#888; font-size:14px; margin-bottom:24px }
  label { display:block; font-size:13px; font-weight:600; color:#555; margin-bottom:6px; margin-top:18px }
  select, input { width:100%; padding:10px 12px; border:1px solid #ddd; border-radius:8px;
                   font-size:15px; background:#fff; transition:border-color .15s }
  select:focus, input:focus { outline:none; border-color:#4a90d9; box-shadow:0 0 0 3px rgba(74,144,217,.1) }
  button { width:100%; padding:12px; margin-top:24px; background:#4a90d9; color:#fff;
           border:none; border-radius:8px; font-size:16px; font-weight:600; cursor:pointer;
           transition:background .15s }
  button:hover { background:#357abd }
  .tip { font-size:12px; color:#999; margin-top:6px }
  .hidden { display:none }
  #submit-msg { margin-top:12px; font-size:14px; color:#4a90d9; text-align:center }
</style>
</head>
<body>
<div class="container">
  <div class="card">
    <h1>🤖 配置你的 AI 助手</h1>
    <p class="sub">首次启动 · 填写 API 密钥后立即开始</p>

    <form id="setup-form">
      <label for="provider">服务商</label>
      <select id="provider" name="provider">
        <option value="deepseek">DeepSeek</option>
        <option value="openai">OpenAI</option>
        <option value="siliconflow">SiliconFlow · 硅基流动</option>
        <option value="zhipu">智谱AI (GLM)</option>
        <option value="dashscope">阿里云百炼 (Qwen)</option>
        <option value="custom">自定义 (OpenAI 兼容)</option>
      </select>

      <label for="api_key">API Key</label>
      <input id="api_key" name="api_key" type="password"
             placeholder="sk-xxxxxxxxxxxxxxxxxxxxxxxx"
             autocomplete="off" required>
      <p class="tip">密钥仅保存在你的空间里，不会上传。</p>

      <div id="custom-fields" class="hidden">
        <label for="api_base">API 地址</label>
        <input id="api_base" name="api_base" type="url"
               placeholder="https://api.example.com/v1">
      </div>

      <label for="model">模型</label>
      <input id="model" name="model" type="text"
             placeholder="留空使用默认模型" list="model-list">
      <datalist id="model-list"></datalist>
      <p class="tip">可输入自定义模型名，或从列表中选择。</p>

      <button type="submit">开始使用 →</button>
      <div id="submit-msg"></div>
    </form>
  </div>
</div>

<script>
// -- provider presets (inlined for no extra request) --
var P = {
  deepseek:{base:"https://api.deepseek.com",ml:["deepseek-chat","deepseek-reasoner"],dm:"deepseek-chat"},
  openai:{base:"https://api.openai.com/v1",ml:["gpt-4o","gpt-4o-mini","gpt-4.1","o4-mini"],dm:"gpt-4o-mini"},
  siliconflow:{base:"https://api.siliconflow.cn/v1",ml:["deepseek-ai/DeepSeek-V3","deepseek-ai/DeepSeek-R1","Qwen/Qwen3-235B-A22B"],dm:"deepseek-ai/DeepSeek-V3"},
  zhipu:{base:"https://open.bigmodel.cn/api/paas/v4",ml:["glm-4-plus","glm-4-flash","glm-4-air"],dm:"glm-4-flash"},
  dashscope:{base:"https://dashscope.aliyuncs.com/compatible-mode/v1",ml:["qwen3-235b-a22b","qwen-max","qwen-plus"],dm:"qwen-plus"},
  custom:{base:"",ml:[],dm:""}
};

var sel = document.getElementById('provider');
var mInput = document.getElementById('model');
var mList = document.getElementById('model-list');
var cf = document.getElementById('custom-fields');
var ab = document.getElementById('api_base');

function updateUI() {
  var k = sel.value, p = P[k];
  mList.innerHTML = '';
  p.ml.forEach(function(m){
    var o = document.createElement('option'); o.value = m; mList.appendChild(o);
  });
  mInput.placeholder = p.dm ? '默认: '+p.dm : '输入模型名';
  if(k==='custom') { cf.classList.remove('hidden'); ab.required=true; }
  else { cf.classList.add('hidden'); ab.required=false; ab.value = p.base; }
}
sel.addEventListener('change', updateUI);
updateUI();

// form submit
var fm = document.getElementById('setup-form');
var msg = document.getElementById('submit-msg');
fm.addEventListener('submit', async function(e){
  e.preventDefault();
  msg.textContent = '正在保存配置...';
  var fd = new FormData(fm);
  var payload = {}; fd.forEach(function(v,k){ payload[k]=v; });
  var resp = await fetch('/', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  var data = await resp.json();
  if(data.ok) {
    msg.textContent = '\u2705 配置已保存！空间即将重启，稍后刷新页面即可使用。';
  } else {
    msg.textContent = '\u274c 保存失败: '+ (data.error||'未知错误');
  }
});
</script>
</body>
</html>"""

DONE_HTML = """\
<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>配置完成</title>
<style>body{font-family:system-ui;display:flex;justify-content:center;align-items:center;
min-height:100vh;background:#f5f5f5;color:#333}
.card{background:#fff;padding:40px;border-radius:12px;text-align:center;box-shadow:0 2px 12px rgba(0,0,0,.08)}
h1{font-size:24px;margin-bottom:12px}p{color:#888}</style>
</head>
<body><div class="card">
<h1>&#x2705; 配置完成</h1>
<p>空间正在重启，稍后刷新页面即可使用 AI 助手。</p>
</div></body></html>"""


# ── app ───────────────────────────────────────────────────────────────
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

DATA_ROOT = _detect_data_root()
CONFIG_PATH = os.path.join(DATA_ROOT, "instances", "default", "config.json")


async def get_setup(request: Request) -> HTMLResponse:
    return HTMLResponse(SETUP_HTML)


async def post_setup(request: Request) -> JSONResponse:
    form = await request.json()

    required = ["provider", "api_key"]
    missing = [k for k in required if not form.get(k, "").strip()]
    if missing:
        return JSONResponse({"ok": False, "error": f"缺少必填项: {', '.join(missing)}"}, status_code=400)

    if form["provider"] not in PROVIDERS:
        return JSONResponse({"ok": False, "error": f"未知服务商: {form['provider']}"}, status_code=400)

    try:
        config = _build_config(form)
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        os.makedirs(DATA_ROOT, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    # Schedule exit after response — container will restart, CAG takes over
    import asyncio
    loop = asyncio.get_event_loop()
    loop.call_later(1.0, lambda: os._exit(0))

    return JSONResponse({"ok": True})


app = Starlette(
    debug=False,
    routes=[
        Route("/", get_setup, methods=["GET"]),
        Route("/", post_setup, methods=["POST"]),
    ],
)


def main() -> None:
    import uvicorn

    sys.stderr.write(f"[setup] 平台数据目录: {DATA_ROOT}\n")
    sys.stderr.write(f"[setup] 配置路径: {CONFIG_PATH}\n")
    sys.stderr.write("[setup] 启动配置引导页 → http://0.0.0.0:7860\n")

    uvicorn.run(app, host="0.0.0.0", port=7860, log_level="warning")


if __name__ == "__main__":
    main()
