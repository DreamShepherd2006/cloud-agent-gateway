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

    config: dict[str, object] = {
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

    # OAuth 配置：优先使用环境变量（HF hf_oauth:true 自动注入），
    # 否则从表单读取（ModelScope 用户手动填写）
    env_id = os.environ.get("OAUTH_CLIENT_ID", "").strip()
    env_secret = os.environ.get("OAUTH_CLIENT_SECRET", "").strip()
    if env_id and env_secret:
        oauth_cfg = {"client_id": env_id, "client_secret": env_secret}
    else:
        client_id = form.get("oauth_client_id", "").strip()
        client_secret = form.get("oauth_client_secret", "").strip()
        oauth_cfg = {"client_id": client_id, "client_secret": client_secret} if client_id and client_secret else {}

    return config, oauth_cfg


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
  .divider { border:none; border-top:1px solid #eee; margin:28px 0 20px }
  .section-title { font-size:16px; margin-bottom:8px }
  .step-num { font-size:13px; font-weight:600; color:#555; margin-top:18px; margin-bottom:6px }
  .copy-box { display:flex; gap:8px; align-items:stretch }
  .copy-box code { flex:1; padding:10px 12px; background:#f0f5ff; border:1px solid #c5d9f6;
                   border-radius:8px; font-size:13px; word-break:break-all; font-family:monospace; overflow-wrap:anywhere }
  .copy-box button { width:auto; margin:0; padding:8px 14px; font-size:13px; white-space:nowrap }
  #submit-msg { margin-top:12px; font-size:14px; color:#4a90d9; text-align:center }
  #submit-msg a { color:#4a90d9 }
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
      <p class="tip">密钥仅保存在你的空间里，不会上传。
        <span id="key-link"></span>
      </p>

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

       <hr class="divider">

       <h2 class="section-title">🔐 OAuth 登录</h2>
       <p class="sub" id="oauth-sub">配置后可通过 ModelScope / HuggingFace 账号登录，无需重复填 API Key。</p>
       <div id="oauth-auto-note" style="display:none;background:#e8f5e9;border:1px solid #a5d6a7;border-radius:8px;padding:12px 16px;font-size:13px;margin-bottom:4px">
         ✅ <b>HuggingFace OAuth 已自动配置</b>（README 中 <code>hf_oauth: true</code>）— 无需手动填 OAuth 凭证
       </div>

       <div id="oauth-section">
        <p class="step-num">① 复制你的空间地址 &amp; 回调地址</p>
        <div class="copy-box">
          <code id="space-url">检测中...</code>
          <button type="button" id="copy-space-btn" onclick="copySpaceUrl()">📋 复制</button>
        </div>
        <div class="copy-box">
          <code id="redirect-url">检测中...</code>
          <button type="button" id="copy-btn" onclick="copyRedirect()">📋 复制</button>
        </div>

        <p class="step-num">② 创建 OAuth 应用并粘贴</p>
        <p class="tip" id="oauth-link-ms">
          👉 打开
          <a href="https://modelscope.cn/my/createApplications?status=create" target="_blank">ModelScope 创建 OAuth 应用</a>
          ，填写：
          <ul style="font-size:0.85rem;margin:4px 0 0 1em;padding:0">
            <li><b>应用名称</b>：任意（如 我的AI助手）</li>
            <li><b>应用官网</b>：粘贴上面的空间地址</li>
            <li><b>授权范围</b>：勾选 <code>profile</code>（用户公开信息）+ <code>read-repos</code>（读取个人仓库）</li>
            <li><b>重定向URL</b>：粘贴上面的回调地址</li>
          </ul>
          → 创建后获取 App ID / App Secret，填回下方
        </p>
        <p class="tip" id="oauth-link-hf" style="display:none">
          👉 打开
          <a href="https://huggingface.co/settings/applications/new" target="_blank">HuggingFace OAuth 应用</a>
          → 粘贴上面的回调地址 → 获取 Client ID / Client Secret
        </p>

        <p class="step-num">③ 填回下方</p>
        <label for="oauth_client_id">App ID / Client ID</label>
        <input id="oauth_client_id" name="oauth_client_id" type="text"
               placeholder="留空可跳过，后续再配">

        <label for="oauth_client_secret">App Secret / Client Secret</label>
        <input id="oauth_client_secret" name="oauth_client_secret" type="password"
               autocomplete="off"
               placeholder="留空可跳过，后续再配">
      </div>

      <hr class="divider">

      <button type="submit">保存配置 →</button>
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

// provider → API Key 创建链接
var KEY_URL = {
  deepseek:"https://platform.deepseek.com/api_keys",
  openai:"https://platform.openai.com/api-keys",
  siliconflow:"https://cloud.siliconflow.cn/account/ak",
  zhipu:"https://open.bigmodel.cn/usercenter/apikeys",
  dashscope:"https://bailian.console.aliyun.com/?apiKey=1",
  custom:""
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
  // API Key 创建链接
  var url = KEY_URL[k];
  var kl = document.getElementById('key-link');
  kl.innerHTML = url ? '👉 <a href="'+url+'" target="_blank">获取 '+sel.options[sel.selectedIndex].text+' API Key</a>' : '';
}
sel.addEventListener('change', updateUI);
updateUI();

// -- OAuth: 自动检测平台并生成回调 URL --
var redirectEl = document.getElementById('redirect-url');
var host = window.location.host;
var isMS = host.indexOf('.ms.show') !== -1;
var spaceUrl = window.location.origin;
var redirectUrl = spaceUrl + (isMS ? '/api/auth/callback' : '/auth/callback');
redirectEl.textContent = redirectUrl;

var spaceEl = document.getElementById('space-url');
spaceEl.textContent = spaceUrl;

function copySpaceUrl() {
  navigator.clipboard.writeText(spaceUrl).then(function(){
    var btn = document.getElementById('copy-space-btn');
    btn.textContent = '✅ 已复制';
    setTimeout(function(){ btn.textContent = '📋 复制'; }, 2000);
  }).catch(function(){
    prompt('按 Ctrl+C 复制:', spaceUrl);
  });
}

// OAuth 自动配置（HF hf_oauth:true → OAUTH_CLIENT_ID env 已注入）
var HF_OAUTH_AUTO = {HF_OAUTH_AUTO};
if (HF_OAUTH_AUTO) {
  document.getElementById('oauth-section').style.display = 'none';
  document.getElementById('oauth-auto-note').style.display = '';
}

// 平台检测：有 ms.show 域名 → ModelScope，否则 HuggingFace
document.getElementById('oauth-link-ms').style.display = isMS ? '' : 'none';
document.getElementById('oauth-link-hf').style.display = isMS ? 'none' : '';

function copyRedirect() {
  navigator.clipboard.writeText(redirectUrl).then(function(){
    var btn = document.getElementById('copy-btn');
    btn.textContent = '✅ 已复制';
    setTimeout(function(){ btn.textContent = '📋 复制'; }, 2000);
  }).catch(function(){
    prompt('按 Ctrl+C 复制:', redirectUrl);
  });
}

// form submit
var fm = document.getElementById('setup-form');
var msg = document.getElementById('submit-msg');
fm.addEventListener('submit', async function(e){
  e.preventDefault();
  msg.innerHTML = '正在保存配置...';
  var fd = new FormData(fm);
  var payload = {}; fd.forEach(function(v,k){ payload[k]=v; });
  var resp = await fetch('/', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  var data = await resp.json();
  if(data.ok) {
    msg.innerHTML = '\u2705 配置已保存！<br><br>'
      + '<b>下一步：重启空间</b><br>'
      + 'ModelScope：点「停止」→ 再点「启动」<br>'
      + 'HuggingFace：Factory Rebuild<br><br>'
      + '重启后访问空间 → OAuth 登录 → 即可使用 AI 助手。';
  } else {
    msg.innerHTML = '\u274c 保存失败: '+ (data.error||'未知错误');
  }
 });
{PREFILL_JS}
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
    """Return setup HTML with pre-filled values if config.json already exists."""
    prefill_js = ""
    try:
        if os.path.isfile(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            provider = (cfg.get("agents", {}).get("defaults", {}).get("provider") or "")
            model = (cfg.get("agents", {}).get("defaults", {}).get("model") or "")
            api_key = (cfg.get("providers", {}).get(provider, {}).get("api_key") or "")
            api_base = (cfg.get("providers", {}).get(provider, {}).get("api_base") or "")
            escape_js = lambda s: s.replace("\\", "\\\\").replace("'", "\\'")
            prefill_js = f"""
// 🔄 检测到已有 config.json → 预填
document.getElementById('provider').value = '{escape_js(provider)}';
document.getElementById('model').value = '{escape_js(model)}';
document.getElementById('api_key').value = '{escape_js(api_key)}';
// OAuth 凭证不在 config.json 中，需要重新填写
console.log('[setup] pre-filled provider={provider} model={model} api_key_len={len(api_key)}');
"""
            print(f"[setup] 🔄 预填 config.json: provider={provider}, model={model}, api_key={'***' if api_key else '(空)'}", flush=True)
    except Exception as e:
        print(f"[setup] ⚠️ 预填失败（忽略）: {e}", flush=True)
    return HTMLResponse(SETUP_HTML.replace("{PREFILL_JS}", prefill_js))


async def post_setup(request: Request) -> JSONResponse:
    form = await request.json()

    required = ["provider", "api_key"]
    missing = [k for k in required if not form.get(k, "").strip()]
    if missing:
        return JSONResponse({"ok": False, "error": f"缺少必填项: {', '.join(missing)}"}, status_code=400)

    if form["provider"] not in PROVIDERS:
        return JSONResponse({"ok": False, "error": f"未知服务商: {form['provider']}"}, status_code=400)

    try:
        config, oauth_cfg = _build_config(form)
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        os.makedirs(DATA_ROOT, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print(f"[setup] ✅ config.json 已写入: {json.dumps(list(config.keys()))}", flush=True)
        oauth_path = os.path.join(DATA_ROOT, "oauth.json")
        with open(oauth_path, "w", encoding="utf-8") as f:
            json.dump(oauth_cfg, f)
        print(f"[setup] ✅ oauth.json 已写入: {json.dumps(list(oauth_cfg.keys()))}", flush=True)
        # 回读验证
        with open(CONFIG_PATH, encoding="utf-8") as f:
            verify = json.load(f)
        print(f"[setup] 🔍 config.json 回读 keys: {json.dumps(list(verify.keys()))}", flush=True)
        assert "oauth" not in verify, "BUG: oauth leaked into config.json!"
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
