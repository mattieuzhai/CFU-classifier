"""Run every test file and report a summary.

    ../.venv/bin/python tests/run_all.py        (from annotator/)

Each file runs in its own process: they build QApplications and load torch, and
a crash in one shouldn't take the others with it.
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = ["test_core.py", "test_canvas_geometry.py", "test_ui.py", "test_v4.py", "test_v5.py", "test_inference.py", "test_v6.py", "test_v7.py", "test_v8.py", "test_v9.py", "test_v10.py", "test_v11.py"]


def main():
    results = []
    for name in FILES:
        path = HERE / name
        if not path.is_file():
            print(f"MISSING  {name}")
            results.append((name, None, 0))
            continue
        print(f"\n=== {name} " + "=" * (60 - len(name)))
        sys.stdout.flush()
        proc = subprocess.run(
            [sys.executable, str(path)], cwd=HERE.parent,
            capture_output=True, text=True,
        )
        passed = sum(1 for line in proc.stdout.splitlines() if line.startswith("PASS"))
        failed = [line for line in proc.stdout.splitlines() if line.startswith("FAIL ")]
        for line in failed:
            print(line)
        if proc.returncode not in (0, 1):
            print(f"  process exited with code {proc.returncode} (segfault?)")
            print(proc.stdout[-2000:])
            print(proc.stderr[-2000:])
        print(f"  {passed} passed, {len(failed)} failed")
        results.append((name, proc.returncode, passed))

    print("\n" + "=" * 68)
    total = sum(p for _, _, p in results)
    bad = [n for n, code, _ in results if code != 0]
    for name, code, passed in results:
        print(f"  {'ok  ' if code == 0 else 'FAIL'}  {name:<28} {passed:>4} checks")
    print(f"\n{total} checks passed across {len(results)} files")
    if bad:
        print("FAILED:", ", ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
