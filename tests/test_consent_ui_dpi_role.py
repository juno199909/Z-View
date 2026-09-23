# -*- coding: utf-8 -*-
"""Tray/consent role must not inherit remote-capture DPI awareness."""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from cmdb_agent_unified_v2 import should_enable_process_dpi_awareness  # noqa: E402


def test_consent_ui_keeps_windows_menu_dpi_unaware():
    assert should_enable_process_dpi_awareness(["--consent-ui"]) is False


def test_remote_capture_roles_keep_dpi_awareness():
    assert should_enable_process_dpi_awareness(["--service-host"]) is True
    assert should_enable_process_dpi_awareness(["--user-session-agent"]) is True
