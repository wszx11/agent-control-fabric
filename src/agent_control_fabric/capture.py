"""Offscreen window capture via Win32 PrintWindow + pure-stdlib PNG encoder.

Captures the rendered surface of any visible top-level window (even if occluded
behind the user's active foreground window) without raising the window, moving
the cursor, or stealing focus. Requires zero third-party image libraries.
"""

from __future__ import annotations

import ctypes
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

from .win32_backend import IS_WINDOWS, ensure_dpi_aware, gdi32, user32, wt

ensure_dpi_aware()

PW_RENDERFULLCONTENT = 0x00000002
BI_RGB = 0
DIB_RGB_COLORS = 0

if IS_WINDOWS:
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wt.DWORD),
            ("biWidth", wt.LONG),
            ("biHeight", wt.LONG),
            ("biPlanes", wt.WORD),
            ("biBitCount", wt.WORD),
            ("biCompression", wt.DWORD),
            ("biSizeImage", wt.DWORD),
            ("biXPelsPerMeter", wt.LONG),
            ("biYPelsPerMeter", wt.LONG),
            ("biClrUsed", wt.DWORD),
            ("biClrImportant", wt.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [
            ("bmiHeader", BITMAPINFOHEADER),
            ("bmiColors", wt.DWORD * 3),
        ]

    user32.GetWindowDC.argtypes = (wt.HWND,)
    user32.GetWindowDC.restype = wt.HDC
    user32.ReleaseDC.argtypes = (wt.HWND, wt.HDC)
    user32.ReleaseDC.restype = ctypes.c_int
    user32.PrintWindow.argtypes = (wt.HWND, wt.HDC, wt.UINT)
    user32.PrintWindow.restype = wt.BOOL

    gdi32.CreateCompatibleDC.argtypes = (wt.HDC,)
    gdi32.CreateCompatibleDC.restype = wt.HDC
    gdi32.CreateCompatibleBitmap.argtypes = (wt.HDC, ctypes.c_int, ctypes.c_int)
    gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
    gdi32.SelectObject.argtypes = (wt.HDC, wt.HGDIOBJ)
    gdi32.SelectObject.restype = wt.HGDIOBJ
    gdi32.DeleteObject.argtypes = (wt.HGDIOBJ,)
    gdi32.DeleteObject.restype = wt.BOOL
    gdi32.DeleteDC.argtypes = (wt.HDC,)
    gdi32.DeleteDC.restype = wt.BOOL
    gdi32.GetDIBits.argtypes = (
        wt.HDC,
        wt.HBITMAP,
        wt.UINT,
        wt.UINT,
        ctypes.c_void_p,
        ctypes.POINTER(BITMAPINFO),
        wt.UINT,
    )
    gdi32.GetDIBits.restype = ctypes.c_int


@dataclass
class CaptureResult:
    ok: bool
    hwnd: int
    width: int
    height: int
    path: str
    size_bytes: int
    non_black_ratio: float
    detail: str = ""


def _encode_rgb_png(width: int, height: int, bgra_bytes: bytes) -> bytes:
    """Encode top-down 32-bit BGRA buffer into a standard 24-bit RGB PNG."""
    stride = width * 4
    raw_rows = bytearray()
    mv = memoryview(bgra_bytes)
    for y in range(height):
        raw_rows.append(0)  # filter type 0 (None)
        row = mv[y * stride : (y + 1) * stride]
        # Convert BGRA -> RGB row-wise
        rgb_row = bytearray(width * 3)
        rgb_row[0::3] = row[2::4]  # R
        rgb_row[1::3] = row[1::4]  # G
        rgb_row[2::3] = row[0::4]  # B
        raw_rows.extend(rgb_row)

    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag)
        crc = zlib.crc32(data, crc) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw_rows), level=6)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def capture_window_offscreen(hwnd: int, out_path: str | Path) -> CaptureResult:
    """Capture `hwnd` offscreen via PrintWindow(PW_RENDERFULLCONTENT) and save PNG."""
    target = Path(out_path)
    if not IS_WINDOWS or user32 is None:
        return CaptureResult(False, hwnd, 0, 0, str(target), 0, 0.0, "PrintWindow requires Windows OS")
    if not user32.IsWindow(hwnd):
        return CaptureResult(False, hwnd, 0, 0, str(target), 0, 0.0, "hwnd is not a valid window")

    # Restore if minimized (-32000) using SW_SHOWNOACTIVATE (4) so focus is NEVER stolen
    Win32Backend_restore = False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
        Win32Backend_restore = True

    rc = wt.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rc)):
        return CaptureResult(False, hwnd, 0, 0, str(target), 0, 0.0, "GetWindowRect failed")

    width = int(rc.right - rc.left)
    height = int(rc.bottom - rc.top)
    if width <= 0 or height <= 0:
        return CaptureResult(False, hwnd, width, height, str(target), 0, 0.0, "window has empty bounds")

    hdc_win = user32.GetWindowDC(hwnd)
    if not hdc_win:
        return CaptureResult(False, hwnd, width, height, str(target), 0, 0.0, "GetWindowDC failed")

    hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_win, width, height)
    old_obj = gdi32.SelectObject(hdc_mem, hbmp)
    try:
        ok = bool(user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT))
        if not ok:
            ok = bool(user32.PrintWindow(hwnd, hdc_mem, 0))

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height  # negative = top-down DIB
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB

        buf_len = width * height * 4
        buf = ctypes.create_string_buffer(buf_len)
        lines = gdi32.GetDIBits(hdc_mem, hbmp, 0, height, buf, ctypes.byref(bmi), DIB_RGB_COLORS)
        if lines != height:
            return CaptureResult(False, hwnd, width, height, str(target), 0, 0.0, f"GetDIBits returned {lines}/{height}")

        raw = buf.raw
        # Sample 1 out of every 64 pixels to compute non-black ratio fast
        sample_step = 256
        total_samples = max(1, len(raw) // sample_step)
        non_black = 0
        for idx in range(0, len(raw) - 3, sample_step):
            if raw[idx] or raw[idx + 1] or raw[idx + 2]:
                non_black += 1
        ratio = round(non_black / total_samples, 4)

        png_bytes = _encode_rgb_png(width, height, raw)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(png_bytes)
        return CaptureResult(
            ok=ok,
            hwnd=hwnd,
            width=width,
            height=height,
            path=str(target),
            size_bytes=len(png_bytes),
            non_black_ratio=ratio,
            detail="PrintWindow offscreen OK" + (" (restored iconic)" if Win32Backend_restore else ""),
        )
    finally:
        if old_obj:
            gdi32.SelectObject(hdc_mem, old_obj)
        if hbmp:
            gdi32.DeleteObject(hbmp)
        if hdc_mem:
            gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd, hdc_win)
