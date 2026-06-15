"""
Cloud platform auto-detection.

Follows the data-driven registry pattern from ``nanobot.providers.registry``
(ProviderSpec + PROVIDERS tuple) and the auto-discovery pattern from
``nanobot.channels.registry`` (pkgutil.iter_modules).

At import time the registry evaluates ``PlatformSpec.matches()`` for every
entry in priority order; the first match wins.  The winning platform
implementation is lazy-imported only after detection succeeds.

Usage::

    from platforms import platform
    print(platform.name, platform.data_root)
"""

from __future__ import annotations

import sys
from importlib import import_module

from cloud_agent_gateway.platforms.base import CloudPlatformProtocol, PlatformSpec

# ── Platform registry ──

PLATFORM_SPECS: tuple[PlatformSpec, ...] = (
    PlatformSpec(
        name="modelscope-squad",
        platform="ms", engine="nanobot", squad=True,
        display_name="ModelScope Staging (Squad)",
        detect_env="MODELSCOPE_ENVIRONMENT",
        detect_env_value="studio",
        module=".modelscope_squad",
        priority=5,
    ),
    PlatformSpec(
        name="modelscope",
        platform="ms", engine="nanobot", squad=False,
        display_name="ModelScope",
        detect_env="MODELSCOPE_ENVIRONMENT",
        detect_env_value="studio",
        detect_url_contains="modelscope",
        module=".modelscope",
        priority=10,
    ),
    PlatformSpec(
        name="hf-staging",
        platform="hf", engine="nanobot", squad=True,
        display_name="HF Staging",
        detect_env="HF_SPACE",
        detect_env_alt="SPACE_ID",
        module=".hf_staging",
        priority=20,
    ),
    PlatformSpec(
        name="hf-spaces",
        platform="hf", engine="nanobot", squad=False,
        display_name="HF Spaces (Cloud Demo)",
        detect_env="HF_SPACE",
        detect_env_alt="SPACE_ID",
        module=".hf_spaces",
        priority=30,
    ),
)

platform: CloudPlatformProtocol


# ── Detection ──


def _detect() -> CloudPlatformProtocol:
    """Evaluate specs in priority order; first match wins.  Exit on mismatch."""
    ordered = sorted(PLATFORM_SPECS, key=lambda s: s.priority)

    for spec in ordered:
        if spec.matches():
            _log(spec.name)
            return _load_platform(spec)

    _log_fatal("无法检测运行平台。请确认正确设置了环境变量。")
    sys.exit(1)


def _load_platform(spec: PlatformSpec) -> CloudPlatformProtocol:
    """Lazy-import the platform module and instantiate its implementation."""
    mod = import_module(spec.module, __package__)
    cls = _find_platform_class(mod)
    return cls()


def _find_platform_class(mod):
    """Find the first non-Protocol class in *mod* that has a ``name`` attribute."""
    for attr_name in dir(mod):
        obj = getattr(mod, attr_name)
        if (
            isinstance(obj, type)
            and hasattr(obj, "name")
            and obj.__name__ not in ("CloudPlatformProtocol", "PlatformProtocol")
        ):
            return obj
    raise ImportError(f"No platform class found in {mod.__name__}")


def _log(name: str) -> None:
    sys.stderr.write(f"[PLATFORM] detected → {name}\n")
    sys.stderr.flush()


def _log_fatal(msg: str) -> None:
    sys.stderr.write(f"\n{'='*60}\n")
    sys.stderr.write(f"[PLATFORM] ❌ FATAL: {msg}\n")
    sys.stderr.write(f"可用平台检测规则:\n")
    for spec in sorted(PLATFORM_SPECS, key=lambda s: s.priority):
        hint = f"  · {spec.name} (priority={spec.priority})"
        if spec.detect_env:
            hint += f"  needs ${spec.detect_env}"
            if spec.detect_env_value:
                hint += f"={spec.detect_env_value}"
        if spec.detect_url_contains:
            hint += f"  needs URL containing '{spec.detect_url_contains}'"
        hint += f"  → squad={spec.squad}"
        sys.stderr.write(hint + "\n")
    sys.stderr.write(f"{'='*60}\n\n")
    sys.stderr.flush()


platform = _detect()
