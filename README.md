# cloud-agent-gateway

AI agent 云部署体系的**框架底层**——平台抽象、OAuth 认证、HTTP Relay 中继。

## 定位

`cloud-agent-gateway` 是一个 pip 包，位于部署栈的底层。它**不包含任何 agent 逻辑**——只负责平台探测、OAuth 回调、身份注入、Relay 中继等基础设施。

```
应用部署层  (nanobot-legion)     ← Squad 多智能体，依赖本包
       ▲ pip install
框架底层  (cloud-agent-gateway)   ← 本包：平台抽象 + OAuth + Relay
       │
       ├── 直接使用 → HF Cloud Demo · MS Cloud Demo    (单智能体快速体验)
       └── 作为依赖 → nanobot-legion                    (Squad 应用部署层)
```

框架无关：任何 agent 框架通过 `PlatformProtocol` 接口即可接入。

> 上游应用部署参考：[nanobot-legion](https://github.com/DreamShepherd2006/nanobot-legion) — 包含完整的五空间部署、Squad 多智能体、Gatekeeper、补丁体系。

## 在线空间

本包支撑五空间部署：

| 空间 | 平台 | 使用方式 | 链接 |
|------|------|----------|------|
| Nightly | HF Spaces | 通过 nanobot-legion | [DreamShepherd2006/nanobot-multi-agent-nightly](https://huggingface.co/spaces/DreamShepherd2006/nanobot-multi-agent-nightly) |
| HF Staging | HF Spaces | 通过 nanobot-legion | [DreamShepherd2006/Nanobot-Staging](https://huggingface.co/spaces/DreamShepherd2006/Nanobot-Staging) |
| MS Staging | ModelScope | 通过 nanobot-legion | [Stone2006/nanobot-multi-agent-nightly](https://www.modelscope.cn/studios/Stone2006/nanobot-multi-agent-nightly) |
| HF Cloud Demo | HF Spaces | 直接使用 | [DreamShepherd2006/nanobot-cloud-demo](https://huggingface.co/spaces/DreamShepherd2006/nanobot-cloud-demo) |
| MS Cloud Demo | ModelScope | 直接使用 | [DreamShepherd/ms-nanobot-cloud-demo](https://www.modelscope.cn/studios/DreamShepherd/ms-nanobot-cloud-demo) |

## 平台支持

| 平台 | 类 | OAuth | Squad Relay | 备注 |
|---|---|---|---|---|
| HF Staging | `HFStagingPlatform` | ✅ | ✅ | 完整 OAuth + WS 身份注入 |
| HF Direct | `HFDirectPlatform` | — | ✅ | 仅 relay，无 OAuth |
| HF Spaces | `HFSpacesPlatform` | ✅ | ✅ | HF OAuth via authlib |
| ModelScope | `ModelScopePlatform` | ✅ | ✅ | MS OAuth + 路由绕过 |
| ModelScope Squad | `ModelScopeSquadPlatform` | ✅ | ✅ | 内部 squad 变体 |

## 核心能力

### 1. 平台探测与抽象（`PlatformProtocol`）

```python
from cloud_agent_gateway.platforms import platform

platform.PLATFORM_NAME   # → "hf_spaces" | "modelscope" | ...
platform.is_hf            # → True / False
platform.can_oauth        # → True / False
platform.instance_path()  # → "/data/instances/neo" (平台感知路径)
```

所有平台差异（路径、环境变量、OAuth 流）封装在对应子类中，调用方无需 `if-else` 分支。

### 2. OAuth 代理

`OAuthProxy` 提供统一的认证流程：

```python
from cloud_agent_gateway.oauth_proxy import OAuthProxy

proxy = OAuthProxy(platform, app)
proxy.mount_routes()  # /api/auth/login, /api/auth/callback, /api/auth/user
```

- **HF Spaces**: 通过 `authlib` 对接 HF OAuth2，注入 `x-forwarded-*` 头绕过代理限制
- **ModelScope Studio**: OAuth 回调路径适配 `/api/auth/callback`，处理平台代理 header 剥离
- 用户身份解析后注入 `X-Nanobot-Sender-ID` / `X-Nanobot-Sender-Name` header

### 3. Relay Token 映射

云平台环境中 token 以环境变量形式注入，命名规则：

```
SQUAD_RELAY_TOKEN_{PLATFORM}_{space_name}
```

例如：
- `SQUAD_RELAY_TOKEN_HF_nanobot_cloud_demo`
- `SQUAD_RELAY_TOKEN_MS_ms_nanobot_cloud_demo`

`python3 -m cloud_agent_gateway.platform_setup` 在启动时自动探测平台的三维坐标（platform × engine × squad）、展开环境变量、写入 shell profile。

### 4. 身份注入

平台代理通常会剥离 `Authorization` 等自定义 header。在应用层注入身份，无需依赖原始 header。

```python
# 注入 sender_id 到 WebSocket envelope
envelope["sender_id"] = user_info["sub"]
envelope["sender_name"] = user_info.get("name", "")
```

### 5. Header 剥离感知

平台代理会剥离/改写以下 header：
- `Authorization` → 移除
- `Content-Length` → ModelScope 注入 0，导致部分 HTTP 库拒绝响应

`PlatformProtocol` 提供 `strip_response_headers` 方法，平台子类声明需要剥离的 header，gatekeeper 自动处理。

## 安装

```bash
pip install cloud-agent-gateway
```

## 使用

### 作为 CLI（单智能体快速启动）

```bash
# 平台初始化（写入 token 等环境变量）
cloud-gateway-setup

# 启动 OAuth 代理 + nanobot gateway
cloud-agent-gateway
```

Cloud Demo 空间使用此模式：平台层 + 上游原生 nanobot，零定制。

### 作为库（嵌入 Squad 应用）

```python
from cloud_agent_gateway.platforms import platform
from cloud_agent_gateway.oauth_proxy import OAuthProxy

# 平台自动探测
print(f"Running on {platform.PLATFORM_NAME}")

# 挂载 OAuth 路由
proxy = OAuthProxy(platform, app)
proxy.mount_routes()

# 注入身份到 WebSocket envelope
# → 见 nanobot-legion gatekeeper.py
```

## 架构

```
┌──────────────────────────────────────────────────────────┐
│                    应用部署层                               │
│                 nanobot-legion                             │
│  Gatekeeper · Squad Bridge · Patches · Dockerfile         │
│                                                           │
│  部署到: Nightly · HF Staging · MS Staging                │
└──────────────────────────────────────────────────────────┘
                           ▲ pip install
┌──────────────────────────────────────────────────────────┐
│              cloud-agent-gateway (本包)                     │
│                    框架底层                                 │
│                                                           │
│  ┌─────────┐  ┌──────────────────┐  ┌───────────────┐    │
│  │OAuthProxy│  │PlatformProtocol  │  │  Relay Mgr    │    │
│  └─────────┘  └──────────────────┘  └───────────────┘    │
│                                                           │
│  直接使用: HF Cloud Demo · MS Cloud Demo                  │
└──────────────────────────────────────────────────────────┘
```

## 相关

- [nanobot-legion](https://github.com/DreamShepherd2006/nanobot-legion) — 上层应用部署层（Squad 多智能体）
- [HKUDS/nanobot](https://github.com/HKUDS/nanobot) — agent 框架
- [PR #4139](https://github.com/HKUDS/nanobot/pull/4139) — `target_chat_id` 会话恢复（配合身份注入使用）

## 许可证

MIT
