"""Photon runs node and npm from the managed toolchain, never from PATH.

Every Hermes install provisions the pinned Node before any code runs, so the
sidecar has exactly one correct interpreter: the one ``installation.nodejs``
names. A PATH lookup here would spawn whatever node the host happens to carry,
at whatever version, and would find nothing at all on a managed-only box.

These tests pin the contract at the two places that spawn: the sidecar start
and the sidecar dependency install.
"""
from __future__ import annotations

import types
from pathlib import Path
from typing import Any, Dict, List

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.photon import adapter as photon_adapter
from plugins.platforms.photon import cli as cli_mod
from plugins.platforms.photon.adapter import PhotonAdapter


def _make_adapter(monkeypatch: pytest.MonkeyPatch) -> PhotonAdapter:
    monkeypatch.setenv("PHOTON_PROJECT_ID", "test-project-id")
    monkeypatch.setenv("PHOTON_PROJECT_SECRET", "test-project-secret")
    cfg = PlatformConfig(enabled=True, token="", extra={})
    return PhotonAdapter(cfg)


class _HealthyClient:
    """Fake httpx.AsyncClient whose /healthz probe answers 200."""

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> "_HealthyClient":
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def post(self, *a: Any, **k: Any) -> Any:
        return types.SimpleNamespace(status_code=200)


class _FakeProc:
    pid = 4242
    stdin = None
    stdout = None

    @staticmethod
    def poll() -> None:
        return None


def _stub_start_sidecar_surroundings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Dict[str, Any]:
    """Stub everything ``_start_sidecar`` touches except the spawns."""
    (tmp_path / "node_modules" / "spectrum-ts").mkdir(parents=True)
    monkeypatch.setattr(photon_adapter, "_SIDECAR_DIR", tmp_path)
    monkeypatch.setattr(photon_adapter, "_sidecar_deps_stale", lambda: False)

    async def _no_reap(self: PhotonAdapter) -> None:
        return None

    async def _no_supervise(self: PhotonAdapter, proc: Any) -> None:
        return None

    monkeypatch.setattr(PhotonAdapter, "_reap_stale_sidecar", _no_reap)
    monkeypatch.setattr(PhotonAdapter, "_supervise_sidecar", _no_supervise)
    monkeypatch.setattr(photon_adapter.httpx, "AsyncClient", _HealthyClient)

    seen: Dict[str, Any] = {}

    def _fake_run(cmd: List[str], **kwargs: Any) -> Any:
        seen["patch_cmd"] = cmd
        seen["patch_env"] = kwargs.get("env")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    def _fake_popen(cmd: List[str], **kwargs: Any) -> _FakeProc:
        seen["spawn_cmd"] = cmd
        seen["spawn_env"] = kwargs.get("env")
        return _FakeProc()

    monkeypatch.setattr(photon_adapter.subprocess, "run", _fake_run)
    monkeypatch.setattr(photon_adapter.subprocess, "Popen", _fake_popen)
    return seen


@pytest.mark.asyncio
async def test_start_sidecar_spawns_the_managed_node(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    managed_node_toolchain: Path,
) -> None:
    """Both node spawns name the managed binary by absolute path."""
    adapter = _make_adapter(monkeypatch)
    seen = _stub_start_sidecar_surroundings(monkeypatch, tmp_path)

    await adapter._start_sidecar()

    managed_node = str(managed_node_toolchain / "node")
    assert seen["spawn_cmd"][0] == managed_node
    assert seen["patch_cmd"][0] == managed_node


@pytest.mark.asyncio
async def test_start_sidecar_spawns_carry_the_managed_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    managed_node_toolchain: Path,
) -> None:
    """Both node spawns build their env from ``with_managed_runtimes``.

    The sidecar shells out to npm for its own postinstall work, and npm's shim
    is ``#!/usr/bin/env node``. An env assembled any other way resolves a
    different interpreter than the one the parent spawned. The stub below
    marks the managed env, because the real managed dirs are empty on a
    hermetic test HERMES_HOME and an assertion over them would prove nothing.
    """
    adapter = _make_adapter(monkeypatch)
    seen = _stub_start_sidecar_surroundings(monkeypatch, tmp_path)
    monkeypatch.setattr(
        photon_adapter.runtime_env,
        "with_managed_runtimes",
        lambda *a, **k: {"HERMES_MANAGED_ENV_MARKER": "1"},
    )

    await adapter._start_sidecar()

    for key in ("spawn_env", "patch_env"):
        env = seen[key]
        assert env is not None, f"{key} must be passed explicitly, not inherited"
        assert env.get("HERMES_MANAGED_ENV_MARKER") == "1", (
            f"{key} did not come from with_managed_runtimes"
        )


@pytest.mark.asyncio
async def test_start_sidecar_refuses_when_node_is_unprovisioned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A damaged runtime dir fails loud and non-retryable at the spawn path.

    check_requirements() answers False for the same condition, but by the time
    the user reached _start_sidecar they asked for Photon, so silence would
    strand them. A retry cannot restore a missing binary either.
    """
    adapter = _make_adapter(monkeypatch)
    _stub_start_sidecar_surroundings(monkeypatch, tmp_path)

    def _unprovisioned() -> Path:
        raise photon_adapter.nodejs.NotProvisioned("node is not in this runtime dir")

    monkeypatch.setattr(photon_adapter.nodejs, "node_path", _unprovisioned)

    with pytest.raises(photon_adapter.PhotonSidecarStartupError) as excinfo:
        await adapter._start_sidecar()

    assert excinfo.value.retryable is False


def test_install_sidecar_runs_the_managed_npm(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    managed_node_toolchain: Path,
) -> None:
    """`hermes photon install-sidecar` runs the pinned npm on the managed PATH."""
    seen: Dict[str, Any] = {}

    def _fake_run(cmd: List[str], **kwargs: Any) -> Any:
        seen["cmd"] = cmd
        seen["env"] = kwargs.get("env")
        return types.SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(cli_mod.subprocess, "run", _fake_run)
    monkeypatch.setattr(cli_mod, "_NPM_ERROR_LOG", tmp_path / ".photon-npm-error.log")
    monkeypatch.setattr(
        cli_mod.runtime_env,
        "with_managed_runtimes",
        lambda *a, **k: {"HERMES_MANAGED_ENV_MARKER": "1"},
    )

    assert cli_mod._install_sidecar() == 0

    assert seen["cmd"][0] == str(managed_node_toolchain / "npm")
    env = seen["env"]
    assert env is not None, "the npm run must pass an env, not inherit one"
    assert env.get("HERMES_MANAGED_ENV_MARKER") == "1", (
        "the npm env did not come from with_managed_runtimes"
    )


def test_reinstall_sidecar_deps_runs_the_managed_npm(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    managed_node_toolchain: Path,
) -> None:
    """The connect-time self-heal runs the pinned npm on the managed env.

    This path fires on hosted images where the user has no CLI, so it has the
    same one-correct-npm constraint as the explicit install command.
    """
    seen: Dict[str, Any] = {}

    def _fake_run(cmd: List[str], **kwargs: Any) -> Any:
        seen["cmd"] = cmd
        seen["env"] = kwargs.get("env")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(photon_adapter, "_SIDECAR_DIR", tmp_path)
    monkeypatch.setattr(photon_adapter.subprocess, "run", _fake_run)
    monkeypatch.setattr(
        photon_adapter.runtime_env,
        "with_managed_runtimes",
        lambda *a, **k: {"HERMES_MANAGED_ENV_MARKER": "1"},
    )

    photon_adapter._reinstall_sidecar_deps()

    assert seen["cmd"][0] == str(managed_node_toolchain / "npm")
    env = seen["env"]
    assert env is not None, "the npm run must pass an env, not inherit one"
    assert env.get("HERMES_MANAGED_ENV_MARKER") == "1", (
        "the npm env did not come from with_managed_runtimes"
    )


def test_reinstall_sidecar_deps_skips_when_npm_is_unprovisioned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A damaged runtime dir leaves the stale deps alone instead of running
    a stray npm. This path is best-effort, so it warns and returns."""

    def _unprovisioned() -> Path:
        raise photon_adapter.nodejs.NotProvisioned("npm is not in this runtime dir")

    monkeypatch.setattr(photon_adapter.nodejs, "npm_path", _unprovisioned)

    def _must_not_run(*a: Any, **k: Any) -> Any:
        raise AssertionError("npm must not run when the toolchain is unprovisioned")

    monkeypatch.setattr(photon_adapter.subprocess, "run", _must_not_run)

    photon_adapter._reinstall_sidecar_deps()


def test_install_sidecar_refuses_when_npm_is_unprovisioned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A damaged runtime dir exits non-zero instead of running a stray npm."""

    def _unprovisioned() -> Path:
        raise cli_mod.nodejs.NotProvisioned("npm is not in this runtime dir")

    monkeypatch.setattr(cli_mod.nodejs, "npm_path", _unprovisioned)

    def _must_not_run(*a: Any, **k: Any) -> Any:
        raise AssertionError("npm must not run when the toolchain is unprovisioned")

    monkeypatch.setattr(cli_mod.subprocess, "run", _must_not_run)

    assert cli_mod._install_sidecar() == 1
