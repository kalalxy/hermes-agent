"""Doctor reports the node and npm that Hermes would actually run.

Hermes runs the pinned node and npm from this install's runtime dir. Nothing
puts that dir on an interactive shell's PATH, so a PATH-only probe reports
"Node.js not found" on a perfectly healthy managed install, and silently
skips the npm audit that depends on it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hermes_cli import doctor
from installation import nodejs


def test_managed_node_wins_over_a_system_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The pinned binary is what Hermes runs, so it is what doctor reports."""
    managed = tmp_path / "managed" / "node"
    managed.parent.mkdir(parents=True)
    managed.write_text("", encoding="utf-8")
    monkeypatch.setattr(nodejs, "node_path", lambda: managed)
    monkeypatch.setattr(doctor, "_safe_which", lambda _cmd: "/usr/bin/node")

    resolved, source = doctor._managed_node_tool("node")

    assert resolved == str(managed)
    assert source == "managed"


def test_managed_npm_wins_over_a_system_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """npm resolves through its own pinned entry, not through node's."""
    managed = tmp_path / "managed" / "npm"
    managed.parent.mkdir(parents=True)
    managed.write_text("", encoding="utf-8")
    monkeypatch.setattr(nodejs, "npm_path", lambda: managed)
    monkeypatch.setattr(doctor, "_safe_which", lambda _cmd: "/usr/bin/npm")

    resolved, source = doctor._managed_node_tool("npm")

    assert resolved == str(managed)
    assert source == "managed"


def test_a_managed_install_is_not_reported_as_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The bug this replaced: managed node present, nothing on PATH.

    A PATH-only probe called this "not found" and skipped the browser-tool
    and npm-audit checks on an install that runs both perfectly well.
    """
    managed = tmp_path / "managed" / "node"
    managed.parent.mkdir(parents=True)
    managed.write_text("", encoding="utf-8")
    monkeypatch.setattr(nodejs, "node_path", lambda: managed)
    monkeypatch.setattr(doctor, "_safe_which", lambda _cmd: None)

    resolved, _ = doctor._managed_node_tool("node")

    assert resolved is not None


def test_system_copy_is_still_reported_when_unprovisioned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doctor reports what is on the machine, so PATH stays the second rung."""

    def _unprovisioned() -> Path:
        raise nodejs.NotProvisioned("node is not in this runtime dir")

    monkeypatch.setattr(nodejs, "node_path", _unprovisioned)
    monkeypatch.setattr(doctor, "_safe_which", lambda _cmd: "/usr/bin/node")

    resolved, source = doctor._managed_node_tool("node")

    assert resolved == "/usr/bin/node"
    assert source == "system"


def test_absent_everywhere_reports_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """No managed tool and nothing on PATH is a real "not found"."""

    def _unprovisioned() -> Path:
        raise nodejs.NotProvisioned("node is not in this runtime dir")

    monkeypatch.setattr(nodejs, "node_path", _unprovisioned)
    monkeypatch.setattr(doctor, "_safe_which", lambda _cmd: None)

    resolved, _ = doctor._managed_node_tool("node")

    assert resolved is None
