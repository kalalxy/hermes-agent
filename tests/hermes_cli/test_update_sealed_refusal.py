"""``hermes update`` on the sealed desktop payload must refuse up front.

The bundled desktop app runs the agent out of its signed resources
(``resources/agent-payload/repo``). That tree is SEALED: it ships an
``install-stamp.json`` naming ``desktop-app`` as its steward, and — by
design — no ``.git`` and no manifest.

The refusal used to key ONLY on the install manifest
(``is_bundled_install``). ``read_install_manifest`` defaults a missing
manifest to ``installMode: source``, so the sealed payload sailed past
the guard into ``_cmd_update_impl``, which ran the pre-update backup and
staged an update INTO the app resources (``*.hermes-update-staging``
debris beside every ``repo/`` top-level dir — observed on a live v0.27.0
win-arm64 bundled install). These tests pin the fixed contract: a
``desktop-app`` sealed tree refuses BEFORE any mutation, with the
steward message, regardless of manifest presence.

Vendored from kshitijk4poor/hermes-agent@restack-fix-sealed-refusal,
adapted to the stamp-pure ladder (the stamp's required updateMechanism
is the classification fact; the manifest is on its way out).
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hermes_cli.main import cmd_update


def _args(**overrides):
    base = dict(eject=False, check=False, gateway=False, branch=None, channel=None)
    base.update(overrides)
    return SimpleNamespace(**base)


class TestSealedDesktopUpdateRefusal:
    def test_sealed_payload_without_manifest_refuses(self, capsys):
        """The live-bug shape: stamp says desktop-app, no manifest on disk
        (is_bundled_install False). Must refuse, print the steward message,
        and never reach the update body."""
        with (
            patch("hermes_cli.config.detect_install_method", return_value="desktop-app"),
            patch("hermes_cli.install_manifest.is_bundled_install", return_value=False),
            patch("hermes_cli.main._cmd_update_impl") as impl,
        ):
            with pytest.raises(SystemExit) as exc:
                cmd_update(_args())
        assert exc.value.code == 1
        impl.assert_not_called()
        out = capsys.readouterr().out
        # The steward refusal, not the git/source refusal.
        assert "desktop app" in out
        assert "git pull" not in out

    def test_materialized_bundled_checkout_still_refuses(self, capsys):
        """The pre-existing path keeps working: a manifest with
        installMode=bundled refuses even when the method probe says git."""
        with (
            patch("hermes_cli.config.detect_install_method", return_value="git"),
            patch("hermes_cli.install_manifest.is_bundled_install", return_value=True),
            patch("hermes_cli.main._cmd_update_impl") as impl,
        ):
            with pytest.raises(SystemExit) as exc:
                cmd_update(_args())
        assert exc.value.code == 1
        impl.assert_not_called()

    def test_eject_still_reachable_on_sealed_payload(self):
        """--eject must keep working on a desktop-app tree: it is the one
        update operation a bundled install supports, and it runs before
        the refusal."""
        with (
            patch("hermes_cli.config.detect_install_method", return_value="desktop-app"),
            patch("hermes_cli.update_cmd.cmd_update_eject", return_value=0) as eject,
        ):
            with pytest.raises(SystemExit) as exc:
                cmd_update(_args(eject=True))
        assert exc.value.code == 0
        eject.assert_called_once()

    def test_check_path_also_refuses_sealed_payload(self, capsys):
        """--check on the sealed payload prints the steward refusal, not a
        misleading 'Not a git repository'. Sibling caller sweep: the
        check path used to assume 'not docker/nix/source ⇒ git tree'."""
        from hermes_cli.update_cmd import _cmd_update_check

        with patch("hermes_cli.config.detect_install_method", return_value="desktop-app"):
            with pytest.raises(SystemExit) as exc:
                _cmd_update_check()
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "desktop app" in out
        assert "Not a git repository" not in out
