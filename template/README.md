# CAG Template — 一键部署到 ModelScope 创空间

本仓库是最简模板，用于在 ModelScope Studio 创建 Docker 类型的 **个人 AI 助手空间**。

## 使用方法

1. 在 [ModelScope 创空间](https://modelscope.cn/studios) 创建新空间
2. SDK 类型选 **Docker**
3. 将本仓库文件拖拽到上传区（或通过 Git 上传）
4. 等待构建完成 → 打开空间 → 填写配置 → 开始使用

## 文件说明

| 文件 | 作用 |
|------|------|
| `Dockerfile` | 构建时从 GitHub 安装 CAG 框架 |
| `entrypoint.sh` | 启动时判断：无配置 → setup 页 / 有配置 → 正常运行 |
| `config.template.json` | 空壳模板，触发 setup 模式 |

## 工作流程

```
用户打开空间 → entrypoint 检测
    ├─ 无 config.json → 显示 setup 配置页（填 API key/模型）
    └─ 有 config.json → 正常启动 CAG（OAuth + 通道绑定）
```

## 重新配置

运行中的空间如需重新配置（换 API Key、模型或 OAuth）：

1. 浏览器访问 `https://你的空间地址/reset-setup`
2. 空间**停止 → 启动**（工厂重建不会清除持久卷上的配置）
3. 自动进入初始化配置页

> `/reset-setup` 仅删除 OAuth 凭证，已有的 API Key 和模型设置会保留并预填。
