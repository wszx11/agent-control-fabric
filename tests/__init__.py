# -*- coding: utf-8 -*-
"""Test package guard: keep the whole suite out of the production _locks dir.

Historically several test modules routed actions through ActionRouter without
isolating ACF_LOCK_DIR, so their fixture records were appended to the real
`_locks/trajectories.jsonl` and inflated retrospect metrics. Importing this
package (which both pytest and `unittest discover -s tests` do) installs a
suite-wide fallback: unless the caller already set ACF_LOCK_DIR, it is pointed
at a throwaway temp dir that is removed at interpreter exit.

Individual test classes should still use tests._isolation.IsolatedLockDirMixin
so direct execution (`python tests/test_x.py`) stays isolated too.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile

if not os.environ.get("ACF_LOCK_DIR"):
    _ACF_SUITE_GUARD_TMP = tempfile.mkdtemp(prefix="acf_suite_guard_")
    os.environ["ACF_LOCK_DIR"] = _ACF_SUITE_GUARD_TMP
    atexit.register(shutil.rmtree, _ACF_SUITE_GUARD_TMP, True)
