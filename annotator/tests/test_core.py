"""Logic tests: folder scanning, model validation, export files, projects.

Run with:  ../.venv/bin/python tests/test_core.py     (from annotator/)
"""

import csv as csvmod
import json
import os
import re
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt5.QtCore import Qt                                    # noqa: E402
from PyQt5.QtGui import QImage                                 # noqa: E402
from PyQt5.QtWidgets import QApplication                       # noqa: E402

from cfu_annotator import export, project, render, scan, status  # noqa: E402
from cfu_annotator.detector import (                           # noqa: E402
    Detector, ModelError, _nms, _suppress_contained, _tile_starts,
)

MODEL = ROOT.parent / "nuc" / "best.pt"
FAILURES = []

# Held at module scope on purpose. If the QApplication is garbage-collected the
# C++ object goes with it, and the next QFont/QFontMetrics call segfaults.
APP = QApplication.instance() or QApplication(sys.argv)


def check(name, condition, detail=""):
    print(("PASS  " if condition else "FAIL  ") + name + (f"   {detail}" if detail else ""))
    sys.stdout.flush()
    if not condition:
        FAILURES.append(name)


def skip(name, why):
    print(f"SKIP  {name}   ({why})")


# --------------------------------------------------------------- scanning

def test_scan():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        for n in ("a.jpg", "b.JPEG", "c.png", "d.PNG"):
            (d / n).write_bytes(b"x")
        for n in ("e.tif", "f.bmp", "g.webp"):
            (d / n).write_bytes(b"x")
        for n in ("notes.txt", "labels.csv", ".DS_Store", "._a.jpg"):
            (d / n).write_bytes(b"x")
        (d / "sub").mkdir()
        (d / "sub" / "deep.jpg").write_bytes(b"x")

        result = scan.scan_folder(d)
        check("scan: finds jpg/jpeg/png", len(result.images) == 4,
              [p.name for p in result.images])
        check("scan: flags unreadable image formats",
              len(result.unsupported_images) == 3,
              [p.name for p in result.unsupported_images])
        check("scan: ignores non-image files", len(result.ignored) == 2,
              [p.name for p in result.ignored])
        check("scan: skips hidden and AppleDouble files",
              not any(p.name.startswith(".") for p in
                      result.images + result.unsupported_images + result.ignored))
        check("scan: does not recurse",
              "deep.jpg" not in [p.name for p in result.images])
        check("scan: warning names the bad extensions", ".tif" in (result.warning or ""))
        for n in ("e.tif", "f.bmp", "g.webp"):
            (d / n).unlink()
        check("scan: no warning when every image is usable",
              scan.scan_folder(d).warning is None)


# ------------------------------------------------------------ detector maths

def test_detector_helpers():
    check("tiling: last tile reaches the edge",
          _tile_starts(6400, 1600, 1280)[-1] == 6400 - 1600, _tile_starts(6400, 1600, 1280))
    check("tiling: small image is a single tile", _tile_starts(800, 1600, 1280) == [0])
    boxes = [[0, 0, 10, 10], [1, 1, 11, 11], [100, 100, 110, 110]]
    check("nms: merges overlapping duplicates",
          sorted(_nms(boxes, [0.9, 0.8, 0.7], 0.5)) == [0, 2])
    check("nms: drops boxes swallowed by a bigger one",
          _suppress_contained([[0, 0, 100, 100], [10, 10, 30, 30]], [0, 1], 0.4) == [0])


def test_model_validation():
    try:
        Detector("/tmp/not-a-model.txt")
        check("model: rejects a non-.pt file", False)
    except ModelError as exc:
        check("model: rejects a non-.pt file", ".pt file" in str(exc))
    try:
        Detector("/tmp/definitely-missing-xyz.pt")
        check("model: rejects a missing file", False)
    except ModelError as exc:
        check("model: rejects a missing file", "does not exist" in str(exc))

    if not MODEL.is_file():
        skip("model: loads the CFU detector", f"{MODEL} not present")
        return None
    detector = Detector(MODEL)
    check("model: loads a detection model", detector.task == "detect")
    check("model: reads class names from the file",
          detector.class_names == ["BFU", "GM", "E", "GEMM"], detector.class_names)
    return detector


# ------------------------------------------------------------------ export

def _records():
    return {
        "p1.jpg": {"annotated": True, "edited": False, "finalized": False, "boxes": [
            {"cls": 0, "conf": 0.9, "xyxy": [10, 20, 50, 60]},
            {"cls": 0, "conf": 0.8, "xyxy": [100, 100, 140, 140]},
            {"cls": 3, "conf": 0.7, "xyxy": [200, 200, 260, 260]}]},
        "p2.jpg": {"annotated": True, "edited": True, "finalized": False, "boxes": []},
        "p4.jpg": {"annotated": True, "edited": False, "finalized": True, "boxes": [
            {"cls": 1, "conf": 0.6, "xyxy": [5, 5, 25, 25]}]},
    }


def test_export():
    names = ["p1.jpg", "p2.jpg", "p3.jpg", "p4.jpg"]
    classes = ["BFU", "GM", "E", "GEMM"]
    records = _records()
    sizes = {"p1.jpg": (1000, 800), "p2.jpg": (1000, 800), "p4.jpg": (500, 500)}

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        folder = export.make_export_dir(out)
        check("export: creates its own folder inside the chosen one",
              folder.parent == out and folder.is_dir(), folder.name)
        check("export: folder name is CFU_export_<date>_<time>",
              re.fullmatch(r"CFU_export_\d{4}-\d{2}-\d{2}_\d{4}", folder.name) is not None,
              folder.name)
        again = export.make_export_dir(out)
        check("export: a second export never reuses the folder", again != folder,
              (folder.name, again.name))

        export.write_csv(folder, names, classes, records)
        rows = list(csvmod.reader(open(folder / export.CSV_NAME)))
        check("csv: header is image + classes + total + status",
              rows[0] == ["image"] + classes + ["total", "status"], rows[0])
        by = {r[0]: r for r in rows[1:]}
        check("csv: one row per image in the folder", len(rows) == 5, len(rows))
        check("csv: counts per class", by["p1.jpg"][1:6] == ["2", "0", "0", "1", "3"],
              by["p1.jpg"])
        check("csv: annotated with no colonies", by["p2.jpg"][6] == "edited", by["p2.jpg"])
        check("csv: never-processed image is distinguishable",
              by["p3.jpg"][6] == "not_annotated", by["p3.jpg"])
        check("csv: finalized status survives export",
              by["p4.jpg"][6] == "finalized", by["p4.jpg"])

        target, count = export.write_yolo(folder, classes, records, sizes)
        check("yolo: one label file per annotated image", count == 3, count)
        check("yolo: classes.txt written",
              (target / "classes.txt").read_text().split() == classes)
        lines = (target / "p1.txt").read_text().strip().split("\n")
        check("yolo: one line per box", len(lines) == 3, lines)
        _, cx, cy, w, h = lines[0].split()
        check("yolo: normalised centre", abs(float(cx) - 30 / 1000) < 1e-6, cx)
        check("yolo: normalised size", abs(float(w) - 40 / 1000) < 1e-6, w)
        check("yolo: empty file when there are no boxes",
              (target / "p2.txt").read_text() == "")

        oob = {"x.jpg": {"annotated": True, "boxes": [
            {"cls": 1, "conf": None, "xyxy": [-50, -50, 200, 200]}]}}
        export.write_yolo(folder, classes, oob, {"x.jpg": (100, 100)})
        parts = (target / "x.txt").read_text().split()
        check("yolo: clamps boxes to the image", all(0 <= float(v) <= 1 for v in parts[1:]),
              parts)

        export.write_run_info(
            folder, app_version="test", project_path=None, image_folder="/imgs",
            image_names=names, records=records, class_names=classes,
            model_info={"path": "/m/best.pt", "task": "detect"},
            current_settings={"conf": 0.25, "tiling": True, "tile_size": 1600,
                              "tile_overlap": 0.2},
            outputs_written=[export.CSV_NAME, f"{export.YOLO_DIRNAME}/"],
        )
        info = (folder / export.INFO_NAME).read_text()
        check("run log: has a timestamp",
              re.search(r"Exported\s+: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", info) is not None)
        check("run log: names the model", "best.pt" in info and "YOLO detect" in info)
        check("run log: lists the classes", ", ".join(classes) in info)
        check("run log: records the status tally", "finalized" in info)
        check("run log: lists what was written", export.CSV_NAME in info)


def test_render():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        source = d / "plate.png"
        image = QImage(800, 600, QImage.Format_RGB32)
        image.fill(Qt.darkGray)
        image.save(str(source))

        check("render: output name gains _annotated",
              render.output_name("plate.png") == "plate_annotated.png",
              render.output_name("plate.png"))
        dest = d / render.output_name("plate.png")
        problem = render.render(
            source,
            [{"cls": 0, "conf": 0.9, "xyxy": [100, 100, 200, 200]},
             {"cls": 2, "conf": 0.5, "xyxy": [300, 250, 380, 330]}],
            ["BFU", "GM", "E", "GEMM"], dest,
        )
        check("render: reports no problem", problem is None, problem)
        check("render: writes the file", dest.is_file() and dest.stat().st_size > 1000)
        drawn = QImage(str(dest))
        check("render: same size as the source", drawn.size() == QImage(str(source)).size())
        check("render: pixels changed where a box was drawn",
              drawn.pixel(100, 102) != QImage(str(source)).pixel(100, 102))
        check("render: reports a missing source instead of raising",
              render.render(d / "nope.png", [], [], d / "out.png") is not None)


# ----------------------------------------------------------------- projects

def test_project():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        records = _records()
        saved = project.save(
            d / "session", image_folder="/imgs", model_path="/m/best.pt",
            output_folder="/out", export_options={"csv": True, "yolo": False},
            detection={"conf": 0.3, "tiling": True, "tile_size": 1280,
                       "tile_overlap": 0.15},
            class_names=["BFU", "GM", "E", "GEMM"], records=records,
            image_sizes={"p1.jpg": (1000, 800)},
        )
        check("project: extension is enforced", saved.suffix == ".cfuproj", saved.name)
        check("project: no temp file left behind", not list(d.glob("*.tmp")))

        back = project.load(saved)
        check("project: restores folders", back["image_folder"] == "/imgs")
        check("project: restores detection settings",
              back["detection"]["tile_size"] == 1280, back["detection"])
        check("project: restores class names",
              back["class_names"] == ["BFU", "GM", "E", "GEMM"])
        check("project: restores every image record",
              set(back["records"]) == set(records))
        check("project: restores box geometry",
              [b["xyxy"] for b in back["records"]["p1.jpg"]["boxes"]]
              == [b["xyxy"] for b in records["p1.jpg"]["boxes"]])
        check("project: restores statuses",
              status.status_of(back["records"]["p4.jpg"]) == status.FINALIZED)
        check("project: restores image sizes", back["image_sizes"]["p1.jpg"] == (1000, 800))

        bad = d / "bad.cfuproj"
        bad.write_text("{not json")
        try:
            project.load(bad)
            check("project: refuses malformed json", False)
        except project.ProjectError as exc:
            check("project: refuses malformed json", "not a valid project" in str(exc))

        foreign = d / "other.cfuproj"
        foreign.write_text('{"format": "something-else"}')
        try:
            project.load(foreign)
            check("project: refuses a foreign file", False)
        except project.ProjectError as exc:
            check("project: refuses a foreign file", "not a CFU Annotator" in str(exc))

        future = d / "future.cfuproj"
        future.write_text(json.dumps({"format": project.FORMAT, "version": 99}))
        try:
            project.load(future)
            check("project: refuses a newer format version", False)
        except project.ProjectError as exc:
            check("project: refuses a newer format version", "newer version" in str(exc))


def test_status_icons():
    for key in status.ORDER:
        check(f"icon: {key} renders", not status.status_icon(key).isNull())
    check("status: finalized outranks edited",
          status.status_of({"annotated": True, "edited": True, "finalized": True})
          == status.FINALIZED)
    check("status: edited outranks annotated",
          status.status_of({"annotated": True, "edited": True}) == status.EDITED)
    check("status: empty record is not annotated",
          status.status_of(None) == status.NOT_ANNOTATED)


def main():
    test_scan()
    test_detector_helpers()
    test_model_validation()
    test_export()
    test_render()
    test_project()
    test_status_icons()
    print()
    print("FAILURES:", FAILURES if FAILURES else "none")
    sys.stdout.flush()
    os._exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
