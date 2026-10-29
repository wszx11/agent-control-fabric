# -*- coding: utf-8 -*-
"""Guards the off-Windows claim so it cannot silently regress.

`scripts/check_offwindows_imports.py` was a manual step; a manual step rots.
These tests run the same check inside the suite, and also prove the checker can
actually fail (a checker that always passes is worse than none).
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECKER = REPO / "scripts" / "check_offwindows_imports.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("_offwindows_checker", CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OffWindowsGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checker = _load_checker()

    def test_checker_script_exists(self):
        self.assertTrue(CHECKER.is_file(), f"missing {CHECKER}")

    def test_repo_has_no_module_level_windows_only_ctypes(self):
        problems = []
        for path in self.checker.TARGETS:
            problems.extend(self.checker.check_file(path))
        self.assertEqual(problems, [], "\n".join(problems))

    def test_checker_flags_planted_violation(self):
        with tempfile.TemporaryDirectory(prefix="acf_portability_") as tmp:
            sample = Path(tmp) / "planted_bad.py"
            sample.write_text("import ctypes\nuser32 = ctypes.windll.user32\n", encoding="utf-8")
            found = self.checker.check_file(sample)
        self.assertEqual(len(found), 1, found)
        self.assertIn("windll", found[0])

    def test_checker_accepts_guarded_and_call_time_use(self):
        guarded = "import ctypes, os\nif os.name == 'nt':\n    u = ctypes.windll.user32\n"
        call_time = "import ctypes\ndef f():\n    return ctypes.WinDLL('user32')\n"
        with tempfile.TemporaryDirectory(prefix="acf_portability_") as tmp:
            for name, src in (("guarded.py", guarded), ("call_time.py", call_time)):
                path = Path(tmp) / name
                path.write_text(src, encoding="utf-8")
                self.assertEqual(self.checker.check_file(path), [], name)

    def test_tests_do_not_hardcode_absolute_repo_paths(self):
        # Build the needle at runtime, otherwise this very file matches itself.
        needle = "ai " + "gongzuoqu"
        offenders = [
            path.name
            for path in (REPO / "tests").glob("*.py")
            if needle in path.read_text(encoding="utf-8", errors="replace")
        ]
        self.assertEqual(offenders, [], f"hardcoded paths in: {offenders}")


if __name__ == "__main__":
    unittest.main()
