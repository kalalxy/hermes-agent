"""The WhatsApp bridge contract when the managed Node is missing.

``installation.nodejs`` raises ``NotProvisioned`` when the runtime dir has
no managed node or npm. It is not a fallback signal: there is no system
Node of known version to reach for. The adapter therefore answers in two
different ways, and which one depends on whether the user asked for
WhatsApp yet.

* ``check_whatsapp_requirements()`` is an availability probe. The toolset
  registry calls it to decide what to show. It answers False.
* ``connect()`` runs because the user enabled WhatsApp. It fails loud,
  sets a non-retryable fatal error, and names the provisioner.

A crash in either place is the fault this file prevents: an escaping
``NotProvisioned`` from the probe takes down the caller that was only
asking a question.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import Platform
from installation import nodejs


def _make_adapter(tmp_path: Path):
    """A WhatsAppAdapter with the attributes connect() touches."""
    from plugins.platforms.whatsapp.adapter import WhatsAppAdapter

    adapter = WhatsAppAdapter.__new__(WhatsAppAdapter)
    adapter.platform = Platform.WHATSAPP
    adapter.config = MagicMock()
    adapter._bridge_port = 19877
    adapter._bridge_script = str(tmp_path / "bridge.js")
    adapter._session_path = tmp_path / "session"
    adapter._bridge_log_fh = None
    adapter._bridge_log = None
    adapter._bridge_process = None
    adapter._reply_prefix = None
    adapter._send_read_receipts = False
    adapter._running = False
    adapter._message_handler = None
    adapter._fatal_error_code = None
    adapter._fatal_error_message = None
    adapter._fatal_error_retryable = True
    adapter._fatal_error_handler = None
    adapter._active_sessions = {}
    adapter._pending_messages = {}
    adapter._background_tasks = set()
    adapter._auto_tts_disabled_chats = set()
    adapter._message_queue = asyncio.Queue()
    adapter._http_session = None
    return adapter


class TestAvailabilityProbe:
    def test_check_returns_false_and_does_not_raise(self):
        """The probe answers the question instead of crashing the asker."""
        from plugins.platforms.whatsapp import adapter as wa

        with patch.object(
            nodejs,
            "node_path",
            side_effect=nodejs.NotProvisioned("node is not in this install"),
        ):
            assert wa.check_whatsapp_requirements() is False

    def test_check_is_true_when_node_runs(self):
        from plugins.platforms.whatsapp import adapter as wa

        with patch.object(nodejs, "node_path", return_value=Path("/managed/bin/node")), \
             patch.object(
                 wa.subprocess, "run", return_value=MagicMock(returncode=0)
             ):
            assert wa.check_whatsapp_requirements() is True


class TestSpawnFailsLoud:
    @pytest.mark.asyncio
    async def test_missing_npm_is_a_non_retryable_fatal_error(self, tmp_path):
        """A tree with no managed npm cannot install the bridge deps.

        Reaching for a system npm would install the bridge against an
        unknown toolchain, so the adapter refuses and names the fix.
        """
        adapter = _make_adapter(tmp_path)

        def _path_exists(path_obj):
            # bridge.js present, node_modules absent -> the npm install runs
            return not str(path_obj).endswith("node_modules")

        from plugins.platforms.whatsapp import adapter as wa

        with patch.object(wa, "check_whatsapp_requirements", return_value=True), \
             patch.object(Path, "exists", autospec=True, side_effect=_path_exists), \
             patch.object(
                 nodejs,
                 "npm_path",
                 side_effect=nodejs.NotProvisioned("npm is not in this install"),
             ), \
             patch("gateway.status.acquire_scoped_lock", return_value=(True, None)), \
             patch("gateway.status.release_scoped_lock"):
            result = await adapter.connect()

        assert result is False
        assert adapter.fatal_error_code == "whatsapp_npm_install_failed"
        assert adapter.fatal_error_retryable is False
        message = adapter.fatal_error_message or ""
        assert "installation.provisioner" in message, message

    @pytest.mark.asyncio
    async def test_missing_node_is_a_non_retryable_fatal_error(self, tmp_path):
        """The spawn itself refuses when the managed node is gone."""
        adapter = _make_adapter(tmp_path)
        from plugins.platforms.whatsapp import adapter as wa

        mock_fh = MagicMock()

        with patch.object(wa, "check_whatsapp_requirements", return_value=True), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "mkdir", return_value=None), \
             patch.object(wa, "subprocess") as mock_sub, \
             patch("builtins.open", return_value=mock_fh), \
             patch.object(wa.asyncio, "sleep", new_callable=AsyncMock), \
             patch.object(nodejs, "npm_path", return_value=Path("/managed/bin/npm")), \
             patch.object(
                 nodejs,
                 "node_path",
                 side_effect=nodejs.NotProvisioned("node is not in this install"),
             ), \
             patch("aiohttp.ClientSession", MagicMock(side_effect=OSError("no bridge"))), \
             patch("gateway.status.acquire_scoped_lock", return_value=(True, None)), \
             patch("gateway.status.release_scoped_lock"):
            mock_sub.run.return_value = MagicMock(returncode=0)
            result = await adapter.connect()

        assert result is False
        assert adapter.fatal_error_code == "whatsapp_node_not_provisioned"
        assert adapter.fatal_error_retryable is False
        message = adapter.fatal_error_message or ""
        assert "installation.provisioner" in message, message
        # The bridge never started, so no process may be left recorded.
        mock_sub.Popen.assert_not_called()

    @pytest.mark.asyncio
    async def test_spawn_failure_closes_the_bridge_log(self, tmp_path):
        """The log handle opened before the spawn must not leak."""
        adapter = _make_adapter(tmp_path)
        from plugins.platforms.whatsapp import adapter as wa

        mock_fh = MagicMock()

        with patch.object(wa, "check_whatsapp_requirements", return_value=True), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "mkdir", return_value=None), \
             patch.object(wa, "subprocess") as mock_sub, \
             patch("builtins.open", return_value=mock_fh), \
             patch.object(wa.asyncio, "sleep", new_callable=AsyncMock), \
             patch.object(nodejs, "npm_path", return_value=Path("/managed/bin/npm")), \
             patch.object(
                 nodejs,
                 "node_path",
                 side_effect=nodejs.NotProvisioned("node is not in this install"),
             ), \
             patch("aiohttp.ClientSession", MagicMock(side_effect=OSError("no bridge"))), \
             patch("gateway.status.acquire_scoped_lock", return_value=(True, None)), \
             patch("gateway.status.release_scoped_lock"):
            mock_sub.run.return_value = MagicMock(returncode=0)
            await adapter.connect()

        assert adapter._bridge_log_fh is None
