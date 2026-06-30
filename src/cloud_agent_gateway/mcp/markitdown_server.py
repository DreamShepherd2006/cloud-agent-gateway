"""MCP server for Microsoft MarkItDown — Office docs → Markdown.

Usage: python -m cloud_agent_gateway.mcp.markitdown_server

The underlying `markitdown` CLI must be installed (pip install markitdown).
"""

import subprocess
import sys
from pathlib import Path


def _check_markitdown() -> str | None:
    """Check that markitdown CLI is available.  Returns None if OK, else error message."""
    try:
        r = subprocess.run(
            ["markitdown", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return None
        return f"markitdown returned {r.returncode}: {r.stderr.strip()}"
    except FileNotFoundError:
        return "markitdown CLI not found — run: pip install markitdown"
    except Exception as exc:
        return f"markitdown check failed: {exc}"


def main() -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        print("mcp package not available — run: pip install mcp", file=sys.stderr)
        sys.exit(1)

    err = _check_markitdown()
    if err:
        print(f"[markitdown] WARNING: {err}", file=sys.stderr)
        # Continue anyway — tool will report the error when called

    mcp = FastMCP("markitdown")

    @mcp.tool()
    def convert(file_path: str) -> str:
        """Convert a document file (PPTX, DOCX, PDF, XLSX, HTML, etc.) to Markdown.

        Supported formats: .pptx, .docx, .pdf, .xlsx, .html, .csv, .json, .xml,
        .zip, .jpg, .png, .mp3, .wav, and many more.

        Args:
            file_path: Absolute or relative path to the document file.

        Returns:
            The extracted Markdown text content.
        """
        p = Path(file_path)
        if not p.exists():
            return f"Error: file not found — {file_path}"

        result = subprocess.run(
            ["markitdown", str(p)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return f"Error ({result.returncode}): {result.stderr.strip()}"
        return result.stdout.strip()

    print("[markitdown] MCP server ready", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
