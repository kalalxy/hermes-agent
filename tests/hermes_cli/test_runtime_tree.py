"""Tests for installation/tree.py — tree classification and channel.

The ladder is stamp-pure: ``.git`` decides checkout vs sealed, and the
stamp's required ``updateMechanism`` decides managed vs working tree. No
path table anywhere in classification (the blessed-root table lives only
in the one-time adoption step, tested in test_post_update_adoption.py).
"""

import json

import pytest

from installation.tree import (
    STEWARD_UPDATE_MESSAGES,
    UPDATE_MECHANISMS,
    GitCheckout,
    Sealed,
    install_method,
    runtime_tree,
    steward_update_message,
)


def _stamp(root, mechanism="external", distribution=None, **extra):
    fields = {
        "schemaVersion": 2,
        "commit": "a" * 40,
        "updateMechanism": mechanism,
        "distribution": distribution,
    }
    fields.update(extra)
    (root / "install-stamp.json").write_text(json.dumps(fields))


class TestRuntimeTree:
    def test_a_tree_with_git_is_a_checkout(self, tmp_path):
        (tmp_path / ".git").mkdir()
        tree = runtime_tree(tmp_path)
        assert isinstance(tree, GitCheckout)
        assert tree.root == tmp_path

    def test_a_worktree_gitfile_also_counts(self, tmp_path):
        # Linked worktrees and submodules have a .git FILE, not a directory.
        (tmp_path / ".git").write_text("gitdir: /somewhere/else\n")
        assert isinstance(runtime_tree(tmp_path), GitCheckout)

    def test_a_gitless_tree_is_sealed_with_the_stamped_steward(self, tmp_path):
        _stamp(tmp_path, mechanism="electron-updater", distribution="desktop-app")
        tree = runtime_tree(tmp_path)
        assert isinstance(tree, Sealed)
        assert tree.steward == "desktop-app"

    def test_a_gitless_tree_without_a_stamp_is_sealed_unknown(self, tmp_path):
        tree = runtime_tree(tmp_path)
        assert isinstance(tree, Sealed)
        assert tree.steward == "unknown"

    def test_a_corrupt_stamp_degrades_to_unknown(self, tmp_path):
        (tmp_path / "install-stamp.json").write_text("{not json")
        tree = runtime_tree(tmp_path)
        assert isinstance(tree, Sealed)
        assert tree.steward == "unknown"

    def test_a_stamp_without_a_mechanism_hard_fails(self, tmp_path):
        """Missing updateMechanism is a build-lane bug — loud, not quiet."""
        (tmp_path / "install-stamp.json").write_text(
            json.dumps({"commit": "a" * 40, "distribution": "docker"})
        )
        with pytest.raises(RuntimeError, match="updateMechanism"):
            runtime_tree(tmp_path)


class TestInstallMethodLadder:
    """Every combination of (.git, stamp, mechanism)."""

    def test_git_plus_self_stamp_is_managed(self, tmp_path):
        (tmp_path / ".git").mkdir()
        _stamp(tmp_path, mechanism="self")
        assert install_method(tmp_path) == "git"

    def test_git_without_stamp_is_a_working_tree(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert install_method(tmp_path) == "source"

    @pytest.mark.parametrize("mechanism", ["electron-updater", "external"])
    def test_git_with_artifact_stamp_is_a_working_tree(self, tmp_path, mechanism):
        """A stamp baked for an artifact does not make a checkout managed."""
        (tmp_path / ".git").mkdir()
        _stamp(tmp_path, mechanism=mechanism, distribution="desktop-app")
        assert install_method(tmp_path) == "source"

    @pytest.mark.parametrize(
        "distribution,mechanism",
        [
            ("docker", "external"),
            ("nix", "external"),
            ("desktop-app", "electron-updater"),
        ],
    )
    def test_stamp_without_git_is_sealed(self, tmp_path, distribution, mechanism):
        _stamp(tmp_path, mechanism=mechanism, distribution=distribution)
        assert install_method(tmp_path) == distribution

    def test_unknown_steward_reports_unknown(self, tmp_path):
        _stamp(tmp_path, mechanism="external", distribution="snap")
        assert install_method(tmp_path) == "unknown"

    def test_neither_is_unknown(self, tmp_path):
        assert install_method(tmp_path) == "unknown"

    def test_no_path_matching_anywhere(self, tmp_path, monkeypatch):
        """A stampless checkout at the blessed managed root is still a
        working tree: classification never consults the path table."""
        home = tmp_path / ".hermes"
        checkout = home / "hermes-agent"
        checkout.mkdir(parents=True)
        (checkout / ".git").mkdir()
        monkeypatch.setenv("HERMES_HOME", str(home))
        assert install_method(checkout) == "source"

    def test_mechanism_vocabulary_is_closed(self):
        assert UPDATE_MECHANISMS == ("self", "electron-updater", "external")


class TestStewardMessages:
    def test_every_known_steward_names_its_mechanism(self):
        # Behavior contract, not a copy snapshot: each refusal must name the
        # steward that owns updates for that tree, so the user knows where
        # to go. The exact wording is free to change.
        assert "desktop app" in steward_update_message("desktop-app")
        assert "docker pull" in steward_update_message("docker")
        assert "flake" in steward_update_message("nix")

    def test_an_unknown_steward_gets_the_fallback_with_its_name(self):
        message = steward_update_message("pacman")
        assert "pacman" in message
        assert "cannot update" in message

    def test_every_table_entry_is_a_refusal(self):
        for steward, message in STEWARD_UPDATE_MESSAGES.items():
            assert message.startswith("\u2717"), steward


class TestResolveUpdateChannel:
    """The channel resolver moved to hermes_cli.update_channel (per-install
    records) — these cases live in tests/hermes_cli/test_update_channel.py."""
