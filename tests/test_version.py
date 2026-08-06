"""Tests for the version tracking utility."""

import os
from unittest.mock import patch

import pytest

from backend import Version


class TestIsCompatible:
    """Tests for version compatibility checking."""

    @staticmethod
    def test_no_version_is_incompatible():
        """Test that an empty stored version is incompatible."""
        is_compatible, message = Version.is_compatible("", "0.8.1")
        assert not is_compatible
        assert "No version information" in message

    @staticmethod
    def test_identical_version_is_compatible():
        """Test that identical versions are compatible with no message."""
        is_compatible, message = Version.is_compatible("0.8.1", "0.8.1")
        assert is_compatible
        assert not message

    @staticmethod
    def test_major_difference_is_incompatible():
        """Test that major version differences are incompatible."""
        is_compatible, message = Version.is_compatible("1.0.0", "0.8.1")
        assert not is_compatible
        assert "Incompatible version" in message

    @staticmethod
    def test_minor_difference_warns():
        """Test that minor version differences warn but are compatible."""
        is_compatible, message = Version.is_compatible("0.7.0", "0.8.1")
        assert is_compatible
        assert "Version difference detected" in message

    @staticmethod
    def test_patch_difference_warns():
        """Test that patch version differences warn but are compatible."""
        is_compatible, message = Version.is_compatible("0.8.0", "0.8.1")
        assert is_compatible
        assert "Version difference detected" in message

    @staticmethod
    @pytest.mark.parametrize(
        "weird_version",
        [
            "clean-up-vector-db-download-path-9f3ab12c",
            "main-abc12345",
            "unknown-unknown",
            "0.8.1-rc1",
        ],
    )
    def test_weird_version_always_warns(weird_version):
        """Test that non-semver stored versions always warn."""
        is_compatible, message = Version.is_compatible(weird_version, "0.8.1")
        assert is_compatible
        assert "Weird version detected" in message


class TestGetDisplayVersion:
    """Tests for the user-facing version string."""

    @staticmethod
    @pytest.mark.parametrize(
        ("slot", "expected"),
        [
            ("production", "0.8.1"),
            ("staging", "0.8.1"),
            ("", "0.8.1"),
            ("dev", "feature-branch-9f3ab12c"),
            ("beta", "feature-branch-9f3ab12c"),
        ],
    )
    def test_display_version_by_slot(slot, expected):
        """Test which slots show the semver vs the branch/commit label."""
        with (
            patch("backend.Version.get_current_version", return_value="0.8.1"),
            patch.dict(
                os.environ,
                {
                    "DEPLOYMENT_SLOT": slot,
                    "GIT_BRANCH": "feature-branch",
                    "GIT_COMMIT": "9f3ab12cdeadbeef",
                },
                clear=False,
            ),
        ):
            assert Version.get_display_version() == expected

    @staticmethod
    def test_display_version_falls_back_to_branch_envs():
        """Test that missing env vars default to 'unknown'."""
        with (
            patch.dict(
                os.environ,
                {"DEPLOYMENT_SLOT": "dev", "GIT_BRANCH": "", "GIT_COMMIT": ""},
                clear=True,
            ),
            patch("backend.Version.get_current_version", return_value="0.8.1"),
        ):
            assert Version.get_display_version() == "unknown-unknown"


class TestIsValidSemver:
    """Tests for the semver format check."""

    @staticmethod
    @pytest.mark.parametrize(
        ("version", "expected"),
        [
            ("0.8.1", True),
            ("1.2.3", True),
            ("feature-branch-9f3ab12c", False),
            ("0.8.1-rc1", False),
            ("0.8", False),
            ("", False),
        ],
    )
    def test_is_valid_semver(version, expected):
        """Test semver detection."""
        assert Version.is_valid_semver(version) is expected
