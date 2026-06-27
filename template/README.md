---
title: nanobot-cloud-demo
emoji: ☁️
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
hf_oauth: true
pinned: false
---

# 🤖 CAG Template — 一键部署个人 AI 助手

本仓库是最简模板，用于在 **HuggingFace Spaces** 或 **ModelScope 创空间** 创建 Docker 类型的个人 AI 助手。

## 使用方法

### HuggingFace
1. 创建 [Docker Space](https://huggingface.co/new-space?sdks=docker)
2. 将本仓库文件上传到空间（或通过 Git 上传）
3. 等待构建完成 → 打开空间 → 填写 LLM 配置 → 开始使用
4. OAuth 登录由 HuggingFace **自动配置**（README 中 `hf_oauth: true`），无需手动创建

### ModelScope
1. 在 [ModelScope 创空间](https://modelscope.cn/studios) 创建新空间，SDK 类型选 **Docker**
2. 将本仓库文件拖拽到上传区（或通过 Git 上传）
3. 等待构建完成 → 打开空间 → 填写 LLM + OAuth 配置 → 开始使用
4. OAuth 需**手动创建**应用（setup 页有指引）

## 文件说明

| 文件 | 作用 |
|------|------|
| `Dockerfile` | 构建时从 GitHub 安装 CAG 框架 + nanobot |
| `entrypoint.sh` | 启动时判断：无配置 → setup 页 / 有配置 → 正常运行 |
| `config.template.json` | 空壳模板，触发 setup 模式 |

## 工作流程

```
用户打开空间 → 检测配置状态
    ├─ 无 oauth.json → Phase 1 配置页（填 API Key / 模型 / OAuth 凭证）
    └─ 有 oauth.json → Phase 2 正常启动（OAuth + 通道绑定）
```

## 重新配置

运行中的空间如需重新配置 OAuth 登录凭证（API Key / 模型会保留并预填）：

1. 浏览器访问 `https://你的空间地址/reset-setup`
2. 空间**停止 → 启动**（工厂重建不会清除持久卷上的配置）
3. 自动进入初始化配置页

> `/reset-setup` 仅删除 OAuth 凭证，已有的 API Key 和模型设置会保留并预填。
