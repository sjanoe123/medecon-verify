"""Reference-data discovery and loading for medecon-verify.

The vintage registries that ``codeset`` stamps against (ICD-10-CM fiscal years,
HCPCS quarters, MS-DRG versions, CMS-HCC phase-in, HEDIS measurement years) are
not hard-coded constants — they are *versioned reference data* that turns over on
CMS's regulatory calendar. This module resolves each named registry through a
documented precedence chain so that a paying subscriber's freshly-shipped feed
transparently supersedes the free tier bundled in the wheel.

Precedence (highest first):

1. **Installed data package** — a ``medecon-verify-data`` distribution that
   advertises an entry point in the group ``medecon_verify.data``. The entry
   point loads to a *provider* object exposing:

       DATA_API_VERSION      int   — the provider-contract version it implements
                                      (optional; defaults to 1 if absent).
       DATA_PACKAGE_VERSION  str   — the human-facing release version, e.g.
                                      "2026Q4" (optional; the installed
                                      distribution's version is used as a
                                      fallback).
       get_registry(name)    -> dict | None
                                    Return the named registry, or None if this
                                    provider does not carry it (fall through to
                                    the bundled tier).

   A provider whose ``DATA_API_VERSION`` this runtime does not support is
   **skipped** (recorded, not silently ignored) and discovery falls through to
   the bundled tier — a lapsed or mismatched feed never yields a half-loaded
   registry.

2. **Bundled lagged free tier** — the JSON files shipped as package data under
   ``medecon_verify/data/registries/``. This is what every install has with no
   subscription; it deliberately lags the paid feed by design (see the product
   plan, 1.2.2).

3. **Strict-mode error** — if neither tier carries the registry, ``load_registry``
   raises :class:`RegistryNotFound`. ``codeset.stamp(..., strict=True)`` turns a
   *coverage* gap (a vintage no installed registry spans) into a loud
   ``CodesetVersionError`` naming the missing registry and the current data
   package. Default (non-strict) callers continue to see ``"UNKNOWN"`` stamps.
"""
from __future__ import annotations

import json
from copy import deepcopy
from importlib import resources
from importlib.metadata import entry_points
from typing import Any, Callable

# Entry-point group a `medecon-verify-data` distribution advertises.
DATA_ENTRY_POINT_GROUP = "medecon_verify.data"

# Provider-contract version this runtime speaks. A provider advertising a
# different major is treated as incompatible and skipped.
SUPPORTED_DATA_API_VERSION = 1

# The bundled free tier lives here (package data, not a sub-package).
_BUNDLED_ANCHOR = "medecon_verify"
_BUNDLED_SUBDIR = ("data", "registries")


class RegistryNotFound(RuntimeError):
    """No installed feed and no bundled file carries the requested registry."""


class DataPackageIncompatible(RuntimeError):
    """An installed data-package provider advertises an unsupported API version.

    Raised only by :func:`require_compatible_data_package`; ordinary
    :func:`load_registry` calls skip an incompatible provider and fall back to
    the bundled tier rather than failing closed, so a default-mode pipeline with
    a stale feed still produces (visibly ``UNKNOWN``) output.
    """


# Provider objects that were discovered but skipped for version incompatibility,
# recorded so strict-mode messages can explain *why* a feed was ignored.
_INCOMPATIBLE: list[tuple[str, Any]] = []


def _iter_data_entry_points() -> list[Any]:
    """Return the entry points in the data group. Seam for tests to monkeypatch.

    Tests inject fake providers by replacing this function; production reads the
    installed distributions' metadata.
    """
    try:
        return list(entry_points(group=DATA_ENTRY_POINT_GROUP))
    except Exception:  # pragma: no cover - defensive against metadata edge cases
        return []


def _entry_point_version(ep: Any) -> str | None:
    """Best-effort release version for a discovered entry point's distribution."""
    dist = getattr(ep, "dist", None)
    if dist is not None:
        version = getattr(dist, "version", None)
        if version:
            return str(version)
    return None


def _load_provider() -> tuple[Any | None, str | None]:
    """Resolve the highest-precedence *compatible* provider.

    Returns ``(provider, version)`` for the first compatible provider, or
    ``(None, None)`` when none is installed (or every one is incompatible).
    """
    _INCOMPATIBLE.clear()
    for ep in _iter_data_entry_points():
        try:
            provider = ep.load()
        except Exception:  # pragma: no cover - a broken feed must not crash the runtime
            continue
        api = int(getattr(provider, "DATA_API_VERSION", 1))
        version = getattr(provider, "DATA_PACKAGE_VERSION", None) or _entry_point_version(ep)
        if api != SUPPORTED_DATA_API_VERSION:
            _INCOMPATIBLE.append((str(version), provider))
            continue
        return provider, (str(version) if version is not None else None)
    return None, None


def data_package_version() -> str | None:
    """Version of the installed, *compatible* ``medecon-verify-data`` feed.

    Returns ``None`` when no compatible data package is installed (the bundled
    free tier is in use). Used to name the current reference data in strict-mode
    error messages.
    """
    _provider, version = _load_provider()
    return version


def has_data_package() -> bool:
    """True when a compatible ``medecon-verify-data`` feed is installed."""
    provider, _version = _load_provider()
    return provider is not None


def require_compatible_data_package() -> None:
    """Raise if a data package is installed but its provider API is unsupported.

    A no-op when no data package is installed (bundled tier) or when the
    installed one is compatible.
    """
    provider, _version = _load_provider()
    if provider is None and _INCOMPATIBLE:
        versions = ", ".join(v or "unknown" for v, _p in _INCOMPATIBLE)
        raise DataPackageIncompatible(
            "installed 'medecon-verify-data' provider(s) advertise an unsupported "
            f"DATA_API_VERSION (found version(s): {versions}); this runtime supports "
            f"DATA_API_VERSION == {SUPPORTED_DATA_API_VERSION}. Upgrade medecon-verify "
            "to a release compatible with the installed data feed."
        )


def _bundled_registry(name: str) -> dict | None:
    """Load a registry from the bundled free tier, or None if absent."""
    anchor = resources.files(_BUNDLED_ANCHOR)
    resource = anchor
    for part in _BUNDLED_SUBDIR:
        resource = resource / part
    resource = resource / f"{name}.json"
    if not resource.is_file():
        return None
    with resource.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_registry(name: str) -> dict:
    """Load a named vintage registry through the precedence chain.

    ``name`` is a bare registry key: ``"icd10cm_fy"``, ``"hcpcs_quarter"``,
    ``"ms_drg"``, ``"cms_hcc"``, or ``"hedis"``. Returns a fresh (deep-copied)
    dict the caller may mutate freely.

    Raises :class:`RegistryNotFound` if neither the installed feed nor the
    bundled tier carries it.
    """
    provider, _version = _load_provider()
    if provider is not None:
        get: Callable[[str], dict | None] | None = getattr(provider, "get_registry", None)
        if get is not None:
            supplied = get(name)
            if supplied is not None:
                return deepcopy(supplied)

    bundled = _bundled_registry(name)
    if bundled is not None:
        return bundled

    raise RegistryNotFound(
        f"no registry '{name}' available: no installed 'medecon-verify-data' feed "
        "carries it and no bundled free-tier file "
        f"'{'/'.join(_BUNDLED_SUBDIR)}/{name}.json' was found in the package."
    )


def registry_source(name: str) -> str:
    """Return which tier serves ``name``: ``"installed"`` or ``"bundled"``.

    Raises :class:`RegistryNotFound` when neither tier carries it.
    """
    provider, _version = _load_provider()
    if provider is not None:
        get = getattr(provider, "get_registry", None)
        if get is not None and get(name) is not None:
            return "installed"
    if _bundled_registry(name) is not None:
        return "bundled"
    raise RegistryNotFound(name)


__all__ = [
    "DATA_ENTRY_POINT_GROUP",
    "SUPPORTED_DATA_API_VERSION",
    "RegistryNotFound",
    "DataPackageIncompatible",
    "load_registry",
    "registry_source",
    "data_package_version",
    "has_data_package",
    "require_compatible_data_package",
]
