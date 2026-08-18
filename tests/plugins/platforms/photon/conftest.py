"""Shared fixtures for the Photon adapter tests.

Photon resolves node and npm through ``installation.nodejs``, which answers
from this install's runtime facts and never from PATH. The test suite runs
against a hermetic temporary HERMES_HOME with no runtime dir, so that lookup
fails here even though every real install provisions the pinned tools before
any code runs. The fixture below restores that guarantee, and the few tests
about a damaged runtime dir override it with their own stub.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from plugins.platforms.photon import adapter as adapter_mod


@pytest.fixture(autouse=True)
def managed_node_toolchain(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    """Answer the managed node and npm lookups with a provisioned toolchain."""
    bin_dir = tmp_path_factory.mktemp("managed-node-bin")
    node_bin = bin_dir / "node"
    npm_bin = bin_dir / "npm"
    for binary in (node_bin, npm_bin):
        binary.write_text("", encoding="utf-8")
    monkeypatch.setattr(adapter_mod.nodejs, "node_path", lambda: node_bin)
    monkeypatch.setattr(adapter_mod.nodejs, "npm_path", lambda: npm_bin)
    return bin_dir
