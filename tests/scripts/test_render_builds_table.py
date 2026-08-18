"""render-builds-table.py: tables from REAL asset names, spliced idempotently.

The contract: table rows exist only for assets that are actually on the
release (missing artifact = missing row, never a dead link), msix / zip /
feed manifests stay out, and re-rendering replaces the previous block
instead of stacking a second one.
"""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "render-builds-table.py"
_SPEC = importlib.util.spec_from_file_location("render_builds_table", _SCRIPT)
assert _SPEC and _SPEC.loader
rbt = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rbt)


ASSETS = [
    "Hermes-0.28.0-mac-arm64.dmg",
    "Hermes-0.28.0-mac-arm64.zip",          # updater delta target — no row
    "Hermes-0.28.0-win-x64.exe",
    "Hermes-0.28.0-win-arm64.exe",
    "Hermes-0.28.0-win-x64.msix",           # store channel — no row
    "Hermes-0.28.0-linux-x64.AppImage",
    "HermesLight-0.28.0-win-x64.exe",
    "latest.yml",                            # feed manifest — no row
    "light.yml",
    "Hermes-0.28.0-win-x64.exe.blockmap",   # no row
]


class TestParseAssets:
    def test_only_table_shaped_assets_parse(self):
        parsed = rbt.parse_assets(ASSETS)
        assert ("mac", "arm64") in parsed["Hermes"]
        assert ("win", "x64") in parsed["Hermes"]
        assert ("win", "arm64") in parsed["Hermes"]
        assert ("linux", "x64") in parsed["Hermes"]
        assert len(parsed["Hermes"]) == 4          # zip/msix/blockmap/yml excluded
        assert parsed["HermesLight"] == {("win", "x64"): ("HermesLight-0.28.0-win-x64.exe", "exe")}

    def test_nightly_versions_parse(self):
        parsed = rbt.parse_assets(["Hermes-0.28.0-nightly.20260818-win-x64.exe"])
        assert ("win", "x64") in parsed["Hermes"]


class TestRenderAndSplice:
    def test_rows_only_for_present_assets(self):
        block = rbt.render_tables(rbt.parse_assets(ASSETS), "v0.28.0", "NousResearch/hermes-agent")
        assert "Hermes-0.28.0-mac-arm64.dmg" in block
        assert "msix" not in block
        assert ".zip" not in block
        # A leg that never uploaded leaves no row at all.
        assert "linux-arm64" not in block

    def test_splice_replaces_marker_and_is_idempotent(self):
        body = f"# Notes\n\n{rbt.MARKER}\n\n## Changes"
        block = rbt.render_tables(rbt.parse_assets(ASSETS), "v0.28.0", "NousResearch/hermes-agent")
        once = rbt.splice(body, block)
        assert "## Downloads" in once
        assert once.count(rbt.MARKER) == 1
        # Second render (e.g. a re-run with more assets) replaces, not stacks.
        twice = rbt.splice(once, block)
        assert twice.count("## Downloads") == 1
        assert twice.count(rbt.END_MARKER) == 1

    def test_no_marker_leaves_body_alone(self):
        block = rbt.render_tables(rbt.parse_assets(ASSETS), "v0.28.0", "NousResearch/hermes-agent")
        assert rbt.splice("no marker here", block) == "no marker here"
