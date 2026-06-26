#!/bin/bash
set -e

# Setup 模式检测
if [ ! -f /app/config.json ]; then
    echo "→ 首次启动，进入配置模式"
    exec python3 -m cloud_agent_gateway.setup
fi

echo "→ 检测到 config.json，正常启动 CAG"
exec python3 -m cloud_agent_gateway.platform_setup \
    --workspace /mnt/workspace \
    --host 0.0.0.0 \
    --port 7860
