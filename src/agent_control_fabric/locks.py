"""Cross-process multi-agent resource locking.

Provides atomic mutual-exclusion locks on named resources across independent
agent processes and runtimes using filesystem semantics (O_CREAT | O_EXCL),
lease timeouts (TTL), dead-owner detection, and append-only audit logging.
"""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


def lock_dir() -> str:
    default_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "_locks",
    )
    d = os.environ.get("ACF_LOCK_DIR", default_dir)
    os.makedirs(d, exist_ok=True)
    return d


def _safe_name(key: str) -> str:
    """Sanitize key string to a safe Windows filename."""
    import re
    return re.sub(r'[<>:"/\\|?*]', "_", key)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            h = kernel32.OpenProcess(0x1000, False, int(pid))
            if not h:
                return False
            try:
                exit_code = ctypes.c_ulong(0)
                if kernel32.GetExitCodeProcess(h, ctypes.byref(exit_code)):
                    return exit_code.value == 259
                return True
            finally:
                kernel32.CloseHandle(h)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, PermissionError):
        return False


@dataclass
class LockRecord:
    key: str
    agent: str
    pid: int
    boot: str
    token: str
    acquired_at: float
    ttl: float
    note: str = ""

    def expired(self, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        return (now - self.acquired_at) > self.ttl or not _pid_alive(self.pid)


class LockError(RuntimeError):
    pass


class ResourceLock:
    """Named mutual-exclusion lock with lease and takeover semantics."""

    def __init__(
        self,
        key: str,
        agent: str = "agent",
        ttl: float = 120.0,
        poll: float = 0.05,
        note: str = "",
    ):
        self.key = key
        self.agent = agent
        self.ttl = ttl
        self.poll = poll
        self.note = note
        self.record: Optional[LockRecord] = None
        self._path = os.path.join(lock_dir(), _safe_name(key) + ".lock")
        self._held = False

    @property
    def path(self) -> str:
        return self._path

    def _read(self) -> Optional[LockRecord]:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                d = json.load(f)
            return LockRecord(**d)
        except (OSError, json.JSONDecodeError, TypeError):
            return None

    def _write_new(self, rec: LockRecord) -> bool:
        try:
            fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, PermissionError):
            return False
        payload = json.dumps(asdict(rec), ensure_ascii=False).encode("utf-8")
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)
        return True

    def _audit(self, event: str, rec: Optional[LockRecord], extra: Optional[Dict[str, Any]] = None):
        line = {
            "ts": time.time(),
            "event": event,
            "key": self.key,
            "agent": self.agent,
            "owner": asdict(rec) if rec else None,
        }
        if extra:
            line.update(extra)
        try:
            p = os.path.join(lock_dir(), "events.jsonl")
            payload = (json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8")
            fd = os.open(p, os.O_CREAT | os.O_WRONLY | os.O_APPEND)
            try:
                os.write(fd, payload)
            finally:
                os.close(fd)
        except OSError:
            pass

    def acquire(self, timeout: float = 10.0) -> "ResourceLock":
        boot = f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"
        rec = LockRecord(
            key=self.key,
            agent=self.agent,
            pid=os.getpid(),
            boot=boot,
            token=uuid.uuid4().hex,
            acquired_at=time.time(),
            ttl=self.ttl,
            note=self.note,
        )
        deadline = time.time() + timeout
        while True:
            if self._write_new(rec):
                self.record, self._held = rec, True
                self._audit("acquired", rec)
                return self
            cur = self._read()
            if cur is None:
                # Guard against TOCTOU race while another process is mid-write:
                # only delete corrupt/empty lock file if it has been stagnant > 1.0s.
                try:
                    age = time.time() - os.path.getmtime(self._path)
                    if age > 1.0:
                        os.remove(self._path)
                        continue
                except OSError:
                    continue
                time.sleep(self.poll)
                continue
            if cur.expired():
                try:
                    os.remove(self._path)
                except OSError:
                    pass
                self._audit("takeover", cur, {"reason": "expired_or_dead_owner"})
                continue
            if time.time() >= deadline:
                raise LockError(
                    f"lock '{self.key}' busy: owner={cur.agent} pid={cur.pid} "
                    f"age={time.time() - cur.acquired_at:.1f}s ttl={cur.ttl}s"
                )
            time.sleep(self.poll)

    def release(self) -> bool:
        if not self._held:
            return False
        cur = self._read()
        if cur and cur.token == (self.record.token if self.record else None):
            try:
                os.remove(self._path)
            except OSError:
                pass
            self._audit("released", cur)
            self._held = False
            return True
        self._audit("release_denied", cur, {"reason": "token_mismatch"})
        return False

    def __enter__(self) -> "ResourceLock":
        return self.acquire()

    def __exit__(self, *exc: Any) -> bool:
        self.release()
        return False


def peek(key: str) -> Optional[Dict[str, Any]]:
    """Read lock holder information without acquiring."""
    path = os.path.join(lock_dir(), _safe_name(key) + ".lock")
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        rec = LockRecord(
            key=d["key"],
            agent=d["agent"],
            pid=d["pid"],
            boot=d["boot"],
            token=d["token"],
            acquired_at=d["acquired_at"],
            ttl=d["ttl"],
            note=d.get("note", ""),
        )
        d["_expired"] = rec.expired()
        return d
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def active_keys() -> List[str]:
    """Return all currently held (non-expired) lock keys."""
    out: List[str] = []
    d = lock_dir()
    try:
        for fn in os.listdir(d):
            if fn.endswith(".lock"):
                k = fn[:-5]
                rec = peek(k)
                if rec and not rec.get("_expired"):
                    out.append(rec.get("key", k))
    except OSError:
        pass
    return sorted(out)
