"""step_adopt_blessed_checkout — one-time birth certificate for shipped
stampless installs.

Blessed root + .git + no stamp → write {updateMechanism: self} atomically.
Anything else — untouched. A read-only tree fails soft (nix-like layouts).
"""
import json
import os
import stat
import sys

import pytest

from hermes_cli.post_update import step_adopt_blessed_checkout


@pytest.fixture
def blessed_checkout(tmp_path, monkeypatch):
    """A .git checkout at the blessed $HERMES_HOME/hermes-agent root."""
    home = tmp_path / ".hermes"
    root = home / "hermes-agent"
    root.mkdir(parents=True)
    (root / ".git").mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("installation.paths.get_install_root", lambda: root)
    return root


def test_adopts_blessed_stampless_checkout(blessed_checkout):
    result = step_adopt_blessed_checkout()
    assert result["ok"] is True
    assert result.get("adopted") == str(blessed_checkout)
    stamp = json.loads((blessed_checkout / "install-stamp.json").read_text())
    assert stamp["updateMechanism"] == "self"
    assert stamp["source"] == "adoption"


def test_adoption_is_idempotent(blessed_checkout):
    """Second run: the stamp already exists, nothing rewritten."""
    step_adopt_blessed_checkout()
    first = (blessed_checkout / "install-stamp.json").read_text()
    result = step_adopt_blessed_checkout()
    assert result.get("skipped") == "already-stamped"
    assert (blessed_checkout / "install-stamp.json").read_text() == first


def test_existing_stamp_is_never_touched(blessed_checkout):
    """Any pre-existing stamp wins — adoption must not clobber richer
    provenance (an installer-written or build-baked stamp)."""
    original = json.dumps({"schemaVersion": 2, "updateMechanism": "self", "source": "installer"})
    (blessed_checkout / "install-stamp.json").write_text(original)
    result = step_adopt_blessed_checkout()
    assert result.get("skipped") == "already-stamped"
    assert (blessed_checkout / "install-stamp.json").read_text() == original


def test_non_blessed_checkout_untouched(tmp_path, monkeypatch):
    """.git anywhere else → never adopted."""
    home = tmp_path / ".hermes"
    home.mkdir()
    dev = tmp_path / "src" / "hermes-agent"
    dev.mkdir(parents=True)
    (dev / ".git").mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("installation.paths.get_install_root", lambda: dev)
    result = step_adopt_blessed_checkout()
    assert result.get("skipped") == "not-a-blessed-root"
    assert not (dev / "install-stamp.json").exists()


def test_sealed_tree_untouched(tmp_path, monkeypatch):
    """No .git → not a checkout, nothing to adopt (sealed trees always
    ship stamps anyway)."""
    home = tmp_path / ".hermes"
    root = home / "hermes-agent"
    root.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("installation.paths.get_install_root", lambda: root)
    result = step_adopt_blessed_checkout()
    assert result.get("skipped") == "not-a-checkout"
    assert not (root / "install-stamp.json").exists()


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="chmod-based read-only dirs are not enforceable on Windows or as root",
)
def test_read_only_tree_fails_soft(blessed_checkout):
    """nix-like read-only tree: debug-log skip, never a crash."""
    mode = stat.S_IMODE(os.stat(blessed_checkout).st_mode)
    os.chmod(blessed_checkout, stat.S_IRUSR | stat.S_IXUSR)
    try:
        result = step_adopt_blessed_checkout()
        assert result["ok"] is True
        assert str(result.get("skipped", "")).startswith("unwritable")
    finally:
        os.chmod(blessed_checkout, mode)


def test_adopted_checkout_classifies_as_git(blessed_checkout):
    """End to end: adoption flips the ladder verdict source → git."""
    from installation.tree import install_method

    assert install_method(blessed_checkout) == "source"
    step_adopt_blessed_checkout()
    assert install_method(blessed_checkout) == "git"
