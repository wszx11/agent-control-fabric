# -*- coding: utf-8 -*-
"""Shared test isolation helpers.

Any test that routes actions (ActionRouter) or calls record_trajectory must
NOT write into the production `_locks/trajectories.jsonl`. Use
`IsolatedLockDirMixin` (or run the suite through `tests/__init__.py`, which
installs a package-wide guard) so ACF_LOCK_DIR points at a private temp dir.
"""
from __future__ import annotations

import os
import shutil
import tempfile


class IsolatedLockDirMixin:
    """Point ACF_LOCK_DIR at a private temp dir for the duration of each test.

    Mix in BEFORE unittest.TestCase, e.g.::

        class MyTests(IsolatedLockDirMixin, unittest.TestCase): ...
    """

    def setUp(self):
        super().setUp()
        self._acf_lock_tmp = tempfile.mkdtemp(prefix="acf_iso_lock_")
        self._acf_old_lock_env = os.environ.get("ACF_LOCK_DIR")
        os.environ["ACF_LOCK_DIR"] = self._acf_lock_tmp

    def tearDown(self):
        if self._acf_old_lock_env is not None:
            os.environ["ACF_LOCK_DIR"] = self._acf_old_lock_env
        else:
            os.environ.pop("ACF_LOCK_DIR", None)
        shutil.rmtree(self._acf_lock_tmp, ignore_errors=True)
        super().tearDown()
