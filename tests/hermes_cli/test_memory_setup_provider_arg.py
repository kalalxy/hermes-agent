"""Tests for `hermes memory setup [provider]` routing.

The `memory setup` subcommand accepts an optional positional ``provider`` so a
fresh install can configure a specific provider directly (e.g.
``hermes memory setup honcho``) without the interactive picker — which matters
because the per-provider ``hermes <provider>`` subcommand is only registered
once that provider is active.
"""

from types import SimpleNamespace
from unittest.mock import patch

from hermes_cli import memory_setup


class TestMemorySetupProviderRouting:
    def test_setup_with_provider_arg_skips_picker(self):
        """`memory setup honcho` routes straight to cmd_setup_provider."""
        args = SimpleNamespace(memory_command="setup", provider="honcho")
        with patch.object(memory_setup, "cmd_setup_provider") as direct, \
             patch.object(memory_setup, "cmd_setup") as picker:
            memory_setup.memory_command(args)
        direct.assert_called_once_with("honcho")
        picker.assert_not_called()


    def test_unknown_provider_reports_and_returns_early(self, capsys):
        """An unknown provider name surfaces a helpful message and returns
        before any config load/save (the not-found guard precedes those imports)."""
        memory_setup.cmd_setup_provider("notaprovider")
        out = capsys.readouterr().out
        assert "not found" in out
        assert "hermes memory setup" in out


class TestInstallDependenciesRunner:
    """`_install_dependencies` must route through ``_pip_install``.

    There is no pip tier: pip resolves the same requirements again
    without uv policy (exclude-newer, the [tool.uv] overrides), so it
    can install a release the project quarantined. An unprovisioned
    tree therefore reports a provisioning fault instead of installing a
    different dependency set."""

    def _run_with_missing_dep(self, tmp_path, uv_bin, run_behavior=None):
        """Drive _install_dependencies for a plugin that declares one missing
        pip dep, capturing every subprocess.run argv the install issues.

        *uv_bin* is what ``ensure_uv()`` resolves to — the managed uv
        path, or None for an unprovisioned tree.
        """
        import os
        import sys
        from unittest.mock import patch as _patch

        (tmp_path / "plugin.yaml").write_text(
            "pip_dependencies:\n  - definitely-not-installed-xyz\n", encoding="utf-8"
        )
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            if run_behavior:
                return run_behavior(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        # The hermetic conftest sets HERMES_DISABLE_LAZY_INSTALLS=1 so no test
        # can trigger a real mid-run pip install. These tests exercise the
        # install ladder itself (against a fully mocked subprocess.run), so
        # they opt back in — the same both-directions override
        # tests/tools/test_lazy_deps.py uses.
        with _patch.dict(os.environ, {"HERMES_DISABLE_LAZY_INSTALLS": "0"}), \
             patch("plugins.memory.find_provider_dir", return_value=tmp_path), \
             patch("hermes_cli.managed_uv.ensure_uv", return_value=uv_bin), \
             patch("installation.pip_ladder.default_uv", return_value=uv_bin), \
             patch("installation.pip_ladder.subprocess.run", fake_run):
            memory_setup._install_dependencies("x")
        return calls, sys.executable

    def test_installs_with_the_managed_uv(self, tmp_path):
        calls, _ = self._run_with_missing_dep(tmp_path, "/managed/bin/uv")
        assert calls
        assert calls[0][:3] == ["/managed/bin/uv", "pip", "install"]

    def test_no_managed_uv_installs_nothing(self, tmp_path):
        """An unprovisioned tree must not fall through to pip.

        pip would resolve without uv policy and could install a release
        the project quarantined, so no install is the correct outcome.
        """
        calls, py = self._run_with_missing_dep(tmp_path, None)
        assert calls == [], f"an install ran with no managed uv: {calls}"

    def test_no_managed_uv_never_reaches_ensurepip(self, tmp_path):
        """The ensurepip bootstrap existed only to heal a pip tier."""
        calls, _ = self._run_with_missing_dep(tmp_path, None)
        assert not any("ensurepip" in c for c in calls), calls
