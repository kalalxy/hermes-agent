"""A session-scoped provisioned toolchain for tests that need real tools.

WHY THIS EXISTS

``_hermetic_environment`` gives every test a fresh empty
``HERMES_INSTALL_ROOT``, so ``installation.nodejs.node_path()`` and its
siblings raise ``NotProvisioned``. That is the correct default: it keeps
a test that asserts unprovisioned behaviour honest, and it stops a test
writing into the developer's working tree.

It also means a test that wants to exercise a REAL managed tool has no
way to get one, so those tests mock the resolver and assert against the
mock. A mocked resolver cannot catch a resolver that returns the wrong
path.

This fixture provisions the pinned toolchain ONCE per session into a
shared readonly store, then points a per-test install root at it.

MEASURED COST (spike, this host, 2026-08-18)

* cold, empty store: 6.9s, 322 MB downloaded
* warm, store already populated, fresh install root: 0.2s ("adopted")
* repeat, same install root: 0.1s ("kept")

The plan gates this design at 60s cold. 6.9s clears it by 9x, and every
run after the first in a session pays 0.2s. On CI the 322 MB store is
the cache unit, keyed on ``hashFiles('installation/runtime-pins.json')``
— the pins are exactly what decides the store contents.

USAGE

    def test_something(managed_deps):
        from installation import nodejs
        assert nodejs.node_path().exists()

OPT-OUT

Mark a test ``@pytest.mark.no_managed_deps`` when it must see an
unprovisioned tree. The marker is what
``tests/gateway/test_whatsapp_not_provisioned.py`` relies on, and any
other test that asserts the NotProvisioned contract must use it rather
than assuming the default.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Env var a CI job sets to a warm, cached store. When it is present the
#: session provision is an "adopt" of bytes that already exist rather
#: than a download.
STORE_ENV = "HERMES_TEST_TOOL_STORE"


def _provision(
    install_root: Path, store_home: Path
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["HERMES_INSTALL_ROOT"] = str(install_root)
    env["HERMES_HOME"] = str(store_home)
    # A packaged runtime dir (the nix devshell exports one that lives in
    # the read-only store) would make the provisioner write into a
    # read-only path and fail instantly. The session store is the whole
    # point here, so drop the override.
    env.pop("HERMES_RUNTIME_DIR", None)
    return subprocess.run(
        [sys.executable, "-m", "installation.provisioner"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=1800,
    )


@pytest.fixture(scope="session")
def managed_deps_store() -> Iterator[Path]:
    """Provision the pinned toolchain once, return the store home.

    Skips rather than fails when provisioning cannot run (no network on
    a sandboxed builder). A skipped test states that it did not check
    something; a passing mock-only test claims it did.
    """
    cached = os.environ.get(STORE_ENV, "").strip()
    if cached:
        store_home = Path(cached)
        store_home.mkdir(parents=True, exist_ok=True)
        cleanup = False
    else:
        store_home = Path(tempfile.mkdtemp(prefix="hermes-test-tool-store-"))
        cleanup = True

    warmup_root = Path(tempfile.mkdtemp(prefix="hermes-test-warmup-root-"))
    try:
        result: subprocess.CompletedProcess[str] = _provision(
            warmup_root, store_home
        )
    except subprocess.TimeoutExpired:
        shutil.rmtree(warmup_root, ignore_errors=True)
        pytest.skip("provisioning the managed toolchain timed out")
        raise  # unreachable: pytest.skip raises. Keeps `result` bound.

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-400:]
        shutil.rmtree(warmup_root, ignore_errors=True)
        pytest.skip(f"cannot provision the managed toolchain here: {detail}")

    yield store_home

    shutil.rmtree(warmup_root, ignore_errors=True)
    if cleanup:
        shutil.rmtree(store_home, ignore_errors=True)


@pytest.fixture
def managed_deps(
    managed_deps_store: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Point this test at the session toolchain. Returns the install root.

    The store is shared, so the bytes are adopted rather than
    downloaded. The FACTS stay per-test: which tools an install claims
    is install-scoped state, and a test that rewrites them must not
    leak that into the next test.
    """
    install_root = tmp_path / "managed_install"
    install_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HERMES_INSTALL_ROOT", str(install_root))
    monkeypatch.setenv("HERMES_HOME", str(managed_deps_store))
    monkeypatch.delenv("HERMES_RUNTIME_DIR", raising=False)

    result: subprocess.CompletedProcess[str] = _provision(
        install_root, managed_deps_store
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-400:]
        pytest.skip(f"cannot adopt the managed toolchain here: {detail}")

    # installation.registry caches nothing across processes, but modules
    # already imported in THIS process may hold a resolved path from the
    # unprovisioned default. Re-read the facts so lookups see the store.
    import installation.registry as registry

    if hasattr(registry, "load_facts"):
        registry.load_facts(install_root / ".hermes-runtime")

    return install_root
