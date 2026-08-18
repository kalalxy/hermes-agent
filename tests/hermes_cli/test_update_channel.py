"""Per-install update-channel records (hermes_cli/update_channel.py).

The manifest's replacement: channel is config, keyed by the install id
(sha16 of the canonical install-root path — boot_bootstrap._install_key),
never home-global. Mechanism comes from the stamp; external installs have
no channel at all.
"""
import json
from pathlib import Path

import pytest

from hermes_cli.update_channel import (
    CHANNEL_MAIN,
    CHANNEL_NIGHTLY,
    CHANNEL_STABLE,
    default_channel,
    install_id,
    resolve_update_channel,
    set_install_channel,
    stale_channel_records,
)


def _stamp(root: Path, mechanism: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "install-stamp.json").write_text(
        json.dumps({"schemaVersion": 2, "updateMechanism": mechanism})
    )


def _config_for(root: Path, channel: str) -> dict:
    return {
        "update": {
            "installs": {
                install_id(root): {"path": str(root), "channel": channel}
            }
        }
    }


class TestInstallId:
    def test_path_derived_and_stable(self, tmp_path):
        """The id hashes the canonical PATH — same path, same id, no matter
        what the tree contains (survives electron-updater artifact swaps)."""
        root = tmp_path / "install"
        root.mkdir()
        before = install_id(root)
        _stamp(root, "electron-updater")  # contents change...
        assert install_id(root) == before  # ...id does not

    def test_two_installs_two_ids(self, tmp_path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        assert install_id(a) != install_id(b)

    def test_matches_the_state_folder_key(self, tmp_path):
        from hermes_cli.boot_bootstrap import _install_key

        root = tmp_path / "install"
        root.mkdir()
        assert install_id(root) == _install_key(root)


class TestResolve:
    def test_per_install_record_wins(self, tmp_path):
        root = tmp_path / "install"
        _stamp(root, "self")
        config = _config_for(root, "stable")
        assert resolve_update_channel(config, root) == CHANNEL_STABLE

    def test_multi_install_isolation(self, tmp_path):
        """Two installs, one config: each resolves its own record and a
        missing record falls to the mechanism default — never the sibling's."""
        a = tmp_path / "a"
        b = tmp_path / "b"
        _stamp(a, "self")
        _stamp(b, "self")
        config = _config_for(a, "stable")
        assert resolve_update_channel(config, a) == CHANNEL_STABLE
        assert resolve_update_channel(config, b) == CHANNEL_MAIN

    def test_defaults_by_mechanism(self, tmp_path):
        source = tmp_path / "src"
        bundle = tmp_path / "bundle"
        _stamp(source, "self")
        _stamp(bundle, "electron-updater")
        assert default_channel(source) == CHANNEL_MAIN
        assert default_channel(bundle) == CHANNEL_STABLE
        assert resolve_update_channel({}, source) == CHANNEL_MAIN
        assert resolve_update_channel({}, bundle) == CHANNEL_STABLE

    def test_stampless_tree_defaults_to_main(self, tmp_path):
        root = tmp_path / "bare"
        root.mkdir()
        assert resolve_update_channel({}, root) == CHANNEL_MAIN

    def test_nightly_normalizes_to_main_for_source(self, tmp_path):
        root = tmp_path / "src"
        _stamp(root, "self")
        config = _config_for(root, "nightly")
        assert resolve_update_channel(config, root) == CHANNEL_MAIN

    def test_nightly_stays_for_electron_updater(self, tmp_path):
        root = tmp_path / "bundle"
        _stamp(root, "electron-updater")
        config = _config_for(root, "nightly")
        assert resolve_update_channel(config, root) == CHANNEL_NIGHTLY

    def test_garbage_record_falls_to_default(self, tmp_path):
        root = tmp_path / "src"
        _stamp(root, "self")
        config = _config_for(root, "yolo")
        assert resolve_update_channel(config, root) == CHANNEL_MAIN


class TestSetChannel:
    def _home(self, tmp_path, monkeypatch):
        home = tmp_path / ".hermes"
        home.mkdir(exist_ok=True)
        monkeypatch.setenv("HERMES_HOME", str(home))
        return home

    def test_set_resolve_round_trip(self, tmp_path, monkeypatch):
        import yaml

        home = self._home(tmp_path, monkeypatch)
        root = tmp_path / "install"
        _stamp(root, "self")

        sha16 = set_install_channel("stable", root)
        assert sha16 == install_id(root)

        written = yaml.safe_load((home / "config.yaml").read_text())
        record = written["update"]["installs"][sha16]
        assert record["channel"] == "stable"
        assert record["path"] == str(root)
        assert resolve_update_channel(written, root) == CHANNEL_STABLE

    def test_preserves_other_config_and_other_installs(self, tmp_path, monkeypatch):
        import yaml

        home = self._home(tmp_path, monkeypatch)
        other = tmp_path / "other"
        other.mkdir()
        (home / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "model": {"provider": "nous"},
                    "update": {
                        "installs": {
                            install_id(other): {"path": str(other), "channel": "nightly"}
                        }
                    },
                }
            )
        )
        root = tmp_path / "install"
        _stamp(root, "self")
        set_install_channel("stable", root)

        written = yaml.safe_load((home / "config.yaml").read_text())
        assert written["model"] == {"provider": "nous"}
        assert written["update"]["installs"][install_id(other)]["channel"] == "nightly"
        assert written["update"]["installs"][install_id(root)]["channel"] == "stable"

    def test_external_mechanism_refuses(self, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        root = tmp_path / "nix-tree"
        _stamp(root, "external")
        with pytest.raises(ValueError, match="owned by"):
            set_install_channel("stable", root)

    def test_bad_channel_refuses(self, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        root = tmp_path / "install"
        _stamp(root, "self")
        with pytest.raises(ValueError, match="unknown channel"):
            set_install_channel("beta", root)


class TestSetChannelCLI:
    """cmd_update --set-channel: the switch texts (design record)."""

    def _args(self, **kw):
        from types import SimpleNamespace

        base = dict(check=False, gateway=False, branch=None, channel=None,
                    set_channel=None, install_id=False)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_stable_switch_from_nightly_is_an_honest_wait(self, capsys):
        from unittest.mock import patch

        from hermes_cli.main import cmd_update

        with (
            patch("hermes_cli.config.detect_install_method", return_value="desktop-app"),
            patch("hermes_cli.update_channel.set_install_channel", return_value="a" * 16),
            patch(
                "installation.tree.read_build_info",
                return_value={
                    "updateMechanism": "electron-updater",
                    "displayVersion": "0.28.0-nightly.20260818",
                },
            ),
        ):
            with pytest.raises(SystemExit) as exc:
                cmd_update(self._args(set_channel="stable"))
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "0.28.0-nightly.20260818" in out       # names where you are
        assert "v0.28.0" in out                       # names the wait target
        assert "hermes-agent.nousresearch.com" in out  # the impatient path

    def test_nightly_optin_warns_about_forward_incompatible_state(self, capsys):
        from unittest.mock import patch

        from hermes_cli.main import cmd_update

        with (
            patch("hermes_cli.config.detect_install_method", return_value="desktop-app"),
            patch("hermes_cli.update_channel.set_install_channel", return_value="a" * 16),
        ):
            with pytest.raises(SystemExit) as exc:
                cmd_update(self._args(set_channel="nightly"))
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "forward-incompatible" in out


class TestDoctorStaleness:
    def test_missing_path_flagged(self, tmp_path):
        gone = tmp_path / "gone"
        config = {
            "update": {"installs": {"deadbeefdeadbeef": {"path": str(gone), "channel": "main"}}}
        }
        stale = stale_channel_records(config)
        assert [(sha, reason) for sha, _r, reason in stale] == [
            ("deadbeefdeadbeef", "missing")
        ]

    def test_replaced_install_flagged(self, tmp_path):
        """The recorded path exists but keys to a different sha16 — the
        record is a leftover from a tree that used to live elsewhere."""
        root = tmp_path / "install"
        root.mkdir()
        config = {
            "update": {"installs": {"0" * 16: {"path": str(root), "channel": "main"}}}
        }
        stale = stale_channel_records(config)
        assert [(sha, reason) for sha, _r, reason in stale] == [("0" * 16, "replaced")]

    def test_unclaimed_record_flagged(self, tmp_path, monkeypatch):
        """sha16 matches the path but no installs/<sha16>/install.json exists."""
        home = tmp_path / ".hermes"
        home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        root = tmp_path / "install"
        root.mkdir()
        config = _config_for(root, "main")
        stale = stale_channel_records(config)
        assert [(sha, reason) for sha, _r, reason in stale] == [
            (install_id(root), "unclaimed")
        ]

    def test_healthy_record_not_flagged(self, tmp_path, monkeypatch):
        home = tmp_path / ".hermes"
        home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        root = tmp_path / "install"
        _stamp(root, "self")
        from hermes_cli.boot_bootstrap import ensure_install_dir

        ensure_install_dir(root)
        config = _config_for(root, "main")
        assert stale_channel_records(config) == []
