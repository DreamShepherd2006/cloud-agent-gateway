"""MCP server for Marp — Markdown → PPTX/PDF/HTML presentations.

Usage: python -m cloud_agent_gateway.mcp.marp_server

The underlying `marp` CLI must be installed (npm install -g @marp-team/marp-cli).
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Built-in Marp themes — always available
_BUILTIN_THEMES = {"default", "gaia", "uncover"}

# Directory containing community CSS theme files (relative to this file)
_THEMES_DIR = Path(__file__).parent / "themes"

# Default Marp theme (gaia = elegant gradient, good for business)
DEFAULT_THEME = "gaia"


def _discover_themes() -> dict[str, Path | None]:
    """Scan built-in + themes/ directory for CSS themes.

    Returns dict {theme_name: Path_or_None}.
    Built-in themes map to None (no --theme-set needed).
    Custom themes map to their CSS file path.
    """
    themes: dict[str, Path | None] = {t: None for t in _BUILTIN_THEMES}
    if not _THEMES_DIR.is_dir():
        return themes
    for css_file in _THEMES_DIR.glob("*.css"):
        try:
            text = css_file.read_text(encoding="utf-8")
            for line in text.split("\n"):
                if "@theme" in line:
                    # Extract theme name from /* @theme xxx */ or @theme xxx
                    name = line.split("@theme", 1)[1].replace("*/", "").strip()
                    if name:
                        themes[name] = css_file
                    break
        except Exception:
            pass
    return themes


def _check_marp() -> str | None:
    """Check that marp CLI is available.  Returns None if OK, else error message."""
    try:
        r = subprocess.run(
            ["marp", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return None
        return f"marp returned {r.returncode}: {r.stderr.strip()}"
    except FileNotFoundError:
        return "marp CLI not found — run: npm install -g @marp-team/marp-cli"
    except Exception as exc:
        return f"marp check failed: {exc}"


def _find_executable() -> str:
    """Find the marp executable, falling back to npx."""
    if shutil.which("marp"):
        return "marp"
    if shutil.which("npx"):
        return "npx"
    return "marp"  # try anyway, let subprocess report the error


def main() -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        print("mcp package not available — run: pip install mcp", file=sys.stderr)
        sys.exit(1)

    err = _check_marp()
    if err:
        print(f"[marp] WARNING: {err}", file=sys.stderr)

    mcp = FastMCP("marp")
    _marp_exe = _find_executable()

    @mcp.tool()
    def render(
        markdown: str,
        output_format: str = "pptx",
        theme: str = DEFAULT_THEME,
    ) -> str:
        """Render Markdown content to a presentation file (PPTX, PDF, or HTML).

        Uses Marp with the specified theme for professional-looking slides.
        The Markdown should use `---` to separate slides and `#` for titles.

        Args:
            markdown: The full Markdown content with Marp-compatible syntax.
            output_format: Output format — "pptx", "pdf", or "html".
            theme: Marp theme name ("default", "gaia", "uncover"). "gaia" is elegant for business.

        Returns:
            Path to the generated file.

        Example Markdown:
            ---
            marp: true
            theme: gaia
            ---
            # Title Slide
            ## Subtitle
            ---
            # Slide 2
            - Point A
            - Point B
        """
        fmt = output_format.lower()
        if fmt not in ("pptx", "pdf", "html"):
            return f"Error: unsupported format '{output_format}'. Use pptx, pdf, or html."

        themes = _discover_themes()
        if theme not in themes:
            available = ", ".join(sorted(themes.keys()))
            return f"Error: unknown theme '{theme}'. Available: {available}"

        # Ensure the markdown has Marp frontmatter (auto-add if missing)
        if not markdown.strip().startswith("---"):
            markdown = f"---\nmarp: true\ntheme: {theme}\n---\n\n{markdown}"
        elif "marp:" not in markdown[:200]:
            markdown = markdown.replace("---", f"---\nmarp: true\ntheme: {theme}", 1)

        md_file = None
        out_dir = None
        try:
            # Write markdown to temp file
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", delete=False, encoding="utf-8"
            ) as f:
                f.write(markdown)
                md_file = f.name

            out_dir = tempfile.mkdtemp()

            # Build marp command
            if _marp_exe == "npx":
                args = ["npx", "-y", "@marp-team/marp-cli"]
            else:
                args = ["marp"]
            out_file = str(Path(out_dir) / f"output.{fmt}")
            args += [
                md_file,
                f"--{fmt}",
                "--allow-local-files",
                "-o", out_file,
            ]

            # Custom themes: point marp at the themes directory
            if theme not in _BUILTIN_THEMES and themes.get(theme):
                args.insert(1, str(_THEMES_DIR))
                args.insert(1, "--theme-set")

            result = subprocess.run(
                args,
                capture_output=True, text=True, timeout=120,
                # MCP stdio transport uses parent stdin for JSON-RPC.
                # Disconnect marp from it to prevent accidental reads/hangs.
                stdin=subprocess.DEVNULL,
                env={
                    **os.environ,
                    # Docker containers lack kernel sandbox capabilities;
                    # Chrome requires --no-sandbox to run headless in containers.
                    "CHROME_NO_SANDBOX": "1",
                },
            )

            if result.returncode != 0:
                return f"Marp error ({result.returncode}): {result.stderr.strip()}"

            # Verify output file
            out_path = Path(out_file)
            if not out_path.is_file() or out_path.stat().st_size == 0:
                return "Error: no output file generated. Check markdown content has at least one slide."

            return f"Generated: {out_path} ({out_path.stat().st_size} bytes)"

        finally:
            if md_file and os.path.isfile(md_file):
                os.unlink(md_file)
            # Keep out_dir (caller can clean up)

    @mcp.tool()
    def list_themes() -> str:
        """List available Marp themes."""
        themes = _discover_themes()
        builtin = [t for t, p in themes.items() if p is None]
        custom = [t for t, p in themes.items() if p is not None]
        lines = [f"Available Marp themes ({len(themes)} total):"]
        lines.append("\n  Built-in:")
        for t in sorted(builtin):
            marker = " ★" if t == DEFAULT_THEME else ""
            lines.append(f"    • {t}{marker}")
        if custom:
            lines.append("\n  Community:")
            for t in sorted(custom):
                lines.append(f"    • {t}")
        lines.append(f"\nDefault: {DEFAULT_THEME}")
        return "\n".join(lines)

    print("[marp] MCP server ready", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
