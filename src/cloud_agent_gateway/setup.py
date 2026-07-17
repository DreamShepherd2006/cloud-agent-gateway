#!/usr/bin/env python3
"""
Cloud Agent Gateway — 首次配置引导页 (setup.py)

通过 ``python3 -m cloud_agent_gateway.setup`` 启动。
在空白空间第一次启动时运行，提供配置表单，
用户填写后生成 config.json 并退出，容器重启后 CAG 接管。

平台自动检测（MS / HF），数据根目录：
  - ModelScope: /mnt/workspace
  - HF Spaces:   /data/instances/{space_id}

Provider 列表来自 nanobot 官方 ``providers/registry.py``，自动跟随上游更新。
"""

from __future__ import annotations

from datetime import datetime
import glob
import json
import os
import shutil
import sys
from typing import Any

# ── provider registry (from official nanobot) ───────────────────────
try:
    from nanobot.providers.registry import PROVIDERS as _NANOBOT_PROVIDERS, find_by_name
except ImportError:  # pragma: no cover — only fails when nanobot not installed
    _NANOBOT_PROVIDERS = ()
    def find_by_name(name: str) -> Any:  # noqa: E302
        return None

# ── UX augmentation (not in ProviderSpec) ───────────────────────────
# Suggested models and API-key creation URLs are CAG-specific UX helpers.
_PROVIDER_MODELS: dict[str, dict[str, Any]] = {
    "deepseek":    {"models": ["deepseek-chat", "deepseek-reasoner"], "default": "deepseek-chat"},
    "openai":      {"models": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "o4-mini"], "default": "gpt-4o-mini"},
    "siliconflow": {"models": ["deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-R1", "Qwen/Qwen3-235B-A22B"], "default": "deepseek-ai/DeepSeek-V3"},
    "zhipu":       {"models": ["glm-4-plus", "glm-4-flash", "glm-4-air"], "default": "glm-4-flash"},
    "dashscope":   {"models": ["qwen3-235b-a22b", "qwen-max", "qwen-plus"], "default": "qwen-plus"},
    "moonshot":    {"models": ["kimi-k2.5", "kimi-k2.6"], "default": "kimi-k2.5"},
    "gemini":      {"models": ["gemini-2.5-flash", "gemini-2.5-pro"], "default": "gemini-2.5-flash"},
    "mistral":     {"models": ["mistral-large-latest", "mistral-small-latest"], "default": "mistral-small-latest"},
    "anthropic":   {"models": ["claude-sonnet-4-20250514", "claude-haiku-3.5"], "default": "claude-haiku-3.5"},
    "volcengine":  {"models": ["deepseek-v3-250324", "deepseek-r1-250528"], "default": "deepseek-v3-250324"},
    "stepfun":     {"models": ["step-3"], "default": "step-3"},
    "minimax":     {"models": ["minimax-m1"], "default": "minimax-m1"},
    "qianfan":     {"models": ["ernie-4.5-8k", "ernie-speed-8k"], "default": "ernie-speed-8k"},
    "novita":      {"models": ["deepseek-r1", "deepseek-v3"], "default": "deepseek-r1"},
    "openrouter":  {"models": ["openai/gpt-4o-mini"], "default": "openai/gpt-4o-mini"},
    "aihubmix":    {"models": ["deepseek-chat"], "default": "deepseek-chat"},
    "skywork":     {"models": ["skywork-chat"], "default": "skywork-chat"},
    "groq":        {"models": ["llama-3.3-70b-versatile"], "default": "llama-3.3-70b-versatile"},
    "huggingface": {"models": ["Qwen/Qwen3-235B-A22B"], "default": "Qwen/Qwen3-235B-A22B"},
    "longcat":     {"models": ["longcat-chat"], "default": "longcat-chat"},
    "ant_ling":    {"models": ["ling-plus"], "default": "ling-plus"},
    "xiaomi_mimo": {"models": ["mimo-chat"], "default": "mimo-chat"},
    "byteplus":    {"models": ["deepseek-v3-250324"], "default": "deepseek-v3-250324"},
}

_PROVIDER_KEY_URLS: dict[str, str] = {
    "deepseek":    "https://platform.deepseek.com/api_keys",
    "openai":      "https://platform.openai.com/api-keys",
    "siliconflow": "https://cloud.siliconflow.cn/account/ak",
    "zhipu":       "https://open.bigmodel.cn/usercenter/apikeys",
    "dashscope":   "https://bailian.console.aliyun.com/?apiKey=1",
    "moonshot":    "https://platform.moonshot.cn/console/api-keys",
    "gemini":      "https://aistudio.google.com/apikey",
    "mistral":     "https://console.mistral.ai/api-keys/",
    "anthropic":   "https://console.anthropic.com/settings/keys",
    "volcengine":  "https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey",
    "stepfun":     "https://platform.stepfun.com/interface-key",
    "minimax":     "https://platform.minimax.io/user-center/basic-information/interface-key",
    "qianfan":     "https://console.bce.baidu.com/qianfan/ais/console/applicationConsole/application",
    "novita":      "https://novita.ai/dashboard/key",
    "openrouter":  "https://openrouter.ai/keys",
    "aihubmix":    "https://aihubmix.com/",
    "groq":        "https://console.groq.com/keys",
    "huggingface": "https://huggingface.co/settings/tokens",
}

# Providers that need more config than what setup form offers.
# is_oauth / is_local / is_direct (except "custom") are auto-skipped.
_SKIP_PROVIDERS = frozenset({"bedrock", "azure_openai", "ovms", "nvidia",
                               "openai_codex", "github_copilot",
                               "minimax_anthropic",
                               "volcengine_coding_plan", "byteplus_coding_plan"})


def _get_setup_providers() -> list[Any]:
    """Return nanobot ProviderSpec list filtered for setup form.

    Skips: OAuth-only, local-only, and providers needing special config.
    """
    result: list[Any] = []
    for spec in _NANOBOT_PROVIDERS:
        if spec.is_oauth or spec.is_local:
            continue
        if spec.name in _SKIP_PROVIDERS:
            continue
        # custom is included (is_direct=True but always shown)
        result.append(spec)
    return result


def _provider_for_form(provider_key: str) -> Any:
    """Look up a provider. Returns None if unknown."""
    if provider_key == "custom":
        # custom always accepted — no registry spec needed
        return type("Spec", (), {"name": "custom", "default_api_base": ""})()
    return find_by_name(provider_key)

# ── platform detection ──────────────────────────────────────────────
def _detect_data_root() -> str:
    """Determine the persistent data root for the current platform."""
    if os.environ.get("MODELSCOPE_ENVIRONMENT") == "studio":
        return "/mnt/workspace"
    # HF Spaces, Docker, unknown — all /data
    return "/data"


def _is_hf_space() -> bool:
    """True if running on HuggingFace Spaces (hf_oauth:true may inject env vars)."""
    return os.environ.get("HF_SPACE") == "1" or bool(os.environ.get("SPACE_ID"))


# ── config builder ───────────────────────────────────────────────────

def _build_squad_peers() -> dict:
    """Return initial peer definition for Legion mode — only neo (Commander).

    Additional worker agents are added later via /config/agents.
    """
    return {
        "neo": {"id": "squad:commander", "gateway_port": 18790, "ws_port": 18791},
    }


def _detect_deploy_platform() -> str:
    """Detect deploy_platform for squad_config.json."""
    if os.environ.get("MODELSCOPE_ENVIRONMENT") == "studio":
        return "modelscope-squad"
    return "hf-staging"


def _build_config(form: dict[str, str]) -> dict:
    """Build a minimal CAG config.json from user form input."""
    provider_key = form["provider"]
    spec = _provider_for_form(provider_key)
    api_base = form.get("api_base", "").strip()
    if not api_base and spec is not None and getattr(spec, "default_api_base", ""):
        api_base = spec.default_api_base

    models_data = _PROVIDER_MODELS.get(provider_key, {})
    model = form.get("model", "").strip() or models_data.get("default", "")

    config: dict[str, object] = {
        "gateway": {"host": "0.0.0.0", "port": 17860},
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
            "websocket": {"enabled": True, "port": 7870, "host": "127.0.0.1",
                          "token": "", "websocket_requires_token": False},
            "weixin": {"enabled": True, "allow_from": ["*"], "token": "",
                       "state_dir": "/home/nanobot/.nanobot/weixin"},
            "feishu": {"enabled": True, "app_id": "", "app_secret": "", "allow_from": ["*"]},
            "dingtalk": {"enabled": True, "client_id": "", "client_secret": "", "allow_from": ["*"]},
            "qq": {"enabled": True, "app_id": "", "secret": "", "allow_from": ["*"]},
        },
        "tools": {
            "ssrf_whitelist": ["127.0.0.1/32", "::1/128"],
            "exec": {"enabled": True, "allowed_env_keys": []},
            "web": {"enabled": True},
        },
    }

    oauth_cfg = _build_oauth(form)
    return config, oauth_cfg


def _build_oauth(form: dict[str, str]) -> dict[str, str]:
    """Build oauth.json dict from form + auto-detect (HF Spaces)."""
    if _is_hf_space():
        env_id = os.environ.get("OAUTH_CLIENT_ID", "").strip()
        env_secret = os.environ.get("OAUTH_CLIENT_SECRET", "").strip()
        if env_id and env_secret:
            return {"client_id": env_id, "client_secret": env_secret}
    client_id = form.get("oauth_client_id", "").strip()
    client_secret = form.get("oauth_client_secret", "").strip()
    if client_id and client_secret:
        return {"client_id": client_id, "client_secret": client_secret}
    return {}


def _build_legion_config(form: dict[str, str]) -> tuple[dict, dict, dict]:
    """Build squad_config.json, neo's config.json, and oauth.json for Legion mode."""
    data_root = _detect_data_root()
    deploy_platform = _detect_deploy_platform()
    commander_user = form.get("commander_user", "").strip()

    # squad_config.json
    squad_config = {
        "deploy_platform": deploy_platform,
        "data_root": data_root,
        "webui_agent": "neo",
        "commander_whitelist": [commander_user] if commander_user else [],
        "user_agent_map": {},
        "relay_timeout": 120,
        "gatekeeper_port": 7860,
        "dlq_dir": os.path.join(data_root, "dlq"),
        "peers": _build_squad_peers(),
    }

    # neo's config.json — provider from official registry
    provider_key = form["provider"]
    spec = _provider_for_form(provider_key)
    api_base = form.get("api_base", "").strip()
    if not api_base and spec is not None and getattr(spec, "default_api_base", ""):
        api_base = spec.default_api_base

    models_data = _PROVIDER_MODELS.get(provider_key, {})
    model = form.get("model", "").strip() or models_data.get("default", "")
    api_key = form.get("api_key", "").strip()

    neo_config: dict[str, object] = {
        "gateway": {"host": "127.0.0.1", "port": 0},
        "agents": {
            "defaults": {
                "instructions": "I am nanobot — a helpful AI assistant.",
                "workspace": "./workspace",
                "model": model,
                "provider": provider_key,
                "max_tokens": 8192,
                "temperature": 0.7,
            }
        },
        "providers": {
            provider_key: {
                "api_key": api_key if api_key else "",
                "api_base": api_base,
            }
        },
        "channels": {
            "websocket": {"enabled": True, "port": 0},
            "weixin": {"enabled": True, "allow_from": ["*"]},
            "feishu": {"enabled": True, "allow_from": ["*"]},
            "dingtalk": {"enabled": True, "allow_from": ["*"]},
            "qq": {"enabled": True, "allow_from": ["*"]},
        },
    }

    oauth_cfg = _build_oauth(form)
    return squad_config, neo_config, oauth_cfg


def _has_removed_agents(data_root: str) -> bool:
    """Check if there are archived (.removed.*) agent directories."""
    legion_instances = os.path.join(data_root, "legion", "instances")
    if not os.path.isdir(legion_instances):
        return False
    for entry in os.listdir(legion_instances):
        if ".removed." in entry and os.path.isdir(os.path.join(legion_instances, entry)):
            return True
    return False


def _backup_legion_config(data_root: str) -> None:
    """Backup squad_config.json and archive active agent directories.

    When switching from multi-agent to single-agent mode:
    1. Backup squad_config.json as .{ts}.bak (preserve all peer zones as-is)
    2. Archive active agent dirs (zone != "archived", including missing zone)
       as .removed.{ts} — already-archived .removed.* dirs stay unchanged
    3. Handle Errno 21 if squad_config.json path is a directory
    4. Delete squad_config.json to trigger single-agent mode on restart
    """
    legion_instances = os.path.join(data_root, "legion", "instances")
    squad_path = os.path.join(data_root, "legion", "squad_config.json")

    if not os.path.isdir(legion_instances):
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 1. Backup squad_config.json (preserve all zones — do not modify)
    peers: dict[str, dict] = {}
    if os.path.isfile(squad_path):
        try:
            with open(squad_path, encoding="utf-8") as f:
                squad_cfg = json.load(f)
            peers = squad_cfg.get("peers", {})
            backup_path = f"{squad_path}.{timestamp}.bak"
            with open(backup_path, "w", encoding="utf-8") as f:
                json.dump(squad_cfg, f, indent=2, ensure_ascii=False)
            print(f"[setup] 📋 已备份 squad_config.json → {os.path.basename(backup_path)}", flush=True)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[setup] ⚠️ 备份 squad_config.json 失败: {e}", flush=True)

    # 2. Archive only active agent directories
    #    (zone != "archived" or missing zone → active)
    count = 0
    for entry in sorted(os.listdir(legion_instances)):
        entry_path = os.path.join(legion_instances, entry)
        if not os.path.isdir(entry_path):
            continue
        # Skip already-archived or non-agent (no config.json)
        if ".removed." in entry:
            continue
        if entry.startswith("."):
            continue
        if not os.path.isfile(os.path.join(entry_path, "config.json")):
            continue
        # Check zone: skip archived agents
        peer = peers.get(entry, {}) if isinstance(peers, dict) else {}
        zone = peer.get("zone", "") if isinstance(peer, dict) else ""
        if zone == "archived":
            continue

        new_name = f"{entry}.removed.{timestamp}"
        new_path = os.path.join(legion_instances, new_name)
        shutil.move(entry_path, new_path)
        print(f"[setup] 📦 已归档 {entry} → {new_name}", flush=True)
        count += 1

    print(f"[setup] 📦 共归档 {count} 个活跃 agent", flush=True)

    # 3. Handle Errno 21: squad_config.json exists as directory
    if os.path.isdir(squad_path):
        shutil.rmtree(squad_path)
        print(f"[setup] ⚠️ squad_config.json 为目录，已移除", flush=True)

    # 4. Delete squad_config.json → single-agent mode
    if os.path.isfile(squad_path):
        os.remove(squad_path)
        print(f"[setup] 🗑️ 已删除 squad_config.json（进入单 agent 模式）", flush=True)


def _update_neo_config(data_root: str, form: dict) -> None:
    """Update neo's config.json with form's provider/model/api_key."""
    provider_key = form["provider"]
    spec = _provider_for_form(provider_key)
    api_base = form.get("api_base", "").strip()
    if not api_base and spec is not None and getattr(spec, "default_api_base", ""):
        api_base = spec.default_api_base

    models_data = _PROVIDER_MODELS.get(provider_key, {})
    model = form.get("model", "").strip() or models_data.get("default", "")
    api_key = form.get("api_key", "").strip()

    neo_cfg_path = os.path.join(data_root, "legion", "instances", "neo", "config.json")
    if os.path.isfile(neo_cfg_path):
        with open(neo_cfg_path) as f:
            neo_cfg = json.load(f)
    else:
        neo_cfg = {}

    neo_cfg.setdefault("agents", {}).setdefault("defaults", {})["model"] = model
    neo_cfg["agents"]["defaults"]["provider"] = provider_key
    neo_cfg.setdefault("providers", {})[provider_key] = {
        "api_key": api_key,
        "api_base": api_base,
    }

    # Ensure social channels are present and enabled for commander (align with single-agent mode)
    for ch_name in ("weixin", "feishu", "dingtalk", "qq"):
        neo_cfg.setdefault("channels", {}).setdefault(ch_name, {"allow_from": ["*"]})
        neo_cfg["channels"][ch_name]["enabled"] = True

    os.makedirs(os.path.dirname(neo_cfg_path), exist_ok=True)
    with open(neo_cfg_path, "w", encoding="utf-8") as f:
        json.dump(neo_cfg, f, indent=2, ensure_ascii=False)
    print(f"[setup] 🔄 已更新 neo config.json (provider={provider_key})", flush=True)
    # Diagnostic: show channel enabled state
    for ch_name in ("weixin", "feishu", "dingtalk", "qq", "websocket"):
        ch = neo_cfg.get("channels", {}).get(ch_name, {})
        print(f"[setup]    📡 {ch_name}: enabled={ch.get('enabled')}", flush=True)


def _restore_legion_config(data_root: str, form: dict) -> tuple[dict, dict | None, dict]:
    """Restore Legion config from backup, or fresh start if no backup found.

    1. Find latest squad_config.json.*.bak
    2. Filter peers to zone != "archived" (missing zone → active)
    3. Rename matching .removed.* dirs back to agent names
    4. Return restored squad_config; fall back to neo-only fresh start if
       no backup or zero active agents remain
    """
    legion_instances = os.path.join(data_root, "legion", "instances")

    # Find latest backup
    backups = sorted(glob.glob(os.path.join(data_root, "legion", "squad_config.json.*.bak")))
    if not backups:
        print("[setup] ℹ️ 未找到备份，全新创建 Legion 配置", flush=True)
        squad_config, neo_config, oauth_cfg = _build_legion_config(form)
        _migrate_legacy_instances(data_root)
        _cleanup_stale_removed(data_root, squad_config)
        return squad_config, neo_config, oauth_cfg

    # Load latest backup
    backup_path = backups[-1]
    with open(backup_path, encoding="utf-8") as f:
        old_cfg = json.load(f)
    print(f"[setup] 📋 从备份恢复: {os.path.basename(backup_path)}", flush=True)

    # Filter peers: zone != "archived" (missing zone → active)
    old_peers = old_cfg.get("peers", {})
    active_peers: dict[str, dict] = {}
    for name, info in old_peers.items():
        if not isinstance(info, dict):
            continue
        zone = info.get("zone", "")
        if zone == "archived":
            continue
        # Preserve original ports; ensure zone is set
        active_peers[name] = {
            **info,
            "zone": zone or "active",
        }

    if not active_peers:
        print("[setup] ⚠️ 备份中无活跃 agent，全新创建", flush=True)
        squad_config, neo_config, oauth_cfg = _build_legion_config(form)
        _migrate_legacy_instances(data_root)
        _cleanup_stale_removed(data_root, squad_config)
        return squad_config, neo_config, oauth_cfg

    # Restore agent dirs from .removed.*: rename back for each active peer
    restored_names: set[str] = set()
    if os.path.isdir(legion_instances):
        for entry in sorted(os.listdir(legion_instances)):
            if ".removed." not in entry:
                continue
            orig_name = entry.split(".removed.", 1)[0]
            if orig_name not in active_peers:
                continue
            src = os.path.join(legion_instances, entry)
            dst = os.path.join(legion_instances, orig_name)
            if not os.path.isdir(src):
                continue
            # Handle target collision: remove existing junk (directory or file)
            if os.path.lexists(dst):
                if os.path.isdir(dst):
                    shutil.rmtree(dst)
                else:
                    os.remove(dst)
            shutil.move(src, dst)
            print(f"[setup] 🔄 已恢复: {entry} → {orig_name}", flush=True)
            restored_names.add(orig_name)

    _migrate_legacy_instances(data_root)

    # Build squad_config from restored peers
    deploy_platform = _detect_deploy_platform()
    commander_user = form.get("commander_user", "").strip()

    squad_config: dict = {
        "deploy_platform": deploy_platform,
        "data_root": data_root,
        "webui_agent": "neo",
        "commander_whitelist": [commander_user] if commander_user else old_cfg.get("commander_whitelist", []),
        "user_agent_map": old_cfg.get("user_agent_map", {}),
        "relay_timeout": old_cfg.get("relay_timeout", 120),
        "gatekeeper_port": old_cfg.get("gatekeeper_port", 7860),
        "dlq_dir": os.path.join(data_root, "dlq"),
        "resurrection_whitelist": old_cfg.get("resurrection_whitelist", ["neo"]),
        "peers": active_peers,
    }

    # Neo config: if restored from backup, update existing; otherwise generate fresh
    neo_config = None
    if "neo" in restored_names:
        _update_neo_config(data_root, form)
    else:
        # Neo was not archived — generate fresh config and ensure neo in peers
        _, neo_cfg, _ = _build_legion_config(form)
        neo_config = neo_cfg
        # Add neo to active_peers if missing
        if "neo" not in active_peers:
            active_peers["neo"] = _build_squad_peers().get("neo", {
                "id": "squad:commander", "gateway_port": 18790, "ws_port": 18791
            })

    oauth_cfg = _build_oauth(form)
    print(f"[setup] ✅ 已从备份恢复 {len(active_peers)} 个活跃 agent: {list(active_peers.keys())}", flush=True)
    return squad_config, neo_config, oauth_cfg


def _cleanup_stale_removed(data_root: str, squad_config: dict) -> None:
    """Restore .removed.* dirs for agents now in the roster (rename back).

    No longer deletes — the legacy delete behavior lost data during mode switches.
    """
    legion_instances = os.path.join(data_root, "legion", "instances")
    if not os.path.isdir(legion_instances):
        return

    roster = set(squad_config.get("peers", {}).keys())
    for entry in sorted(os.listdir(legion_instances)):
        if ".removed." not in entry:
            continue
        base_name = entry.split(".removed.", 1)[0]
        if base_name not in roster:
            continue
        src = os.path.join(legion_instances, entry)
        dst = os.path.join(legion_instances, base_name)
        if not os.path.isdir(src):
            continue
        if os.path.lexists(dst):
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            else:
                os.remove(dst)
        shutil.move(src, dst)
        print(f"[setup] 🔄 已恢复归档: {entry} → {base_name}", flush=True)


def _build_provider_form_data() -> tuple[str, str, str]:
    """Generate dynamic HTML/JS provider data from nanobot official registry.

    Returns (provider_options_html, presets_js, key_urls_js).
    """
    select_lines = ['      <select id="provider" name="provider">']
    p_entries: list[str] = []
    k_entries: list[str] = []

    for spec in _get_setup_providers():
        select_lines.append(f'        <option value="{spec.name}">{spec.label}</option>')

        models_data = _PROVIDER_MODELS.get(spec.name, {})
        models = models_data.get("models", [])
        default_m = models_data.get("default", models[0] if models else "")
        base = spec.default_api_base or ""

        p_entries.append(
            f'  {spec.name}:{{base:"{base}",ml:{json.dumps(models)},dm:"{default_m}"}}'
        )
        k_entries.append(
            f'  {spec.name}:"{_PROVIDER_KEY_URLS.get(spec.name, "")}"'
        )

    # custom (not in nanobot registry — always append)
    select_lines.append('        <option value="custom">自定义 (OpenAI 兼容)</option>')
    p_entries.append('  custom:{base:"",ml:[],dm:""}')
    k_entries.append('  custom:""')

    select_lines.append('      </select>')

    options_html = "\n".join(select_lines)
    presets_js = "var P = {\n" + ",\n".join(p_entries) + "\n};"
    key_urls_js = "var KEY_URL = {\n" + ",\n".join(k_entries) + "\n};"

    return options_html, presets_js, key_urls_js


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
   .mode-switch { display:flex; gap:10px; margin-bottom:12px }
   .mode-option { flex:1; border:2px solid #ddd; border-radius:8px; padding:12px 14px;
     cursor:pointer; transition:border-color .15s; display:flex; flex-direction:column; gap:4px }
   .mode-option:has(input:checked) { border-color:#4a90d9; background:#f0f5ff }
   .mode-option input[type=radio] { position:absolute; opacity:0; width:0 }
   .mode-label { font-size:14px; font-weight:600 }
   .mode-desc { font-size:12px; color:#888 }
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
      <label>部署模式</label>
      <div class="mode-switch">
        <label class="mode-option">
          <input type="radio" name="deploy_mode" value="cloud" checked onchange="onModeChange()">
          <span class="mode-label">单用户模式</span>
          <span class="mode-desc">Cloud Native · 个人使用，一个 AI 助手</span>
        </label>
        <label class="mode-option">
          <input type="radio" name="deploy_mode" value="legion" onchange="onModeChange()">
          <span class="mode-label">多用户模式</span>
          <span class="mode-desc">Squad Legion · 多人协作，多个 Agent 分工</span>
        </label>
      </div>

       <div id="legion-fields" class="hidden">
        <label for="commander_user">管理员用户名</label>
        <input id="commander_user" name="commander_user" type="text"
               placeholder="你的 OAuth 登录用户名">
        <p class="tip">此用户拥有最高权限，可以管理所有 Agent。</p>
        <label id="fresh-start-label" style="margin-top:10px;display:none">
          <input type="checkbox" name="fresh_start" value="true" id="fresh_start" style="width:auto">
          <span style="font-weight:normal">全新开始（忽略已有配置）</span>
        </label>
      </div>

      <label for="provider">服务商</label>
{PROVIDER_OPTIONS}

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
         <p class="step-num">① 空间地址 → 填到「应用官网」</p>
         <div class="copy-box">
           <code id="space-url">检测中...</code>
           <button type="button" id="copy-space-btn" onclick="copySpaceUrl()">📋 复制</button>
         </div>

         <p class="step-num">② 回调地址 → 填到「重定向URL」</p>
         <div class="copy-box">
           <code id="redirect-url">检测中...</code>
           <button type="button" id="copy-btn" onclick="copyRedirect()">📋 复制</button>
         </div>

         <p class="step-num">③ 创建 OAuth 应用</p>
        <p class="tip" id="oauth-link-ms">
          👉 打开
          <a href="https://modelscope.cn/my/createApplications?status=create" target="_blank">ModelScope 创建 OAuth 应用</a>
          ，填写：
          <ul style="font-size:0.85rem;margin:4px 0 0 1em;padding:0">
            <li><b>应用名称</b>：任意（如 我的AI助手）</li>
             <li><b>应用官网</b>：粘贴 <b>① 空间地址</b></li>
             <li><b>授权范围</b>：勾选 <code>profile</code>（用户公开信息）+ <code>read-repos</code>（读取个人仓库）</li>
             <li><b>重定向URL</b>：粘贴 <b>② 回调地址</b></li>
          </ul>
          → 创建后获取 App ID / App Secret，填回下方
        </p>
        <p class="tip" id="oauth-link-hf" style="display:none">
          👉 打开
          <a href="https://huggingface.co/settings/applications/new" target="_blank">HuggingFace OAuth 应用</a>
           → 粘贴 <b>② 回调地址</b> → 获取 Client ID / Client Secret
        </p>

         <p class="step-num">④ 填回下方</p>
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
// -- provider presets (generated from nanobot official registry) --
{PROVIDER_PRESETS_JS}
// -- provider → API Key 创建链接 --
{PROVIDER_KEY_URLS_JS}

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

// 检测已有 Legion 备份 → 多用户模式下显示"全新开始"复选框
var LEGION_BACKUP_EXISTS = {LEGION_BACKUP_EXISTS};
if (LEGION_BACKUP_EXISTS) {
  document.getElementById('fresh-start-label').style.display = '';
}

// 平台检测：有 ms.show 域名 → ModelScope，否则 HuggingFace
document.getElementById('oauth-link-ms').style.display = isMS ? '' : 'none';
document.getElementById('oauth-link-hf').style.display = isMS ? 'none' : '';

function onModeChange() {
  var mode = document.querySelector('input[name=deploy_mode]:checked').value;
  document.getElementById('legion-fields').classList.toggle('hidden', mode !== 'legion');
  // 更新回调地址：多 agent 使用 /api/squad/auth/callback
  if (mode === 'legion') {
    redirectUrl = spaceUrl + '/api/squad/auth/callback';
  } else {
    redirectUrl = spaceUrl + (isMS ? '/api/auth/callback' : '/auth/callback');
  }
  redirectEl.textContent = redirectUrl;
}

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
    # Compute OAuth auto-detect: only on HF Spaces with valid OAUTH_CLIENT_ID
    hf_oauth_auto = "false"
    if _is_hf_space():
        env_id = os.environ.get("OAUTH_CLIENT_ID", "").strip()
        env_secret = os.environ.get("OAUTH_CLIENT_SECRET", "").strip()
        if env_id and env_secret:
            hf_oauth_auto = "true"
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

    # Generate dynamic provider data from nanobot official registry
    provider_opts, presets_js, key_urls_js = _build_provider_form_data()

    # Detect archived agents for "fresh start" checkbox visibility
    has_archived = _has_removed_agents(DATA_ROOT)

    html = (SETUP_HTML
            .replace("{PROVIDER_OPTIONS}", provider_opts)
            .replace("{PROVIDER_PRESETS_JS}", presets_js)
            .replace("{PROVIDER_KEY_URLS_JS}", key_urls_js)
            .replace("{PREFILL_JS}", prefill_js)
            .replace("{HF_OAUTH_AUTO}", hf_oauth_auto)
             .replace("{LEGION_BACKUP_EXISTS}", "true" if has_archived else "false"))
    return HTMLResponse(html)


async def post_setup(request: Request) -> JSONResponse:
    form = await request.json()
    deploy_mode = form.get("deploy_mode", "cloud")

    required = ["provider", "api_key"]
    if deploy_mode == "legion":
        required.append("commander_user")
    missing = [k for k in required if not form.get(k, "").strip()]
    if missing:
        return JSONResponse({"ok": False, "error": f"缺少必填项: {', '.join(missing)}"}, status_code=400)

    if _provider_for_form(form["provider"]) is None:
        return JSONResponse({"ok": False, "error": f"未知服务商: {form['provider']}"}, status_code=400)

    try:
        if deploy_mode == "legion":
            squad_config, neo_config, oauth_cfg = _restore_legion_config(DATA_ROOT, form)

            # Write squad_config.json
            squad_path = os.path.join(DATA_ROOT, "legion", "squad_config.json")
            os.makedirs(os.path.dirname(squad_path), exist_ok=True)
            # Handle Errno 21: path exists as directory
            if os.path.isdir(squad_path):
                shutil.rmtree(squad_path)
                print(f"[setup] ⚠️ squad_config.json 为目录，已移除", flush=True)
            with open(squad_path, "w", encoding="utf-8") as f:
                json.dump(squad_config, f, indent=2, ensure_ascii=False)
            print(f"[setup] ✅ squad_config.json 已写入: {json.dumps(list(squad_config.keys()))}", flush=True)

            # Write neo's config.json only for fresh start (restore already wrote it)
            if neo_config:
                neo_cfg_path = os.path.join(DATA_ROOT, "legion", "instances", "neo", "config.json")
                os.makedirs(os.path.dirname(neo_cfg_path), exist_ok=True)
                with open(neo_cfg_path, "w", encoding="utf-8") as f:
                    json.dump(neo_config, f, indent=2, ensure_ascii=False)
                print(f"[setup] ✅ neo config.json 已写入: {neo_cfg_path}", flush=True)
        else:
            # 备份 Legion 配置（从多用户模式切换到单用户模式）
            _backup_legion_config(DATA_ROOT)

            # 清理旧 Legion 残留
            for legacy in [
                os.path.join(DATA_ROOT, "squad_config.json"),
                os.path.join(DATA_ROOT, "legion", "squad_config.json"),
            ]:
                if os.path.exists(legacy):
                    os.remove(legacy)
                    print(f"[setup] 🧹 已清理旧的 squad_config.json ({legacy})", flush=True)

            config, oauth_cfg = _build_config(form)
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            print(f"[setup] ✅ config.json 已写入: {json.dumps(list(config.keys()))}", flush=True)
            # 回读验证
            with open(CONFIG_PATH, encoding="utf-8") as f:
                verify = json.load(f)
            print(f"[setup] 🔍 config.json 回读 keys: {json.dumps(list(verify.keys()))}", flush=True)
            assert "oauth" not in verify, "BUG: oauth leaked into config.json!"

        os.makedirs(DATA_ROOT, exist_ok=True)
        oauth_path = os.path.join(DATA_ROOT, "oauth.json")
        with open(oauth_path, "w", encoding="utf-8") as f:
            json.dump(oauth_cfg, f)
        print(f"[setup] ✅ oauth.json 已写入: {json.dumps(list(oauth_cfg.keys()))}", flush=True)
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


def _migrate_legacy_instances(data_root: str) -> None:
    """Copy old agent instances from DATA_ROOT/instances/ to DATA_ROOT/legion/instances/.

    Skips 'default' (single-agent), '_template', and 'logs'.
    Only copies agent dirs that don't already exist in the legion target.
    """
    legacy_instances = os.path.join(data_root, "instances")
    legion_instances = os.path.join(data_root, "legion", "instances")

    if not os.path.isdir(legacy_instances):
        return

    os.makedirs(legion_instances, exist_ok=True)

    # Only migrate dirs that look like nanobot agent instances (contain config.json)
    skip = {"default", "_template", "logs", ".git"}
    migrated = 0

    for name in sorted(os.listdir(legacy_instances)):
        if name in skip:
            continue
        src = os.path.join(legacy_instances, name)
        dst = os.path.join(legion_instances, name)
        if not os.path.isdir(src):
            continue
        if not os.path.isfile(os.path.join(src, "config.json")):
            continue  # not an agent instance, skip
        if os.path.exists(dst):
            continue  # already in legion, skip
        shutil.move(src, dst)
        print(f"[setup] 🔄 迁移旧 agent: {name} → legion/instances/{name}", flush=True)
        migrated += 1

    if migrated:
        print(f"[setup] ✅ 已迁移 {migrated} 个旧 agent 到 legion/", flush=True)


if __name__ == "__main__":
    main()

