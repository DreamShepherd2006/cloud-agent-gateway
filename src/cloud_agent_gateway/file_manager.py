"""
File Manager — file listing, upload, download, delete for cag-template users.

Mounted at /files in oauth_proxy. Path traversal is blocked.
Works across HF (/data/files/) and ModelScope (/mnt/workspace/files/).
"""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from datetime import datetime, timezone

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse
from starlette.routing import Route


# ── Files directory ────────────────────────────────────────────────
def _detect_files_dir() -> str:
    for base in ("/mnt/workspace", "/data"):
        if os.path.isdir(base):
            d = os.path.join(base, "files")
            os.makedirs(d, exist_ok=True)
            return d
    return "/tmp/files"

FILES_DIR = _detect_files_dir()


def _safe_path(subpath: str) -> Path:
    """Resolve subpath within FILES_DIR, blocking traversal."""
    root = Path(FILES_DIR).resolve()
    target = (root / subpath).resolve()
    if not str(target).startswith(str(root)):
        raise ValueError("path traversal blocked")
    return target


def _format_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n}{unit}"
        n //= 1024
    return f"{n}TB"


def _format_time(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    local = dt.astimezone()
    return local.strftime("%Y-%m-%d %H:%M")


# ── HTML page ──────────────────────────────────────────────────────
_STYLE = """
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         max-width: 720px; margin: 40px auto; padding: 0 20px;
         background: #0d1117; color: #c9d1d9; }
  h1 { font-size: 20px; margin-bottom: 24px; }
  h1 a { color: #58a6ff; text-decoration: none; font-size: 14px; margin-left: 12px; }
  .upload-zone { border: 2px dashed #30363d; border-radius: 8px; padding: 24px;
                 text-align: center; margin-bottom: 24px; }
  .upload-zone:hover { border-color: #58a6ff; }
  .upload-zone input[type=file] { display: none; }
  .upload-zone label { color: #58a6ff; cursor: pointer; font-size: 14px; }
  .upload-zone .hint { color: #8b949e; font-size: 12px; margin-top: 6px; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; color: #8b949e; font-size: 12px; text-transform: uppercase;
       padding: 8px 0; border-bottom: 1px solid #21262d; }
  td { padding: 10px 0; border-bottom: 1px solid #21262d; font-size: 14px; }
  .name { word-break: break-all; }
  .name a { color: #c9d1d9; text-decoration: none; }
  .name a:hover { color: #58a6ff; }
  .actions a { color: #8b949e; text-decoration: none; font-size: 13px; margin-right: 12px; }
  .actions a:hover { color: #f85149; }
  .actions a.dl:hover { color: #58a6ff; }
  .empty { text-align: center; color: #484f58; padding: 40px 0; }
  #status { position: fixed; top: 16px; right: 16px; padding: 8px 16px;
            border-radius: 6px; font-size: 13px; display: none; z-index: 999; }
  #status.ok { background: #238636; color: #fff; display: block; }
  #status.err { background: #da3633; color: #fff; display: block; }
</style>
"""

_SCRIPT = """
<script>
async function upload(file) {
  const fd = new FormData();
  fd.append('file', file);
  const r = await fetch('/files/upload', { method: 'POST', body: fd });
  const d = await r.json();
  const s = document.getElementById('status');
  if (d.ok) {
    s.className = 'ok'; s.textContent = '✓ 上传成功: ' + d.name;
  } else {
    s.className = 'err'; s.textContent = '✗ 上传失败';
  }
  setTimeout(() => { s.className = ''; }, 3000);
  setTimeout(() => location.reload(), 500);
}

async function delFile(name) {
  if (!confirm('删除 ' + name + '？')) return;
  const r = await fetch('/files/delete/' + encodeURIComponent(name), { method: 'DELETE' });
  const d = await r.json();
  if (d.ok) location.reload();
}

document.getElementById('fileInput').addEventListener('change', function() {
  if (this.files.length) upload(this.files[0]);
});
</script>
"""

HTML = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>文件管理</title>
{_STYLE}
</head>
<body>
<div id="status"></div>
<h1>📁 文件管理<a href="/">← 返回对话</a></h1>
<div class="upload-zone">
  <input type="file" id="fileInput">
  <label for="fileInput">📤 点击上传文件</label>
  <div class="hint">支持任意类型文件</div>
</div>
<table id="fileTable">
  <thead><tr><th>文件名</th><th>大小</th><th>时间</th><th></th></tr></thead>
  <tbody></tbody>
</table>
<div class="empty" id="emptyMsg">暂无文件</div>
{_SCRIPT}
</body>
</html>"""


def _render_listing() -> str:
    """Return HTML with file list rows injected."""
    files = []
    for entry in sorted(Path(FILES_DIR).iterdir(), key=lambda e: e.stat().st_mtime, reverse=True):
        if entry.is_file():
            s = entry.stat()
            files.append({
                "name": entry.name,
                "size": _format_size(s.st_size),
                "time": _format_time(s.st_mtime),
            })

    if not files:
        return HTML

    rows = []
    for f in files:
        enc = f["name"]  # URL-encoding handled by browser
        rows.append(
            f'<tr>'
            f'<td class="name"><a href="/files/view/{enc}">{f["name"]}</a></td>'
            f'<td>{f["size"]}</td>'
            f'<td>{f["time"]}</td>'
            f'<td class="actions">'
            f'<a class="dl" href="/files/view/{enc}">下载</a>'
            f'<a href="javascript:delFile(\'{f["name"]}\')">删除</a>'
            f'</td></tr>'
        )

    # Inject rows and hide empty message
    body = HTML.replace('<div class="empty" id="emptyMsg">暂无文件</div>',
                        '<div class="empty" id="emptyMsg" style="display:none">暂无文件</div>')
    body = body.replace('<tbody></tbody>', '<tbody>' + ''.join(rows) + '</tbody>')
    return body


# ── Route handlers ─────────────────────────────────────────────────
async def list_page(request: Request) -> HTMLResponse:
    return HTMLResponse(_render_listing())


async def view_file(request: Request) -> Response:
    subpath = request.path_params.get("path", "")
    if not subpath:
        return RedirectResponse("/files")
    try:
        path = _safe_path(subpath)
    except ValueError:
        return JSONResponse({"error": "invalid path"}, status_code=400)
    if not path.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)

    media_type, _ = mimetypes.guess_type(str(path))
    return FileResponse(str(path), media_type=media_type or "application/octet-stream",
                        filename=path.name)


async def upload_file(request: Request) -> JSONResponse:
    try:
        form = await request.form()
    except Exception:
        return JSONResponse({"error": "invalid form"}, status_code=400)
    uploaded = form.get("file")
    if uploaded is None:
        return JSONResponse({"error": "no file"}, status_code=400)
    filename = Path(uploaded.filename).name
    if not filename:
        return JSONResponse({"error": "empty filename"}, status_code=400)
    content = await uploaded.read()
    dest = Path(FILES_DIR) / filename
    dest.write_bytes(content)
    return JSONResponse({"ok": True, "name": filename, "size": len(content)})


async def delete_file(request: Request) -> JSONResponse:
    subpath = request.path_params.get("path", "")
    if not subpath:
        return JSONResponse({"error": "missing path"}, status_code=400)
    try:
        path = _safe_path(subpath)
    except ValueError:
        return JSONResponse({"error": "invalid path"}, status_code=400)
    if not path.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    path.unlink()
    return JSONResponse({"ok": True, "deleted": path.name})


# ── App ────────────────────────────────────────────────────────────
app = Starlette(routes=[
    Route("/", list_page, methods=["GET"]),
    Route("/view/{path:path}", view_file, methods=["GET"]),
    Route("/upload", upload_file, methods=["POST"]),
    Route("/delete/{path:path}", delete_file, methods=["DELETE"]),
])