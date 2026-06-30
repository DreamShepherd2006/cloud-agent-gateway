"""
CAG Template Phase 2 launcher.

Identical to Cloud Native entrypoint.sh flow:
    platform_setup → data_root → first-run seed → storage symlink
    → gateway → oauth_proxy

Extra: export OAUTH_CLIENT_ID / OAUTH_CLIENT_SECRET from oauth.json
(Phase 1 writes it; Phase 2 reads it for OAuth auto-config).
"""
import json
import os
import shutil
import subprocess
import sys
import time


def main() -> None:
    print(f"\n{'='*50}")
    print(f"  CAG template — Phase 2 — production mode")
    print(f"{'='*50}\n")

    # ── 1. Platform detection (matches Cloud Native entrypoint.sh) ──
    print("── Platform ──")
    sys.stdout.flush()
    result = subprocess.run(
        [sys.executable, "-m", "cloud_agent_gateway.platform_setup"],
        capture_output=True, text=True,
    )
    if result.stderr:
        print(result.stderr.strip())
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("export "):
            rest = line[len("export "):]
            if "=" in rest:
                name, _, val = rest.partition("=")
                val = val.strip("'\"")
                os.environ[name] = val

    # Use DATA_ROOT from platform_setup (Cloud Native source of truth)
    data_root = os.environ.get("DATA_ROOT", "/data")
    print(f"    data_root: {data_root}")

    # ── 2. OAuth from oauth.json (Phase 1→2 bridge) ──
    print("── OAuth ──")
    oauth_path = os.path.join(data_root, "oauth.json")
    try:
        with open(oauth_path) as f:
            oauth = json.load(f)
        cid = oauth.get("client_id", "")
        secret = oauth.get("client_secret", "")
        if cid and secret:
            os.environ["OAUTH_CLIENT_ID"] = cid
            os.environ["OAUTH_CLIENT_SECRET"] = secret
            print(f"    ✅ OAuth configured (client_id={cid})")
        else:
            print("    ℹ️  OAuth not configured")
    except FileNotFoundError:
        print("    ℹ️  oauth.json not found (OAuth disabled)")

    # ── 3. Config seed + storage (matches Cloud Native entrypoint.sh) ──
    print("── Storage ──")
    inst_dir = os.path.join(data_root, "instances", "default")
    config_path = os.path.join(inst_dir, "config.json")

    # First-run config seed
    if not os.path.isfile(config_path):
        os.makedirs(inst_dir, exist_ok=True)
        template_cfg = "/app/config.template.json"
        if os.path.isfile(template_cfg):
            shutil.copy(template_cfg, config_path)
            print(f"    🔧 first run: seeded config from {template_cfg}")
        else:
            print(f"    ⚠️  {template_cfg} not found — nanobot will use defaults")
    else:
        os.makedirs(inst_dir, exist_ok=True)

    # Symlink ~/.nanobot/instances → $DATA_ROOT/instances (Cloud Native pattern)
    home = os.environ.get("HOME", "/home/nanobot")
    nanobot_home = os.path.join(home, ".nanobot")
    os.makedirs(nanobot_home, exist_ok=True)
    link = f"{nanobot_home}/instances"
    if not os.path.islink(link):
        try:
            os.symlink(f"{data_root}/instances", link)
        except FileExistsError:
            pass

    # Channel credential path (NANOBOT_ACCOUNT_BASE)
    channels_dir = f"{inst_dir}/channels"
    os.makedirs(channels_dir, exist_ok=True)
    os.environ["NANOBOT_ACCOUNT_BASE"] = channels_dir
    print(f"    instances  → {link}")
    print(f"    channels   → {channels_dir}")

    # ── 3.5. MCP auto-injection (CAG tools → nanobot MCP servers) ──
    print("── MCP ──")
    try:
        from cloud_agent_gateway.mcp import inject_mcp_config
        inject_mcp_config(config_path)
    except Exception as exc:
        print(f"    ⚠️  MCP injection failed: {exc}")

    # ── 4. Gateway (matches Cloud Native entrypoint.sh) ──
    print("── Gateway ──")
    with open(config_path) as f:
        cfg = json.load(f)
    gw_port = str(cfg["gateway"]["port"])
    ws_port = str(cfg["channels"]["websocket"]["port"])
    print(f"    port: {gw_port}  ws: {ws_port}")

    gw = subprocess.Popen(
        [
            sys.executable, "-u", "-m", "nanobot",
            "gateway",
            "--config", config_path,
            "--workspace", os.path.join(data_root, "instances"),
        ],
        env=os.environ.copy(),
    )

    # Wait for health
    import urllib.request
    for i in range(30):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{gw_port}/health", timeout=2)
            print(f"    ✅ ready ({i * 2 + 2}s)")
            break
        except Exception:
            try:
                os.kill(gw.pid, 0)
            except OSError:
                print("❌ gateway exited unexpectedly", file=sys.stderr)
                sys.exit(1)
        time.sleep(2)
    else:
        print("❌ gateway failed to start", file=sys.stderr)
        sys.exit(1)

    # ── 5. OAuth proxy (matches Cloud Native entrypoint.sh) ──
    print("── Proxy ──")
    print("    oauth_proxy → :7860")
    print(f"{'='*50}\n")
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable, "-m", "cloud_agent_gateway"])


if __name__ == "__main__":
    main()
