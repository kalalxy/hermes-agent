"""_ensure_tui_node decides from the runtime facts, never from PATH.

The TUI runs on the pinned Node this install provisions. A PATH probe answers
"is there any node here", which is a different question: it says yes to a
system node of the wrong version and, by saying yes, skips the provisioning
that would have installed the right one. These tests pin the facts-based
contract, and the escape hatch that lets an operator refuse the install.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

import hermes_cli.main as main_mod
from installation import registry
from installation.provisioner import ToolResult


@pytest.fixture
def managed_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> List[Path]:
    """Give the managed toolchain a known, non-empty set of PATH dirs."""
    dirs = [tmp_path / "managed" / "node" / "bin", tmp_path / "managed" / "npm" / "bin"]
    for d in dirs:
        d.mkdir(parents=True)
    monkeypatch.setattr("installation.env.managed_path_dirs", lambda *a, **k: dirs)
    return dirs


def _record_provisions(monkeypatch: pytest.MonkeyPatch, ok: bool = True) -> List[str]:
    """Capture which tools the provisioner is asked for."""
    asked: List[str] = []

    def _provision(tool: str, *a: Any, **k: Any) -> ToolResult:
        asked.append(tool)
        return ToolResult(
            tool,
            "downloaded" if ok else "failed",
            detail=None if ok else "download refused",
        )

    monkeypatch.setattr("installation.provisioner.provision_tool", _provision)
    return asked


def _facts_say(monkeypatch: pytest.MonkeyPatch, present: Dict[str, bool]) -> None:
    """Answer tool_path() from *present* rather than a real runtime dir."""

    def _tool_path(name: str, *a: Any, **k: Any) -> Path | None:
        return Path(f"/managed/{name}") if present.get(name) else None

    monkeypatch.setattr(registry, "tool_path", _tool_path)


def test_provisions_when_the_facts_have_no_node(
    monkeypatch: pytest.MonkeyPatch, managed_dirs: List[Path]
) -> None:
    """An unprovisioned tree provisions, whatever PATH happens to hold."""
    _facts_say(monkeypatch, {})
    monkeypatch.delenv("HERMES_SKIP_NODE_BOOTSTRAP", raising=False)
    asked = _record_provisions(monkeypatch)

    main_mod._ensure_tui_node()

    # npm extends node, so asking for npm brings the whole chain up.
    assert asked == ["npm"]


def test_provisions_when_the_facts_have_node_but_no_npm(
    monkeypatch: pytest.MonkeyPatch, managed_dirs: List[Path]
) -> None:
    """A half-provisioned tree still provisions. npm is its own pinned tool."""
    _facts_say(monkeypatch, {"node": True})
    monkeypatch.delenv("HERMES_SKIP_NODE_BOOTSTRAP", raising=False)
    asked = _record_provisions(monkeypatch)

    main_mod._ensure_tui_node()

    assert asked == ["npm"]


def test_a_system_node_on_path_does_not_satisfy_the_gate(
    monkeypatch: pytest.MonkeyPatch, managed_dirs: List[Path], tmp_path: Path
) -> None:
    """A node on PATH is the wrong question and must not skip provisioning.

    This is the whole point of the pin: a system node answers a PATH probe at
    whatever version it happens to be, and the TUI needs the pinned one.
    """
    system_bin = tmp_path / "system-bin"
    system_bin.mkdir()
    for name in ("node", "npm"):
        binary = system_bin / name
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(system_bin))
    _facts_say(monkeypatch, {})
    monkeypatch.delenv("HERMES_SKIP_NODE_BOOTSTRAP", raising=False)
    asked = _record_provisions(monkeypatch)

    main_mod._ensure_tui_node()

    assert asked == ["npm"], "a system node on PATH suppressed the provisioning"


def test_provisioned_facts_skip_the_provisioner(
    monkeypatch: pytest.MonkeyPatch, managed_dirs: List[Path]
) -> None:
    """The normal case costs nothing: facts present, no provisioning."""
    _facts_say(monkeypatch, {"node": True, "npm": True})
    asked = _record_provisions(monkeypatch)

    main_mod._ensure_tui_node()

    assert asked == []


def test_skip_env_refuses_the_install(
    monkeypatch: pytest.MonkeyPatch, managed_dirs: List[Path]
) -> None:
    """HERMES_SKIP_NODE_BOOTSTRAP=1 stops the download."""
    _facts_say(monkeypatch, {})
    monkeypatch.setenv("HERMES_SKIP_NODE_BOOTSTRAP", "1")
    asked = _record_provisions(monkeypatch)

    main_mod._ensure_tui_node()

    assert asked == []


def test_managed_dirs_go_on_path_when_already_provisioned(
    monkeypatch: pytest.MonkeyPatch, managed_dirs: List[Path]
) -> None:
    """The TUI child inherits this PATH, so it must carry the managed dirs.

    The child and everything it spawns has to reach the same toolchain the
    parent resolved, which only happens through PATH.
    """
    _facts_say(monkeypatch, {"node": True, "npm": True})
    _record_provisions(monkeypatch)
    monkeypatch.setenv("PATH", "/usr/bin")

    main_mod._ensure_tui_node()

    entries = main_mod.os.environ["PATH"].split(main_mod.os.pathsep)
    for managed_dir in managed_dirs:
        assert str(managed_dir) in entries
    assert "/usr/bin" in entries, "the inherited PATH must survive"


def test_path_is_not_extended_when_provisioning_fails(
    monkeypatch: pytest.MonkeyPatch, managed_dirs: List[Path]
) -> None:
    """A failed provision reports and returns instead of promising a PATH.

    Adding dirs that hold no tools would turn a clear provisioning error into
    a confusing "command not found" further downstream.
    """
    _facts_say(monkeypatch, {})
    monkeypatch.delenv("HERMES_SKIP_NODE_BOOTSTRAP", raising=False)
    _record_provisions(monkeypatch, ok=False)
    monkeypatch.setenv("PATH", "/usr/bin")

    main_mod._ensure_tui_node()

    assert main_mod.os.environ["PATH"] == "/usr/bin"
