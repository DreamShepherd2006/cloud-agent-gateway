"""
File Manager — listing, upload, download, delete, mkdir, touch for cag-template users.

Mounted at /files in oauth_proxy. Path traversal blocked.
Works across HF (/data/files/) and ModelScope (/mnt/workspace/files/).
"""

from __future__ import annotations

import mimetypes
import os
import shutil
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


def _root() -> Path:
    return Path(FILES_DIR).resolve()


def _safe_path(subpath: str) -> Path:
    """Resolve subpath within FILES_DIR, blocking traversal."""
    target = (_root() / subpath).resolve()
    if not str(target).startswith(str(_root())):
        raise ValueError("path traversal blocked")
    return target


def _current_dir(request: Request) -> Path:
    """Get current directory from ?dir= query param (relative to FILES_DIR)."""
    raw = request.query_params.get("dir", "")
    if not raw:
        return _root()
    try:
        d = _safe_path(raw)
    except ValueError:
        return _root()
    if not d.is_dir():
        return _root()
    return d


def _rel_path(p: Path) -> str:
    """Path relative to FILES_DIR root."""
    return str(p.resolve().relative_to(_root()))


def _format_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n}{unit}"
        n //= 1024
    return f"{n}TB"


def _format_time(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


# ── HTML page ──────────────────────────────────────────────────────
_STYLE = """
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         max-width: 780px; margin: 40px auto; padding: 0 20px;
         background: #0d1117; color: #c9d1d9; }
  h1 { font-size: 20px; margin-bottom: 8px; }
  h1 a { color: #58a6ff; text-decoration: none; font-size: 14px; margin-left: 12px; }
  .breadcrumb { margin-bottom: 16px; font-size: 14px; color: #8b949e; }
  .breadcrumb a { color: #58a6ff; text-decoration: none; }
  .breadcrumb span { color: #c9d1d9; }
  .toolbar { display: flex; gap: 10px; margin-bottom: 20px; flex-wrap: wrap; }
  .toolbar button { background: #21262d; color: #c9d1d9; border: 1px solid #30363d;
                    border-radius: 6px; padding: 6px 14px; cursor: pointer; font-size: 13px; }
  .toolbar button:hover { background: #30363d; }
  .upload-zone { border: 2px dashed #30363d; border-radius: 8px; padding: 24px;
                 text-align: center; margin-bottom: 20px; }
  .upload-zone:hover { border-color: #58a6ff; }
  .upload-zone input[type=file] { display: none; }
  .upload-zone label { color: #58a6ff; cursor: pointer; font-size: 14px; }
  .upload-zone .hint { color: #8b949e; font-size: 12px; margin-top: 6px; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; color: #8b949e; font-size: 12px; text-transform: uppercase;
       padding: 8px 0; border-bottom: 1px solid #21262d; }
  td { padding: 10px 0; border-bottom: 1px solid #21262d; font-size: 14px; }
  .icon { width: 20px; display: inline-block; text-align: center; }
  .name { word-break: break-all; }
  .name a { color: #c9d1d9; text-decoration: none; }
  .name a:hover { color: #58a6ff; }
  .name a.dir { color: #58a6ff; font-weight: 500; }
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
const CUR_DIR = '__CUR_DIR__';

async function upload(file) {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('dir', CUR_DIR);
  const r = await fetch('/files/upload', { method: 'POST', body: fd });
  const d = await r.json();
  const s = document.getElementById('status');
  if (d.ok) {
    s.className = 'ok'; s.textContent = '✓ 上传成功: ' + d.name;
  } else {
    s.className = 'err'; s.textContent = '✗ 上传失败: ' + (d.error || '');
  }
  setTimeout(() => { s.className = ''; }, 3000);
  setTimeout(() => location.reload(), 500);
}

async function delEntry(name, isDir) {
  const label = isDir ? '删除目录 ' : '删除 ';
  if (!confirm(label + name + '？' + (isDir ? '\\n目录内容将一并删除！' : ''))) return;
  const r = await fetch('/files/delete/' + encodeURIComponent((CUR_DIR ? CUR_DIR + '/' : '') + name),
                        { method: 'DELETE' });
  const d = await r.json();
  if (d.ok) location.reload();
  else alert('删除失败: ' + (d.error || ''));
}

function mkdir() {
  const name = prompt('新目录名:');
  if (!name) return;
  fetch('/files/mkdir', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({dir: CUR_DIR, name: name})
  }).then(r => r.json()).then(d => {
    if (d.ok) location.reload();
    else alert('创建失败: ' + (d.error || ''));
  });
}

function touch() {
  const name = prompt('新文件名:');
  if (!name) return;
  fetch('/files/touch', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({dir: CUR_DIR, name: name})
  }).then(r => r.json()).then(d => {
    if (d.ok) location.reload();
    else alert('创建失败: ' + (d.error || ''));
  });
}

function refresh() { location.reload(); }

document.getElementById('fileInput').addEventListener('change', function() {
  if (this.files.length) upload(this.files[0]);
});
</script>
"""


def _listing_url(dir_path: str = "") -> str:
    if dir_path:
        return f"/files/?dir={dir_path}"
    return "/files"


def _view_url(rel: str) -> str:
    return f"/files/view/{rel}"


def _render_listing(cur: Path) -> str:
    """Build HTML with breadcrumb, toolbar, and entry list."""
    rel = _rel_path(cur) if cur != _root() else ""

    # Breadcrumb
    breadcrumbs = ['<a href="/files">📁 根目录</a>']
    if rel:
        parts = rel.split("/")
        for i, p in enumerate(parts):
            prefix = "/".join(parts[:i + 1])
            breadcrumbs.append(f'<a href="{_listing_url(prefix)}">{p}</a>')
    breadcrumb_html = " / ".join(breadcrumbs)

    # Collect entries (dirs first, then files)
    dirs = []
    files = []
    try:
        for entry in sorted(cur.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            s = entry.stat()
            item = {
                "name": entry.name,
                "rel": (_rel_path(entry) if cur != _root() else entry.name),
                "mtime": _format_time(s.st_mtime),
            }
            if entry.is_dir():
                item["size"] = "-"
                dirs.append(item)
            elif entry.is_file():
                item["size"] = _format_size(s.st_size)
                files.append(item)
    except PermissionError:
        pass

    if not dirs and not files:
        return _render_template(rel, breadcrumb_html, "", '<div class="empty">暂无文件</div>')

    # Build table rows
    rows = []
    for d in dirs:
        rows.append(
            f'<tr>'
            f'<td class="name"><span class="icon">📁</span> '
            f'<a class="dir" href="{_listing_url(d["rel"])}">{d["name"]}/</a></td>'
            f'<td>{d["size"]}</td>'
            f'<td>{d["mtime"]}</td>'
            f'<td class="actions">'
            f'<a href="javascript:delEntry(\'{d["name"]}\', true)">删除</a>'
            f'</td></tr>'
        )
    for f in files:
        rows.append(
            f'<tr>'
            f'<td class="name"><span class="icon">📄</span> '
            f'<a href="{_view_url(f["rel"])}">{f["name"]}</a></td>'
            f'<td>{f["size"]}</td>'
            f'<td>{f["mtime"]}</td>'
            f'<td class="actions">'
            f'<a class="dl" href="{_view_url(f["rel"])}">下载</a>'
            f'<a href="javascript:delEntry(\'{f["name"]}\', false)">删除</a>'
            f'</td></tr>'
        )

    table = f"""<table>
  <thead><tr><th>名称</th><th>大小</th><th>修改时间</th><th></th></tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table>"""
    return _render_template(rel, breadcrumb_html, table, "")


def _render_template(cur_rel: str, breadcrumb: str, table: str, empty: str) -> str:
    script = _SCRIPT.replace("__CUR_DIR__", cur_rel)
    return f"""<!DOCTYPE html>
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
<div class="breadcrumb">{breadcrumb}</div>
<div class="toolbar">
  <button onclick="mkdir()">📁 新建目录</button>
  <button onclick="touch()">📄 新建文件</button>
  <button onclick="refresh()">🔄 刷新</button>
</div>
<div class="upload-zone">
  <input type="file" id="fileInput">
  <label for="fileInput">📤 点击上传文件</label>
  <div class="hint">支持任意类型文件</div>
</div>
{table}
{empty}
{script}
</body>
</html>"""


# ── Route handlers ─────────────────────────────────────────────────
async def list_page(request: Request) -> HTMLResponse:
    cur = _current_dir(request)
    return HTMLResponse(_render_listing(cur))


async def view_file(request: Request) -> FileResponse | JSONResponse | RedirectResponse:
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

    # Respect current directory from form
    dir_rel = form.get("dir", "")
    try:
        dest_dir = _safe_path(dir_rel) if dir_rel else _root()
    except ValueError:
        return JSONResponse({"error": "invalid dir"}, status_code=400)
    if not dest_dir.is_dir():
        return JSONResponse({"error": "dir not found"}, status_code=400)

    content = await uploaded.read()
    dest = dest_dir / filename
    dest.write_bytes(content)
    return JSONResponse({"ok": True, "name": filename, "size": len(content)})


async def delete_entry(request: Request) -> JSONResponse:
    subpath = request.path_params.get("path", "")
    if not subpath:
        return JSONResponse({"error": "missing path"}, status_code=400)
    try:
        path = _safe_path(subpath)
    except ValueError:
        return JSONResponse({"error": "invalid path"}, status_code=400)
    if not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return JSONResponse({"ok": True, "deleted": path.name})


async def mkdir(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "missing name"}, status_code=400)
    # Prevent path separators in name
    if "/" in name or "\\" in name:
        return JSONResponse({"error": "name contains path separator"}, status_code=400)
    dir_rel = body.get("dir", "")
    try:
        parent = _safe_path(dir_rel) if dir_rel else _root()
    except ValueError:
        return JSONResponse({"error": "invalid dir"}, status_code=400)
    new_dir = parent / name
    if new_dir.exists():
        return JSONResponse({"error": "already exists"}, status_code=409)
    new_dir.mkdir(parents=False)
    return JSONResponse({"ok": True, "name": name})


async def touch_file(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "missing name"}, status_code=400)
    if "/" in name or "\\" in name:
        return JSONResponse({"error": "name contains path separator"}, status_code=400)
    dir_rel = body.get("dir", "")
    try:
        parent = _safe_path(dir_rel) if dir_rel else _root()
    except ValueError:
        return JSONResponse({"error": "invalid dir"}, status_code=400)
    new_file = parent / name
    if new_file.exists():
        return JSONResponse({"error": "already exists"}, status_code=409)
    new_file.touch()
    return JSONResponse({"ok": True, "name": name})


# ── App ────────────────────────────────────────────────────────────
app = Starlette(routes=[
    Route("/", list_page, methods=["GET"]),
    Route("/view/{path:path}", view_file, methods=["GET"]),
    Route("/upload", upload_file, methods=["POST"]),
    Route("/delete/{path:path}", delete_entry, methods=["DELETE"]),
    Route("/mkdir", mkdir, methods=["POST"]),
    Route("/touch", touch_file, methods=["POST"]),
])