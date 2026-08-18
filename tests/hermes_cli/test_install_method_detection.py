"""detect_install_method derives everything from the running code tree.

Stamp-pure ladder — no stored method flags, no path-matching: sealed trees
carry install-stamp.json (its ``distribution`` field names the steward),
git trees are classified by their stamp's ``updateMechanism`` — ``self``
means the installer created this checkout to be updated (``git``), no
stamp means somebody's working tree (``source``). $HERMES_HOME is never
consulted, so co-located installs sharing one data dir (host + Docker
gateway) cannot contaminate each other by construction.
"""
import json

import pytest


def _detect(project_root):
    from hermes_cli.config import detect_install_method

    return detect_install_method(project_root=project_root)


def _write_stamp(root, distribution, mechanism="external"):
    (root / "install-stamp.json").write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "commit": "a" * 40,
                "distribution": distribution,
                "updateMechanism": mechanism,
            }
        )
    )


@pytest.mark.parametrize(
    "distribution,mechanism",
    [("docker", "external"), ("nix", "external"), ("desktop-app", "electron-updater")],
)
def test_sealed_tree_reports_stamp_distribution(tmp_path, distribution, mechanism):
    _write_stamp(tmp_path, distribution, mechanism)
    assert _detect(tmp_path) == distribution


def test_unknown_steward_reports_unknown(tmp_path):
    """A newer package manager's stamp value must not leak into consumers."""
    _write_stamp(tmp_path, "snap")
    assert _detect(tmp_path) == "unknown"


def test_bare_tree_is_unknown(tmp_path):
    assert _detect(tmp_path) == "unknown"


def test_stamp_without_mechanism_hard_fails(tmp_path):
    """A mechanism-less stamp is a build-lane bug, not a soft 'unknown'."""
    (tmp_path / "install-stamp.json").write_text(
        json.dumps({"schemaVersion": 2, "commit": "a" * 40, "distribution": "docker"})
    )
    with pytest.raises(RuntimeError, match="updateMechanism"):
        _detect(tmp_path)


def test_git_with_self_stamp_is_git(tmp_path):
    """Installer-stamped (or adopted) checkout: `hermes update` owns it.
    Location does not matter — the stamp is the whole fact."""
    (tmp_path / ".git").mkdir()
    _write_stamp(tmp_path, None, "self")
    assert _detect(tmp_path) == "git"


def test_git_without_stamp_is_source(tmp_path):
    """A random clone / dev worktree is somebody's working tree, not the
    managed install — `hermes update` refuses it."""
    checkout = tmp_path / "src" / "hermes-agent"
    checkout.mkdir(parents=True)
    (checkout / ".git").mkdir()
    assert _detect(checkout) == "source"


def test_worktree_gitfile_is_a_checkout(tmp_path):
    """A linked worktree's .git is a FILE; it still classifies as a checkout."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /somewhere/.git/worktrees/wt\n")
    assert _detect(worktree) == "source"


def test_git_with_artifact_stamp_is_source(tmp_path):
    """.git + a stamp baked for an ARTIFACT (non-self mechanism) is a dev
    tree that ran a packaging script — never managed by `hermes update`."""
    (tmp_path / ".git").mkdir()
    _write_stamp(tmp_path, "docker", "external")
    assert _detect(tmp_path) == "source"


def test_location_no_longer_classifies(tmp_path, monkeypatch):
    """The managed-root path table is gone from the ladder: a stampless
    checkout AT the blessed root still reads ``source`` (the adoption
    step is what writes the stamp — classification never path-matches)."""
    home = tmp_path / ".hermes"
    checkout = home / "hermes-agent"
    checkout.mkdir(parents=True)
    (checkout / ".git").mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert _detect(checkout) == "source"


def test_home_scoped_state_is_ignored(tmp_path, monkeypatch):
    """Legacy $HERMES_HOME markers must not influence detection.

    Models the shared-home scenario (host install + Docker gateway
    bind-mounting ~/.hermes): whatever a co-located container left in the
    data dir, this tree's classification only reads this tree.
    """
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / ".install_method").write_text("docker\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    _write_stamp(tmp_path, "nix")
    assert _detect(tmp_path) == "nix"


def test_every_method_has_update_guidance(tmp_path):
    """Invariant: each derivable method maps to non-empty update guidance."""
    from hermes_cli.config import recommended_update_command_for_method

    for method in ("docker", "nix", "desktop-app", "git", "source", "unknown"):
        assert recommended_update_command_for_method(method).strip()
