FROM python:3.12-slim

WORKDIR /app

# ── 系统依赖 ──────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# ── CAG + nanobot ─────────────────────────────────────────────────────
# 🔄 bump BUILD to force reinstall: 2
RUN echo [bust=12] && pip install --no-cache-dir \
    "git+https://github.com/DreamShepherdCD/cloud-agent-gateway.git@feat/setup-page" \
    itsdangerous \
    "git+https://github.com/DreamShepherd2006/nanobot.git@nightly" \
    && echo "[CAG+nanobot] installed"

# ── 0.0.0.0 gateway 绑定 ─────────────────────────────────────────────
RUN SITE_PKG=$(python3 -c 'import site; print(site.getsitepackages()[0])') && \
    CMD_FILE="$SITE_PKG/nanobot/cli/commands.py" && \
    sed -i 's/config\.gateway\.host = "127\.0\.0\.1"/config.gateway.host = "0.0.0.0"/g' "$CMD_FILE" && \
    sed -i '/^def _run_gateway/,/^def /{s/host = host if host is not None else api_cfg\.host/host = "0.0.0.0"/}' "$CMD_FILE" && \
    echo "[patch] 0.0.0.0"

# ── 通道自动重载 ──────────────────────────────────────────────────────
RUN python3 -m cloud_agent_gateway.deploy.cloud.patch_weixin_reload \
    && python3 -m cloud_agent_gateway.deploy.cloud.patch_feishu_reload \
    && python3 -m cloud_agent_gateway.deploy.cloud.patch_dingtalk_reload \
    && python3 -m cloud_agent_gateway.deploy.cloud.patch_qq_reload \
    && echo "[patch] channels"

EXPOSE 7860

RUN useradd -m -u 1000 nanobot && chown -R nanobot:nanobot /app
USER nanobot
ENV HOME=/home/nanobot

# oauth.json 不存在 → Phase 1 setup；存在 → Phase 2 启动
CMD ["bash", "-c", "[ -f /mnt/workspace/oauth.json ] && exec python3 -m cloud_agent_gateway.template_launch || exec python3 -m cloud_agent_gateway.setup"]
