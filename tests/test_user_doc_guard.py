# -*- coding: utf-8 -*-
"""Unit coverage for the v0.5.1 user-document protection guard (was: zero tests).

Covers: unsaved-marker title pattern, editor names in title/process, empty
title combined with an editor process (Chinese-localized editor windows),
neutral apps staying unblocked, and a source-level regression assert that the
invalid escape sequence that used to trigger DeprecationWarning is gone.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_control_fabric import win32_backend  # noqa: E402
from agent_control_fabric.win32_backend import is_user_document_window  # noqa: E402


class IsUserDocumentWindowTests(unittest.TestCase):
    def test_unsaved_marker_patterns_block(self):
        for title in ("*Untitled - my.txt - Notepad",
                      "* 报告.docx - Word",
                      "*main.py - Visual Studio Code",
                      "*data.xlsx - Excel"):
            self.assertTrue(is_user_document_window(title), title)

    def test_editor_names_in_title_block(self):
        for title in ("周报 - Notepad", "budget - Excel", "readme - WPS"):
            self.assertTrue(is_user_document_window(title), title)

    def test_editor_process_names_block_even_with_neutral_or_empty_title(self):
        for proc in ("notepad.exe", "NOTEPAD.EXE", "winword.exe", "excel.exe",
                     "powerpnt.exe", "wps.exe", "notepad++.exe"):
            self.assertTrue(is_user_document_window("任意标题", process_name=proc), proc)
            self.assertTrue(is_user_document_window("", process_name=proc), proc)

    def test_neutral_apps_are_not_blocked(self):
        self.assertFalse(is_user_document_window("ToDesk", process_name="ToDesk.exe"))
        self.assertFalse(is_user_document_window("Telegram - zx", process_name="telegram.exe"))
        self.assertFalse(is_user_document_window("", process_name="python.exe"))
        self.assertFalse(is_user_document_window("云电脑控制台", process_name="chrome.exe"))

    def test_no_invalid_escape_sequences_in_source(self):
        # regression: the tuple used to hold "notepad\+\+" (invalid escape -> DeprecationWarning)
        src = Path(win32_backend.__file__).read_text(encoding="utf-8")
        self.assertNotIn(r'"notepad\+\+"', src)


if __name__ == "__main__":
    unittest.main()
