# -*- coding: utf-8 -*-
"""Static check: no module-level use of Windows-only ctypes surface.

Why static instead of "run the suite on Linux": this repo's CI leg is what would
prove that, and we cannot emulate a real posix build from a Windows interpreter
(`os.name` drives pathlib's concrete class, so faking it breaks at import).

What this does prove: every reference to `ctypes.windll`, `ctypes.WinDLL`,
`ctypes.WINFUNCTYPE` or `ctypes.wintypes` is either
  (a) inside a top-level `if` whose condition mentions the platform guard, or
  (b) not at module level at all (i.e. inside a function/class body).

Exit code 0 = clean, 1 = violations found (printed as path:line).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = sorted((ROOT / "src" / "agent_control_fabric").glob("*.py")) + \
    sorted((ROOT / "tests").glob("*.py"))
FORBIDDEN_ATTRS = {"windll", "WinDLL", "WINFUNCTYPE", "wintypes", "WinError"}
GUARD_HINTS = {"IS_WINDOWS", "name", "platform"}  # os.name / sys.platform / IS_WINDOWS


def _mentions_platform(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in GUARD_HINTS:
            return True
        if isinstance(sub, ast.Attribute) and sub.attr in GUARD_HINTS:
            return True
    return False


def _windows_refs(node: ast.AST):
    """Yield (lineno, dotted-name) for every Windows-only ctypes reference."""
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Attribute):
            continue
        if sub.attr not in FORBIDDEN_ATTRS:
            continue
        chain, cur = [sub.attr], sub.value
        while isinstance(cur, ast.Attribute):
            chain.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name) and cur.id == "ctypes":
            yield sub.lineno, ".".join(reversed(chain))


def _label(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def check_file(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    problems: list[str] = []
    for stmt in tree.body:
        if isinstance(stmt, ast.If) and _mentions_platform(stmt.test):
            continue  # platform-guarded block: allowed
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue  # not module level, evaluated only when called
        for lineno, name in _windows_refs(stmt):
            problems.append(f"{_label(path)}:{lineno}: module-level {name}")
    return problems


def main() -> int:
    problems: list[str] = []
    for path in TARGETS:
        problems.extend(check_file(path))
    print(f"SCANNED: {len(TARGETS)} files")
    if problems:
        print(f"VIOLATIONS: {len(problems)}")
        for line in problems:
            print("  " + line)
        return 1
    print("VIOLATIONS: 0 (all Windows-only ctypes use is platform-guarded or call-time)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
