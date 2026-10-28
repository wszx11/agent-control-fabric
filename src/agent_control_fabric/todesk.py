"""ToDesk remote desktop integration and payload bundle staging.

Provides telemetry, window inspection, sanitized bundle staging,
and background interaction helpers for ToDesk (Windows remote control).

Security discipline:
- Never includes plaintext password files (*.password.txt) in transfer bundles.
- Only encrypted containers (*.tar.enc) and verification checksums are staged.
- Background interaction uses PostMessage/UIA without stealing foreground focus.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Action, BackendResult, Target
from .win32_backend import Win32Backend, WindowInfo


@dataclass
class ToDeskWindow:
    hwnd: int
    title: str
    class_name: str
    pid: int
    rect: tuple[int, int, int, int]
    visible: bool


@dataclass
class ToDeskTelemetry:
    installed: bool
    install_path: str
    running: bool
    pids: List[int]
    windows: List[ToDeskWindow]
    main_window_id: Optional[int]
    remote_window_id: Optional[int]
    remote_target_id: Optional[str]
    is_connected: bool


def get_todesk_telemetry() -> ToDeskTelemetry:
    """Scan and inspect local ToDesk processes, configuration, and windows."""
    install_path = r"D:\ToDesk\ToDesk.exe"
    installed = os.path.exists(install_path)

    pids: List[int] = []
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name"]):
            if p.info["name"] and "todesk" in p.info["name"].lower():
                pids.append(p.info["pid"])
    except Exception:
        pass

    windows: List[ToDeskWindow] = []
    main_window_id: Optional[int] = None
    remote_window_id: Optional[int] = None
    remote_target_id: Optional[str] = None
    is_connected = False

    user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

    def _enum(hwnd: int, _lparam: Any) -> bool:
        pid = ctypes.wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            vis = bool(user32.IsWindowVisible(hwnd))
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value
            c_buf = ctypes.create_unicode_buffer(80)
            user32.GetClassNameW(hwnd, c_buf, 80)
            cls_name = c_buf.value
            rc = ctypes.wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rc))
            rect = (rc.left, rc.top, rc.right, rc.bottom)

            windows.append(
                ToDeskWindow(
                    hwnd=hwnd,
                    title=title,
                    class_name=cls_name,
                    pid=pid.value,
                    rect=rect,
                    visible=vis,
                )
            )
        return True

    user32.EnumWindows(WNDENUMPROC(_enum), 0)

    for w in windows:
        if w.class_name == "H-SMILE-FRAME":
            if w.title == "ToDesk":
                if w.visible or main_window_id is None:
                    main_window_id = w.hwnd
            elif "windows-" in w.title.lower() or w.title.startswith("Windows-"):
                if w.visible or remote_window_id is None:
                    remote_window_id = w.hwnd
                if w.visible:
                    is_connected = True
                m = re.search(r"\(([\d\s]+)\)", w.title)
                if m:
                    remote_target_id = m.group(1).replace(" ", "")
                elif not remote_target_id and w.title:
                    remote_target_id = w.title

    # Read config.ini if available for saved remote targets
    cfg_path = Path(r"D:\ToDesk\config.ini")
    if not remote_target_id and cfg_path.exists():
        try:
            txt = cfg_path.read_text(encoding="utf-8", errors="replace")
            m_target = re.search(r"destId\s*=\s*(\d+)", txt)
            if m_target:
                remote_target_id = m_target.group(1)
        except Exception:
            pass

    return ToDeskTelemetry(
        installed=installed,
        install_path=install_path,
        running=len(pids) > 0,
        pids=pids,
        windows=windows,
        main_window_id=main_window_id,
        remote_window_id=remote_window_id,
        remote_target_id=remote_target_id,
        is_connected=is_connected,
    )


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def prepare_sanitized_bundle(
    source_dir: str | Path,
    output_zip: str | Path,
) -> Dict[str, Any]:
    """Package encrypted containers into a sanitized ZIP while strictly

    rejecting any plaintext credentials or password files (*.password.txt).
    """
    source_path = Path(source_dir)
    zip_path = Path(output_zip)
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    included_files: List[Dict[str, Any]] = []
    rejected_files: List[str] = []

    for item in sorted(source_path.iterdir()):
        name_lower = item.name.lower()
        # Security redline: strictly reject password files
        if "password" in name_lower or item.suffix in (".key", ".pem", ".env"):
            rejected_files.append(item.name)
            continue

        if item.is_file() and (
            item.name.endswith(".enc")
            or item.name.endswith(".sha256.txt")
            or item.name.endswith(".sha256")
        ):
            sha = compute_sha256(item)
            included_files.append({
                "name": item.name,
                "size_bytes": item.stat().st_size,
                "sha256": sha,
            })

    if not included_files:
        raise ValueError(f"No valid sanitized payload files found in {source_path}")

    # Build sanitized zip
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_meta in included_files:
            file_on_disk = source_path / file_meta["name"]
            zf.write(file_on_disk, arcname=file_meta["name"])

        # Also write the manifest into the zip
        manifest = {
            "bundle": zip_path.name,
            "created_at": time.time(),
            "included": included_files,
            "security_rejected_plaintext_passwords": rejected_files,
            "verification_note": "Plaintext passwords stripped to prevent leakage across remote channels.",
        }
        zf.writestr("MANIFEST_SANITIZED.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    zip_sha = compute_sha256(zip_path)
    return {
        "zip_path": str(zip_path),
        "zip_size_bytes": zip_path.stat().st_size,
        "zip_sha256": zip_sha,
        "included_count": len(included_files),
        "rejected_passwords_count": len(rejected_files),
        "manifest": manifest,
    }


def copy_files_to_clipboard(file_paths: List[str | Path]) -> bool:
    """Set the Windows system clipboard to CF_HDROP format containing file paths.

    This enables ToDesk's background file clipboard sync to replicate the files.
    """
    import win32clipboard
    import win32con

    paths = [str(Path(p).resolve()) for p in file_paths if Path(p).exists()]
    if not paths:
        return False

    # Structure of DROPFILES (20 bytes header + null-terminated wide strings + double null)
    # DWORD pFiles (offset from start of struct, 20 bytes)
    # POINT pt (0, 0)
    # BOOL fNC (0)
    # BOOL fWide (1 for Unicode)
    offset = 20
    header = offset.to_bytes(4, "little") + (0).to_bytes(8, "little") + (0).to_bytes(4, "little") + (1).to_bytes(4, "little")

    encoded_paths = b"".join(p.encode("utf-16le") + b"\x00\x00" for p in paths) + b"\x00\x00"
    payload = header + encoded_paths

    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_HDROP, payload)
        return True
    finally:
        win32clipboard.CloseClipboard()
