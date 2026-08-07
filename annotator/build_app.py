"""Build the CFU Annotator into a double-clickable app.

    python build_app.py

On macOS this produces `dist/CFU Annotator.app`; on Windows,
`dist/CFU Annotator/CFU Annotator.exe`. PyInstaller can only build for the
platform it runs on, so a Windows .exe must be built on Windows.

The build is large (torch and ultralytics are bundled whole) and takes a few
minutes. Afterwards the app is verified by running its own self-test.
"""

import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = HERE / "CFU_Annotator.spec"
DIST = HERE / "dist"
BUILD = HERE / "build_pyinstaller"
APP_NAME = "CFU Annotator"


def _size(path):
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return f"{total / 1e9:.2f} GB" if total > 1e9 else f"{total / 1e6:.0f} MB"


def _bundle_path():
    if sys.platform == "darwin":
        return DIST / f"{APP_NAME}.app"
    return DIST / APP_NAME


def _executable():
    if sys.platform == "darwin":
        return _bundle_path() / "Contents" / "MacOS" / APP_NAME
    if sys.platform.startswith("win"):
        return _bundle_path() / f"{APP_NAME}.exe"
    return _bundle_path() / APP_NAME


def build():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed. Install it with:\n")
        print(f"    {sys.executable} -m pip install pyinstaller\n")
        return 1

    print(f"Building {APP_NAME} with {sys.executable}")
    print("This bundles torch + ultralytics and takes a few minutes.\n")

    target = _bundle_path()
    if target.exists():
        print(f"Removing the previous build at {target}")
        shutil.rmtree(target)

    started = time.time()
    result = subprocess.run(
        [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm",
            "--distpath", str(DIST),
            "--workpath", str(BUILD),
            str(SPEC),
        ],
        cwd=HERE,
    )
    if result.returncode != 0:
        print("\nBuild FAILED — see the PyInstaller output above.")
        return result.returncode

    minutes = (time.time() - started) / 60
    print(f"\nBuilt {target}  ({_size(target)}, {minutes:.1f} min)")
    return 0


def verify(model=None):
    """Run the bundled app's own self-test, so a broken build can't slip past."""
    exe = _executable()
    if not exe.exists():
        print(f"Nothing to verify — {exe} does not exist.")
        return 1

    print(f"\nVerifying {exe.name} …\n")
    command = [str(exe), "--selftest"]
    if model:
        command.append(str(model))
    result = subprocess.run(command)
    print()
    if result.returncode == 0:
        print("The bundled app passed its self-test.")
        if sys.platform == "darwin":
            print(f"\nDouble-click  {_bundle_path()}  to run it.")
            print("Move it anywhere you like — it needs no Python installation.")
    else:
        print("The bundled app FAILED its self-test (see above).")
    return result.returncode


if __name__ == "__main__":
    args = sys.argv[1:]
    model = None
    # A model to exercise in the self-test, if one is sitting in the repo.
    for candidate in (HERE.parent / "nuc" / "best.pt",):
        if candidate.is_file():
            model = candidate
            break
    if args and args[0] == "--verify-only":
        sys.exit(verify(model))
    code = build()
    if code:
        sys.exit(code)
    sys.exit(verify(model))
