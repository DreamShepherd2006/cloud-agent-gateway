"""binding_prompt.py — agent binding awareness injection

Provides prompt text and skill content that teaches the nanobot agent
how to guide users through channel binding (WeChat, DingTalk, Telegram,
Discord, Feishu, Slack). Other channels require manual config.

Usage in Cloud Demo entrypoint:
    from cloud_agent_gateway.binding_prompt import BINDING_SKILL_MD
    # Write to ~/.nanobot/skills/channel-binding/SKILL.md
    # Or inject into config.system_prompt

The skill is self-contained — the agent only needs to know the API endpoints
and the conversation flow. Internal security is handled by the HTTP routes
(localhost-only check in oauth_proxy.py).
"""

# ═══════════════════════════════════════════════════════════════
# Skill definition (Markdown for nanobot skill system)
# ═══════════════════════════════════════════════════════════════

BINDING_SKILL_MD = """---
name: channel-binding
description: Guide user to bind WeChat/DingTalk/Telegram/Discord/Feishu/Slack channels from Space WebUI.
always: true
---

# Channel Binding

Bind WeChat / DingTalk / Telegram / Discord / Feishu / Slack channels for this agent.
Other channels (WhatsApp, QQ, WeCom, NapCat, Mochat, MSTeams, Matrix, Signal, Email) require
manual config.json editing — direct users to the sidebar pinned chat for instructions.

## When to trigger

Check binding status on first interaction with each user, or when the user asks about channels.

## Check binding status

Send an internal HTTP GET:
```
GET http://127.0.0.1:7860/api/bind/status
```
Returns `{"wechat": {"bound": true/false}, "dingtalk": {"bound": true/false}, ...}`.

## Bind WeChat (QR code)

1. Tell user: "正在获取微信登录二维码..."
2. Call:
   ```
   POST http://127.0.0.1:7860/api/bind/wechat/qr
   ```
   Returns `{"qrcode_id": "...", "qrcode_img": "base64..."}`
3. Display the QR image to the user (decode base64 → PNG)
4. Tell user: "请用微信扫描二维码"
5. Poll every 2-3 seconds:
   ```
   GET http://127.0.0.1:7860/api/bind/wechat/status?qrcode=xxx
   ```
   Returns `{"status": "waiting|scanned|confirmed|expired", "message": "..."}`
6. On `"confirmed"`: tell user "微信绑定成功 ✅"
7. On `"expired"`: tell user "二维码已过期，重新获取..." → go to step 1

## Bind DingTalk (credentials)

1. Ask user: "请提供钉钉 AppKey 和 AppSecret"
2. Tell user: "正在验证凭证..."
3. Call:
   ```
   POST http://127.0.0.1:7860/api/bind/dingtalk/submit
   Content-Type: application/json
   {"client_id": "<AppKey>", "client_secret": "<AppSecret>"}
   ```
   Returns `{"ok": true, "message": "..."}` or `{"error": "..."}`
4. On success: tell user "钉钉绑定成功 ✅"
5. On failure: tell user the error and ask to retry

## Bind Telegram (bot token)

1. Ask user: "请提供 Telegram Bot Token（从 @BotFather 获取）"
2. Call:
   ```
   POST http://127.0.0.1:7860/api/bind/telegram/submit
   Content-Type: application/json
   {"token": "<bot_token>"}
   ```
3. On success: tell user "Telegram 已绑定 ✅"

## Bind Discord (bot token)

1. Ask user: "请提供 Discord Bot Token（从 Developer Portal 获取）"
2. Call:
   ```
   POST http://127.0.0.1:7860/api/bind/discord/submit
   Content-Type: application/json
   {"token": "<bot_token>"}
   ```
3. On success: tell user "Discord 已绑定 ✅"

## Bind Feishu (credentials)

1. Ask user: "请提供飞书 App ID 和 App Secret（从飞书开放平台获取）"
2. Call:
   ```
   POST http://127.0.0.1:7860/api/bind/feishu/submit
   Content-Type: application/json
   {"app_id": "<app_id>", "app_secret": "<app_secret>"}
   ```
3. On success: tell user "飞书已绑定 ✅"

## Bind Slack (bot + app token)

1. Ask user: "请提供 Slack Bot Token 和 App Token（从 Slack API 获取）"
2. Call:
   ```
   POST http://127.0.0.1:7860/api/bind/slack/submit
   Content-Type: application/json
   {"bot_token": "<bot_token>", "app_token": "<app_token>"}
   ```
3. On success: tell user "Slack 已绑定 ✅"

## First-interaction greeting

When a user first chats, check binding status silently. If any channel is unbound:

> 检测到你还未绑定社交通道。回复「绑定微信」、或打开左侧栏「社交通道配置」自行操作。

Only prompt once per user — don't nag after they decline or complete binding.

## Security

- All bind endpoints are localhost-only (no external access)
- WeChat QR data flows directly: ilink API → agent → user
- Credentials are validated against the respective API before saving
- No third-party data transmission
"""

# ═══════════════════════════════════════════════════════════════
# Minimal system prompt injection (optional, for agents without
# skill loading)
# ═══════════════════════════════════════════════════════════════

BINDING_SYSTEM_PROMPT = (
    "You can help users bind social channels (WeChat, DingTalk, Telegram, Discord, Feishu, Slack).\n\n"
    "To check binding status, silently call GET http://127.0.0.1:7860/api/bind/status "
    "on the first interaction with each user.\n"
    "If any channel is unbound, proactively offer help:\n"
    '  "检测到你还未绑定社交通道。回复「绑定微信」或打开左侧栏「社交通道配置」自行操作。"\n\n'
    "WeChat: POST /api/bind/wechat/qr → display QR → poll /api/bind/wechat/status?qrcode=xxx\n"
    "DingTalk: ask AppKey+AppSecret → POST /api/bind/dingtalk/submit\n"
    "Telegram: ask bot token → POST /api/bind/telegram/submit\n"
    "Discord: ask bot token → POST /api/bind/discord/submit\n"
    "Feishu: ask App ID+App Secret → POST /api/bind/feishu/submit\n"
    "Slack: ask Bot Token+App Token → POST /api/bind/slack/submit\n\n"
    "Other channels (WhatsApp, QQ, WeCom, etc.) require manual config.json editing — "
    "direct users to the sidebar pinned chat.\n"
    "All endpoints are at http://127.0.0.1:7860 (internal, no auth needed)."
)


def inject_binding_skill(skills_dir: str) -> str:
    """Write the channel-binding skill to a skills directory.

    Returns the path to the created SKILL.md.
    """
    import os
    skill_dir = os.path.join(skills_dir, "channel-binding")
    os.makedirs(skill_dir, exist_ok=True)
    skill_path = os.path.join(skill_dir, "SKILL.md")
    with open(skill_path, "w", encoding="utf-8") as f:
        f.write(BINDING_SKILL_MD)
    return skill_path
