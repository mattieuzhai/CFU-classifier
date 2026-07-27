"""Entry point.  Run with:  python -m app   (from the CFU-classifier folder)"""

import sys


def _check_dependencies():
    """Fail with a readable message rather than a traceback."""
    missing = []
    for module, package in (
        ("PyQt5", "PyQt5"),
        ("numpy", "numpy"),
        ("PIL", "pillow"),
        ("ultralytics", "ultralytics"),
    ):
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    if missing:
        print(
            "CFU Annotator can't start — these Python packages are missing:\n\n"
            f"    {', '.join(missing)}\n\n"
            "Install them with:\n\n"
            f"    {sys.executable} -m pip install {' '.join(missing)}\n",
            file=sys.stderr,
        )
        return False
    return True


def _selftest(model_path=None):
    """Check that this build can actually run: Qt, the UI, torch, ultralytics.

    Used to verify a bundled (.app / .exe) build, where a missing data file or
    hidden import only shows up at runtime. Pass a .pt file to also exercise a
    real detection.
    """
    import os
    import tempfile

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    failures = []

    def step(name, fn):
        try:
            detail = fn()
        except Exception as exc:                       # noqa: BLE001
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
            failures.append(name)
        else:
            print(f"PASS  {name}" + (f"   {detail}" if detail else ""))

    from . import APP_NAME, __version__
    print(f"{APP_NAME} {__version__} self-test")
    print(f"python  {sys.version.split()[0]}")
    print(f"frozen  {getattr(sys, 'frozen', False)}")

    state = {}

    def make_app():
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QtCore import QT_VERSION_STR
        state["app"] = QApplication.instance() or QApplication([])
        return f"Qt {QT_VERSION_STR}"

    def build_window():
        from .mainwindow import MainWindow
        state["win"] = MainWindow()
        return "main window constructed"

    def import_torch():
        import torch
        return f"torch {torch.__version__}"

    def import_ultralytics():
        import ultralytics
        return f"ultralytics {ultralytics.__version__}"

    def draw_icons():
        from .status import ORDER, status_icon
        for key in ORDER:
            assert not status_icon(key).isNull(), key
        return f"{len(ORDER)} status icons"

    def project_roundtrip():
        from . import project
        with tempfile.TemporaryDirectory() as tmp:
            path = project.save(
                os.path.join(tmp, "t"), image_folder="/x", model_path=None,
                output_folder=None, export_options={"csv": True},
                detection={"conf": 0.25}, class_names=["A"],
                records={"a.png": {"boxes": [{"cls": 0, "conf": 0.5,
                                              "xyxy": [1, 2, 3, 4]}],
                                   "annotated": True}},
                image_sizes={"a.png": (10, 10)},
            )
            back = project.load(path)
            assert back["records"]["a.png"]["annotated"]
        return "save/load ok"

    step("Qt available", make_app)
    step("UI builds", build_window)
    step("torch imports", import_torch)
    step("ultralytics imports", import_ultralytics)
    step("status icons render", draw_icons)
    step("project save/load", project_roundtrip)

    if model_path:
        def run_detection():
            import numpy as np
            from PIL import Image

            from .detector import Detector

            detector = Detector(model_path)
            with tempfile.TemporaryDirectory() as tmp:
                sample = os.path.join(tmp, "sample.png")
                Image.fromarray(
                    np.random.randint(90, 170, (900, 900, 3), dtype=np.uint8)
                ).save(sample)
                found = detector.predict(sample, conf=0.25, tiling=False)
            return f"{detector.task}, {len(detector.class_names)} classes, " \
                   f"{len(found)} detection(s) on a test image"

        step("model loads and detects", run_detection)

    print()
    print("SELF-TEST:", "FAILED " + ", ".join(failures) if failures else "OK")
    return 1 if failures else 0


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "--selftest":
        return _selftest(argv[1] if len(argv) > 1 else None)

    if not _check_dependencies():
        return 1

    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication

    from . import APP_NAME
    from .mainwindow import MainWindow

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)

    window = MainWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
