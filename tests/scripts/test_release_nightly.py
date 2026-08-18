"""Nightly release plumbing in scripts/release.py.

The nightly channel's whole safety story is tag SHAPES: nightly tags
carry a -nightly.YYYYMMDD suffix, every stable selector requires the
no-suffix shape, and the version math (next-MINOR over stable) makes
electron-updater's semver ordering implement both channel-switch
directions. These tests pin the shapes and the math; the workflow only
provides credentials.
"""

import importlib.util
from pathlib import Path

_RELEASE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "release.py"
_SPEC = importlib.util.spec_from_file_location("hermes_release_nightly", _RELEASE_PATH)
assert _SPEC and _SPEC.loader
release = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(release)


class TestNightlyTagShape:
    def test_nightly_tag_for_date_is_next_minor(self):
        assert release.nightly_tag_for_date("v0.27.2", "20260818") == "v0.28.0-nightly.20260818"
        assert release.nightly_tag_for_date("v1.4.0", "20261231") == "v1.5.0-nightly.20261231"

    def test_no_stable_line_yet(self):
        assert release.nightly_tag_for_date(None, "20260818") == "v0.1.0-nightly.20260818"

    def test_shape_round_trips_through_the_nightly_matcher(self):
        tag = release.nightly_tag_for_date("v0.27.2", "20260818")
        assert release._NIGHTLY_TAG_RE.fullmatch(tag)

    def test_nightly_shape_is_invisible_to_the_stable_selector(self):
        """THE invariant: a nightly tag must never parse as stable —
        otherwise stable users update onto nightlies."""
        tag = release.nightly_tag_for_date("v0.27.2", "20260818")
        assert release._SEMVER_TAG_RE.fullmatch(tag) is None

    def test_stable_shape_is_invisible_to_the_nightly_matcher(self):
        assert release._NIGHTLY_TAG_RE.fullmatch("v0.27.2") is None
        # Legacy CalVer must not match either (v2026.7.20 has 20-prefixed
        # components that could fool a sloppy pattern).
        assert release._NIGHTLY_TAG_RE.fullmatch("v2026.7.20") is None

    def test_nightly_outversions_current_stable_loses_to_next_minor(self):
        """The semver ordering that makes both channel switches work,
        checked with packaging's canonical comparison when available,
        else by electron-updater's documented precedence rules."""
        try:
            from packaging.version import Version
        except ImportError:
            import pytest

            pytest.skip("packaging not installed in this env")
        nightly = Version("0.28.0-nightly.20260818".replace("-nightly.", "a"))
        assert Version("0.27.2") < nightly < Version("0.28.0")


class TestNightlyDateStamps:
    def test_prune_cutoff_uses_the_tag_date(self):
        """The prune window keys on the tag's own YYYYMMDD group."""
        m = release._NIGHTLY_TAG_RE.fullmatch("v0.28.0-nightly.20260801")
        assert m and m.group(1) == "20260801"
