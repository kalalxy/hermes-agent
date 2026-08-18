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
    base = dict(check=False, gateway=False, branch=None, channel=None,
                set_channel=None, install_id=False)
    base.update(overrides)
    return SimpleNamespace(**base)


class TestSealedDesktopUpdateRefusal:
    def test_sealed_payload_refuses(self, capsys):
        """The live-bug shape: stamp says desktop-app (no manifest exists —
        the manifest is dead). Must refuse, print the steward message, and
        never reach the update body."""
        with (
            patch("hermes_cli.config.detect_install_method", return_value="desktop-app"),
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

    def test_materialized_bundled_checkout_is_just_a_checkout_now(self):
        """The manifest is dead: nothing can mark a checkout 'bundled'
        anymore. A .git tree classifies purely by its stamp — this case
        (formerly 'manifest says bundled → refuse') now proceeds like any
        managed checkout. The refusal surface is the stamp alone."""
        with (
            patch("hermes_cli.config.detect_install_method", return_value="git"),
            patch("hermes_cli.main._cmd_update_impl") as impl,
            patch("hermes_cli.main._install_hangup_protection", return_value=None),
            patch("hermes_cli.main._finalize_update_output"),
        ):
            try:
                cmd_update(_args())
            except SystemExit:
                pass
        # The update body was reachable — no manifest gate stands in the way.
        assert impl.called

    def test_refusal_names_the_docs_page_direction(self, capsys):
        """The sealed refusal points at the desktop app (switching to a
        source install is a docs journey, not a CLI flag anymore)."""
        with (
            patch("hermes_cli.config.detect_install_method", return_value="desktop-app"),
            patch("hermes_cli.main._cmd_update_impl") as impl,
        ):
            with pytest.raises(SystemExit):
                cmd_update(_args())
        impl.assert_not_called()
        out = capsys.readouterr().out
        assert "desktop app" in out

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
