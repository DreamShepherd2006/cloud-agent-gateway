"""
CAG Template Phase 2 launcher.

Same path as MS Cloud Native:
  oauth.json → env vars → platform_setup → gateway → oauth_proxy
"""
import json
import os
import subprocess
import sys
import time


def main() -> None:
    from cloud_agent_gateway.setup import _detect_data_root
    data_root = _detect_data_root()
    config_path = os.path.join(data_root, "instances", "default", "config.json")
    home = os.environ.get("HOME", "/home/nanobot")

    print(f"\n{'='*50}")
    print(f"  CAG template — Phase 2 — production mode")
    print(f"{'='*50}\n")

    # ── 1. Platform detection (identical to MS Cloud Native) ──
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
    print(f"    platform: {os.environ.get('DEPLOY_PLATFORM', 'unknown')}")

    # ── 2. OAuth (exports same env vars MS Cloud Native uses) ──
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

    # ── 3. Storage ──
    print("── Storage ──")
    inst_dir = os.path.join(data_root, "instances", "default")
    os.makedirs(f"{inst_dir}/workspace/sessions", exist_ok=True)
    os.makedirs(f"{inst_dir}/workspace/memory", exist_ok=True)
    channels_dir = f"{inst_dir}/channels"
    os.makedirs(channels_dir, exist_ok=True)

    nanobot_home = os.path.join(home, ".nanobot")
    os.makedirs(nanobot_home, exist_ok=True)
    link = f"{nanobot_home}/instances"
    if not os.path.islink(link):
        try:
            os.symlink(f"{data_root}/instances", link)
        except FileExistsError:
            pass
    os.environ["NANOBOT_ACCOUNT_BASE"] = channels_dir
    print(f"    instances  → {link}")
    print(f"    channels   → {channels_dir}")

    # ── 4. Gateway ──
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

    # ── 5. OAuth proxy (same as MS Cloud Native) ──
    print("── Proxy ──")
    print("    oauth_proxy → :7860")
    print(f"{'='*50}\n")
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable, "-m", "cloud_agent_gateway"])


if __name__ == "__main__":
    main()
