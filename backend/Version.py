"""Simple version tracking utility for TAIC smart assistant.

Reads version from pyproject.toml and provides compatibility checking.
"""

import os
import re
from pathlib import Path


def get_current_version() -> str:
    """Get current version from pyproject.toml.

    Returns:
        The current version string from pyproject.toml, or a default if missing.
    """
    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"

    with pyproject_path.open() as f:
        content = f.read()

    version_match = re.search(r'version\s*=\s*"([^"]+)"', content)
    if version_match:
        return version_match.group(1)
    return "0.1.0"


def parse_version(version: str) -> tuple[int, int, int]:
    """Parse semantic version string into (major, minor, patch).

    Args:
        version: A string in the format "major.minor.patch".

    Returns:
        A tuple containing the major, minor, and patch version numbers.
    """
    try:
        parts = version.split(".")
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, IndexError):
        return (0, 1, 0)


def is_compatible(
    stored_version: str,
    current_version: str | None = None,
) -> tuple[bool, str]:
    """Check if stored version is compatible with current version.

    Rules:
    - No version = incompatible (error)
    - Major version differences = incompatible
    - Minor/patch differences = compatible but warn

    Args:
        stored_version: The stored version string to compare against.
        current_version: The current version string to compare against. If None, it will be read from pyproject.toml.

    Returns:
        A tuple of (is_compatible, message) indicating compatibility status and details.
    """
    if current_version is None:
        current_version = get_current_version()

    if not stored_version:
        return (
            False,
            "No version information found. This data was created before version tracking was implemented. Please migrate or recreate this data.",
        )

    stored_major, stored_minor, stored_patch = parse_version(stored_version)
    current_major, current_minor, current_patch = parse_version(current_version)

    if stored_major != current_major:
        return (
            False,
            f"Incompatible version: stored={stored_version}, current={current_version}. Major version differences may cause issues.",
        )

    if stored_minor != current_minor or stored_patch != current_patch:
        return (
            True,
            f"Version difference detected: stored={stored_version}, current={current_version}. Data should load but may have minor compatibility issues.",
        )

    return True, ""


# Module-level constants
CURRENT_VERSION = get_current_version()


def get_deployment_slot() -> str:
    """Get the current Azure App Service deployment slot name.

    App Service automatically sets WEBSITE_SLOT_NAME for every slot
    ("production" for the main site, otherwise the slot name). The
    DEPLOYMENT_SLOT env var is honoured as an override (e.g. for local dev).

    Returns:
        The slot name in lowercase, or "production" if unknown.
    """
    slot = (
        os.getenv("DEPLOYMENT_SLOT") or os.getenv("WEBSITE_SLOT_NAME") or "production"
    )
    return slot.strip().lower()


def get_display_version() -> str:
    """Get the user-facing version string.

    Production shows the plain semver from pyproject.toml. Non-production
    slots (dev/staging/beta) show just the branch name and commit hash so
    every test deployment is uniquely identifiable.

    The suffix is driven purely by environment variables set on the slot, so
    this defaults to a clean production version and can never show a dev
    label in production unless the slot settings explicitly say otherwise.

    Returns:
        The version string to display to users.
    """
    slot = get_deployment_slot()
    if slot in {"production", ""}:
        return get_current_version()
    branch = os.getenv("GIT_BRANCH") or "unknown"
    commit = os.getenv("GIT_COMMIT") or os.getenv("COMMIT_HASH") or "unknown"
    return f"{branch}-{commit[:8]}"
