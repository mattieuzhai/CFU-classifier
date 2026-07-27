# PyInstaller build recipe for the CFU Annotator.
#
# Build with:   python build_app.py
# or directly:  pyinstaller --noconfirm CFU_Annotator.spec
#
# The same spec produces a macOS .app bundle and, when run on Windows, a
# CFU Annotator.exe — PyInstaller can only build for the OS it runs on, so a
# Windows executable has to be built on a Windows machine.

from PyInstaller.utils.hooks import collect_all

APP_NAME = "CFU Annotator"

datas, binaries, hiddenimports = [], [], []

# Ultralytics loads model configs from yaml files at runtime, so its package
# data has to come along, not just its modules.
for package in ("ultralytics",):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# Whole packages nothing in this app touches. Everything ultralytics needs at
# inference time (torch, torchvision, numpy, PIL, matplotlib, pandas, cv2) is
# deliberately left in — trimming those breaks prediction at runtime.
#
# Only ever exclude top-level packages. Excluding submodules of torch (e.g.
# torch.testing, torch.distributions) breaks it: torch imports them itself, and
# the partial import leaves its C extensions half-registered, which surfaces as
# "cannot initialize type RpcBackendOptions: already defined".
EXCLUDES = [
    "tkinter", "PyQt6", "PySide2", "PySide6",
    "gradio", "gradio_client", "fastapi", "uvicorn", "starlette",
    "IPython", "ipykernel", "jupyter", "jupyter_client", "jupyter_core",
    "notebook", "nbconvert", "nbformat", "zmq",
    "sahi", "ensemble_boxes", "pybboxes", "polars", "sklearn",
    "numba", "llvmlite", "tensorboard", "wandb",
]

a = Analysis(
    ["cfu_annotator.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # no terminal window when double-clicked
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,       # builds for the machine doing the building
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)

app = BUNDLE(
    coll,
    name=f"{APP_NAME}.app",
    icon=None,
    bundle_identifier="org.stjude.cfu-annotator",
    info_plist={
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleShortVersionString": "1.1.0",
        "CFBundleVersion": "1.1.0",
        "NSHighResolutionCapable": True,
        # Opening a .cfuproj from Finder should start the app.
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "CFU Annotator project",
                "CFBundleTypeExtensions": ["cfuproj"],
                "CFBundleTypeRole": "Editor",
                "LSHandlerRank": "Owner",
            }
        ],
    },
)
