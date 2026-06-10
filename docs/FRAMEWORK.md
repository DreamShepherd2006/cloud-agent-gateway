# Cloud Agent Gateway — Framework Overview

## Seven-Capability Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      Cloud Agent Gateway                        │
│                                                                 │
│  ① OAuth Engine    ② WS Proxy (+identity)  ③ Session Bridge     │
│  token-in-URL 绕过  sender_id 注入到       external→internal     │
│  header 剥离        JSON envelope          chat_id 映射          │
│                                                                 │
│  ④ Platform Adapter           ⑤ Capability Detection            │
│  HF / MS / Docker             上游提供了→退让, 没给→顶上          │
│                                                                 │
│  ⑥ Channel Binding            ⑦ Relay Engine                   │
│  WeChat / DingTalk / 可扩展    /api/squad/relay                  │
│  Protocol + 注册表 + 自动发现   单 agent (Cloud Demo)              │
│                               多 agent (nanobot-legion)           │
└─────────────────────────────────────────────────────────────────┘
```

| # | 能力 | 问题 | 解法 | 状态 |
|---|------|------|------|:---:|
| ① | OAuth Engine | 平台代理剥离 Authorization / Set-Cookie | token-in-URL → JS 自动附加到 fetch/WS | ✅ |
| ② | WS Proxy (+identity) | sender_id 在路由后丢失 | Gateway 中间人注入 sender_id/sender_name | ✅ |
| ③ | Session Bridge | 页面刷新丢历史 (new_chat 而非 attach) | external→internal chat_id 映射 | ✅ |
| ④ | Platform Adapter | HF / MS / Docker 差异 | PlatformProtocol + 各平台子类 | ✅ |
| ⑤ | Capability Detection | 上游功能各版本参差不齐 | 检测 → 退让/顶上 | ✅ |
| ⑥ | Channel Binding | 用户自助绑定社交账号 | BindingSpec 协议 + 自动发现 | ✅ |
| ⑦ | Relay Engine | Agent 间 / 外部消息路由 | 单 agent (oauth_proxy) + 多 agent (gatekeeper) | ✅ |

---

## Design Patterns

### Platform Abstraction (correct)

```
CloudPlatformProtocol (框架层，不改)  ←──  平台实现
PlatformSpec (注册表)                    ├─ hf_spaces.py  (平台=hf, 引擎=nanobot, squad=false)
  platform × engine × squad              ├─ hf_staging.py (平台=hf, 引擎=nanobot, squad=true)
  + matches() 多层过滤                   ├─ modelscope.py (平台=ms, 引擎=nanobot, squad=false)
                                         ├─ modelscope_squad.py (平台=ms, 引擎=nanobot, squad=true)
platform_setup.py (CLI: python3 -m)      └─ hf_direct.py  (平台=hf, 引擎=—,      squad=true)
  三维匹配 → 加载唯一匹配项

加新平台 → 一个文件 + 一行注册，框架零改动
```

### Channel Binding (was anti-pattern, now fixed)

**之前**：`channel_binding.py` + `oauth_proxy.py` 硬编码 → 加频道需要改约 6 处

**现在**：

```
cloud-agent-gateway/              deploy/cloud/channel_bindings/
  channel_binding.py                __init__.py          ← 注册入口
    ChannelBindingProtocol          wechat_binding.py     ← 微信绑定
    BindingSpec (注册表)             dingtalk_binding.py   ← 钉钉绑定
    discover()                                              未来 QQ...
  oauth_proxy.py
    自动发现 → 注册路由
```

加新频道 → 写一个文件 + 一行 import，框架层零改动。

---

## Two-Layer Architecture

| 层 | 仓库 | 说明 |
|----|------|------|
| **框架底层** | `cloud-agent-gateway` | platform abstraction, OAuth, Relay, Channel Binding. Framework-agnostic, pip-installable |
| **应用部署层** | `nanobot-legion` | Squad multi-agent system built on top of cloud-agent-gateway |

---

## Relay: Two Implementations

两个 relay 使用相同的 API 路径 `/api/squad/relay`，但实现完全不同：

| | Squad Relay (多智能体) | Cloud Demo Relay (单智能体) |
|---|---|---|
| **代码** | `gatekeeper.py` `_handle_relay()` | `oauth_proxy.py` `squad_relay()` |
| **路由** | sender→target + 权限白名单 | 无路由，token 匹配即放行 |
| **空间** | HF Staging, MS Nightly | HF Cloud Demo, MS Cloud Demo |
| **Token** | `SQUAD_RELAY_TOKEN_{PLATFORM}_{space_name}` | 同一命名规范 |

---

## Five Online Spaces

| Space | Platform | Layer |
|-------|----------|-------|
| Nightly | HF | nanobot-legion |
| HF Staging | HF | nanobot-legion |
| MS Staging | ModelScope | nanobot-legion |
| HF Cloud Demo | HF | cloud-agent-gateway (单 agent) |
| MS Cloud Demo | ModelScope | cloud-agent-gateway (单 agent) |

---

## Deployment Pipeline

```
本地开发 → HF Staging (自动 build) → MS Staging (手动 rebuild) → Nightly (cherry-pick)
              ↑ 框架验证                  ↑ 平台兼容                 ↑ 生产
```

---

## Upstream PR Plan

| # | PR | 状态 |
|---|-----|------|
| 1 | WebSocket identity injection (`sender_id`/`sender_name`) | 🔴 PR #4134 — 等 @chengyongru |
| 2 | Session recovery (`attach` hint → 刷新恢复会话) | 🔜 需先确认 `_dispatch_envelope` 有 `metadata` |
| 3 | Header stripping awareness (`proxy_header_blacklist`) | 🔜 和 #2 同批提 |

---

## Branch Rules

- `main` = stable production (Nightly), cherry-pick only from staging
- `staging` = validation frontier (HF Staging + MS Staging), tracks upstream nightly
- Cloud Demo spaces are NOT deployed from nanobot-legion branches — they use cloud-agent-gateway independently

---

*Last updated: 2026-06-05*
