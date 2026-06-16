# cloud-agent-gateway

AI agent 云部署体系的**框架底层**——平台抽象、OAuth 认证、通道绑定、持久化同步、HTTP Relay 中继。

## 定位

`cloud-agent-gateway` 是一个 pip 包，位于部署栈的底层。它**不包含任何 agent 逻辑**——只负责平台探测、OAuth 回调、身份注入、通道绑定、Relay 中继等基础设施。

```
应用部署层  (nanobot-legion)     ← Squad 多智能体，依赖本包
       ▲ pip install
框架底层  (cloud-agent-gateway)   ← 本包：平台抽象 + OAuth + 通道绑定 + Relay
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

| 平台类 | OAuth | Relay | 备注 |
|:---|:---|:---|:---|
| `HFStagingPlatform` | ✅ | ✅ | 完整 OAuth + WS 身份注入 |
| `HFDirectPlatform` | — | ✅ | 仅 relay，无 OAuth |
| `HFSpacesPlatform` | ✅ | ✅ | HF OAuth via authlib |
| `ModelScopePlatform` | ✅ | ✅ | MS OAuth + 路由绕过 |
| `ModelScopeSquadPlatform` | ✅ | ✅ | Squad 内部变体 + dataset 同步 |

### 2. 持久化存储（`PersistentStorageProtocol`）

统一的持久化协议，封装 11 个读写方法，平台子类通过 `_on_persistent_write()` 钩子触发外部同步：

```
read_config      read_credential   read_sidebar_state
read_session     write_config      write_credential
write_sidebar_state  write_session  write_webui_transcript
delete_session   _on_persistent_write  ← 同步钩子
```

ModelScope 部署通过 `_on_persistent_write` 将 `/mnt/workspace/instances/` 镜像到 ModelScope 数据集，实现配置的跨重启持久化。

### 3. Dataset 双向同步

ModelScope 数据集作为持久化存储的后端，支持容器↔数据集双向同步：

```
容器 → 数据集:  _on_persistent_write() 推送变更
数据集 → 容器:  后台 daemon 线程每 60s 轮询，检测到远端新 commit 后
               自动 merge + deep-merge config.json 到运行中实例
```

`_git_op_lock` 保证推送与轮询的 git 操作互斥，避免竞态。

### 4. OAuth 代理

`OAuthProxy` 提供统一的认证流程：

```python
from cloud_agent_gateway.oauth_proxy import OAuthProxy

proxy = OAuthProxy(platform, app)
proxy.mount_routes()  # /api/auth/login, /api/auth/callback, /api/auth/user
```

- **HF Spaces**: 通过 `authlib` 对接 HF OAuth2，注入 `x-forwarded-*` 头绕过代理限制
- **ModelScope Studio**: OAuth 回调路径适配 `/api/auth/callback`，处理平台代理 header 剥离
- 用户身份解析后注入 `X-Nanobot-Sender-ID` / `X-Nanobot-Sender-Name` header

### 5. Relay Token 映射

云平台环境中 token 以环境变量形式注入，命名规则：

```
SQUAD_RELAY_TOKEN_{PLATFORM}_{space_name}
```

例如：
- `SQUAD_RELAY_TOKEN_HF_nanobot_cloud_demo`
- `SQUAD_RELAY_TOKEN_MS_NanobotNightly`

`python3 -m cloud_agent_gateway.platform_setup` 在启动时自动探测平台、展开环境变量、统一映射到 `SQUAD_RELAY_TOKEN`（供 `oauth_proxy.py` / `gatekeeper.py` 读取）。

### 6. 通道绑定（Channel Binding）

自服务通道绑定系统，支持 15 个社交通道：

| 自动绑定（7 个） | 手动配置（8 个） |
|:---|:---|
| 微信、QQ、飞书、钉钉、Telegram、Discord、Slack | WhatsApp、WeCom、NapCat、Mochat、MSTeams、Matrix、Signal、Email |

```python
from cloud_agent_gateway.deploy.cloud.channel_bindings import discover

# 自动发现所有 BindingSpec
bindings = discover()
# → 7 个自动绑定 + 8 个手动配置页
```

设计原则：
- 新增通道 = 一个 `BindingSpec` 文件 + 一行 import，框架零改动
- 凭证持久化到 `channels/{channel}/account.json`，Dockerfile 补丁轮询自动连接
- 绑定页通过 `/bind/{channel}` 公开路由提供，无需 LLM 参与

### 7. 身份注入 & Header 剥离感知

平台代理通常会剥离 `Authorization` 等自定义 header。在应用层注入身份：

```python
envelope["sender_id"] = user_info["sub"]
envelope["sender_name"] = user_info.get("name", "")
```

`PlatformProtocol` 提供 `strip_response_headers` 方法，平台子类声明需要剥离的 header（如 ModelScope 注入的 `Content-Length: 0`），gatekeeper 自动处理。

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
```

## 架构

```
┌──────────────────────────────────────────────────────────────────┐
│                     应用部署层 (nanobot-legion)                    │
│       Gatekeeper · Squad Bridge · Patches · Dockerfile           │
│                                                                  │
│       部署到: Nightly · HF Staging · MS Staging                  │
└──────────────────────────────────────────────────────────────────┘
                              ▲ pip install
┌──────────────────────────────────────────────────────────────────┐
│                cloud-agent-gateway (本包) v0.1.5                  │
│                         框架底层                                  │
│                                                                  │
│  ┌──────────┐ ┌───────────────────┐ ┌─────────────────────────┐  │
│  │OAuthProxy│ │PlatformProtocol   │ │  PersistentStorage      │  │
│  │          │ │ 5 个平台子类       │ │  11 方法 + dataset 同步  │  │
│  └──────────┘ └───────────────────┘ └─────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Channel Binding (15 通道) · Relay Token 映射 · 身份注入    │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  直接使用: HF Cloud Demo · MS Cloud Demo                        │
│  作为依赖: nanobot-legion                                        │
└──────────────────────────────────────────────────────────────────┘
```

## 相关

- [nanobot-legion](https://github.com/DreamShepherd2006/nanobot-legion) — 上层应用部署层（Squad 多智能体）
- [HKUDS/nanobot](https://github.com/HKUDS/nanobot) — agent 框架
- [PR #4139](https://github.com/HKUDS/nanobot/pull/4139) — `target_chat_id` 会话恢复（配合身份注入使用）
- [PR #4271](https://github.com/HKUDS/nanobot/pull/4271) — `read_only` 会话元数据标记

## 许可证

MIT
