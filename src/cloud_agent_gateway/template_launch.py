"""
CAG Template Phase 2 launcher.

Replaces the shell entrypoint.sh:
- platform detection
- OAuth credential export from config.json
- nanobot gateway startup
- oauth_proxy startup
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def banner(phase: str) -> None:
    print(f"\n{'='*50}")
    print(f"  CAG template — {phase}")
    print(f"{'='*50}\n")


def die(msg: str) -> None:
    print(f"❌ {msg}", file=sys.stderr)
    sys.exit(1)


def find_config(data_root: str) -> str:
    return os.path.join(data_root, "instances", "default", "config.json")


def ensure_dirs(data_root: str, home: str) -> str:
    """Set up persistent storage directories and env."""
    inst = os.path.join(data_root, "instances", "default")
    os.makedirs(f"{inst}/workspace/sessions", exist_ok=True)
    os.makedirs(f"{inst}/workspace/memory", exist_ok=True)
    channels_dir = f"{inst}/channels"
    os.makedirs(channels_dir, exist_ok=True)

    # symlink instances so nanobot can find them
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
    return inst


def export_oauth(data_root: str) -> None:
    """Read oauth.json from persistent volume and export as env vars."""
    oauth_file = os.path.join(data_root, "oauth.json")
    try:
        with open(oauth_file) as f:
            oauth = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        nope = "    ℹ️  OAuth not configured (skip)"
        print(nope)
        return

    cid = oauth.get("client_id", "")
    secret = oauth.get("client_secret", "")
    if cid and secret:
        os.environ["OAUTH_CLIENT_ID"] = cid
        os.environ["OAUTH_CLIENT_SECRET"] = secret
        print(f"    ✅ OAuth configured (client_id={cid})")
    else:
        print(f"    ℹ️  OAuth not configured (skip)")


def wait_health(port: int, pid: int, timeout: int = 60) -> None:
    """Poll gateway health endpoint until ready or timeout."""
    import urllib.request

    for i in range(timeout // 2):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
            print(f"    ✅ nanobot gateway ready ({i * 2 + 2}s)")
            return
        except Exception:
            pass
        # check if process still alive
        try:
            os.kill(pid, 0)
        except OSError:
            die("nanobot gateway exited unexpectedly")
        time.sleep(2)
    die("nanobot gateway failed to start")


def main() -> None:
    data_root = os.environ.get("DATA_ROOT", "/mnt/workspace")
    config_file = find_config(data_root)
    home = os.environ.get("HOME", "/home/nanobot")

    banner("Phase 2 — production mode")

    # ── Platform detection ──
    print("── Platform detection ──")
    sys.stdout.flush()
    result = subprocess.run(
        [sys.executable, "-m", "cloud_agent_gateway.platform_setup"],
        capture_output=True, text=True,
    )
    if result.stdout:
        for line in result.stdout.strip().split("\n"):
            key, _, val = line.partition("=")
            if key and val:
                os.environ[key] = val
                print(f"    {key}={val}")
    if result.stderr:
        print(result.stderr, end="")
    print(f"    platform: {os.environ.get('DEPLOY_PLATFORM', 'unknown')}")

    # ── OAuth ──
    print("── OAuth ──")
    export_oauth(data_root)

    # ── Storage ──
    print("── Storage ──")
    instance_dir = ensure_dirs(data_root, home)

    # ── Gateway ──
    print("── Start nanobot gateway ──")
    with open(config_file) as f:
        cfg = json.load(f)
    gw_port = cfg["gateway"]["port"]
    ws_port = cfg["channels"]["websocket"]["port"]
    print(f"    gateway   : 0.0.0.0:{gw_port}")
    print(f"    websocket : 127.0.0.1:{ws_port}")

    # start gateway as subprocess
    gw = subprocess.Popen(
        [
            sys.executable, "-u", "-m", "nanobot",
            "gateway",
            "--config", config_file,
            "--workspace", os.path.join(data_root, "instances"),
        ],
        env=os.environ.copy(),
    )
    print(f"    PID: {gw.pid}")

    # wait for it
    wait_health(gw_port, gw.pid)

    # ── OAuth proxy ──
    print("── Start OAuth proxy ──")
    print("    listening 0.0.0.0:7860")
    print(f"{'='*50}\n")

    # Replace self with oauth_proxy (keeps container alive)
    os.execv(
        sys.executable,
        [sys.executable, "-m", "cloud_agent_gateway"],
    )


if __name__ == "__main__":
    main()
