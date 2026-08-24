"""Writing annotations out: a count summary (.csv), YOLO labels, and a run log."""

import csv
from datetime import datetime
from pathlib import Path

from .status import CONTAMINATED, ORDER as STATUS_ORDER, status_of

CSV_NAME = "CFU_counts.csv"
YOLO_DIRNAME = "yolo_labels"
IMAGES_DIRNAME = "annotated_images"
INFO_NAME = "export_info.txt"

EXPORT_PREFIX = "CFU_export"

# Characters a folder name can't contain (or that make paths miserable).
_ILLEGAL = set('/\\:*?"<>|')


def export_dir_name(when=None):
    """Timestamped folder name, e.g. CFU_export_2026-07-27_1642."""
    when = when or datetime.now()
    return f"{EXPORT_PREFIX}_{when.strftime('%Y-%m-%d_%H%M')}"


def clean_folder_name(name):
    """Turn whatever the user typed into a usable folder name, or '' if empty.

    Keeps their wording — spaces and dashes included — but drops path
    separators and characters that break on other platforms, so a name typed
    here still opens on a Windows share.
    """
    if not name:
        return ""
    cleaned = "".join("-" if ch in _ILLEGAL else ch for ch in str(name))
    cleaned = cleaned.replace("\n", " ").replace("\t", " ").strip(" .-")
    return cleaned[:120].strip(" .-")


def make_export_dir(output_folder, name=None, when=None):
    """Create a fresh, uniquely named folder for one export.

    Uses `name` when given, otherwise a timestamp. Every export gets its own
    folder, so a re-run never quietly overwrites numbers someone already
    pasted into a figure: a name that is already taken gains a -2 suffix
    rather than clobbering what is there.
    """
    parent = Path(output_folder)
    base = clean_folder_name(name) or export_dir_name(when)
    target = parent / base
    attempt = 2
    while target.exists():
        target = parent / f"{base}-{attempt}"
        attempt += 1
    target.mkdir(parents=True)
    return target


def csv_path(export_folder):
    return Path(export_folder) / CSV_NAME


def yolo_dir(export_folder):
    return Path(export_folder) / YOLO_DIRNAME


def images_dir(export_folder):
    return Path(export_folder) / IMAGES_DIRNAME


def info_path(export_folder):
    return Path(export_folder) / INFO_NAME


def write_csv(output_folder, image_names, class_names, records):
    """One row per image, one column per class, plus a total and a status flag.

    Images that were never run through the model still get a row, with zeros and
    status 'not_annotated', so a zero count is never mistaken for a plate that
    simply wasn't processed.
    """
    target = csv_path(output_folder)
    target.parent.mkdir(parents=True, exist_ok=True)

    header = ["image"] + list(class_names) + ["total", "status"]
    with open(target, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        for name in image_names:
            record = records.get(name)
            boxes = record["boxes"] if record else []
            counts = [0] * len(class_names)
            for box in boxes:
                if 0 <= box["cls"] < len(counts):
                    counts[box["cls"]] += 1
            writer.writerow(
                [name] + counts + [sum(counts), status_of(record)]
            )
    return target


def _format_params(params):
    if not params:
        return ["    (not recorded)"]
    tiling = params.get("tiling", True)
    lines = [
        f"    Confidence threshold : {params.get('conf', '?')}",
        f"    Tiling               : {'on' if tiling else 'off'}",
    ]
    if tiling:
        lines += [
            f"    Tile size            : {params.get('tile_size', '?')} px",
            f"    Tile overlap         : {params.get('tile_overlap', '?')}",
        ]
    return lines


def write_run_info(output_folder, *, app_version, project_path, image_folder,
                   image_names, records, class_names, model_info,
                   current_settings, outputs_written):
    """A plain-text record of how this export was produced.

    Detection settings are read back from the images themselves rather than from
    whatever the sliders happen to say at export time, so if settings were
    changed part-way through a session every set actually used is listed.
    """
    target = info_path(output_folder)
    target.parent.mkdir(parents=True, exist_ok=True)

    counted = {name: records.get(name) for name in image_names}
    annotated = [n for n, r in counted.items() if r and r.get("annotated")]
    by_status = {}
    for record in counted.values():
        key = status_of(record)
        by_status[key] = by_status.get(key, 0) + 1

    lines = [
        "CFU Annotator — export record",
        "=" * 60,
        "",
        f"Exported          : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"App version       : {app_version}",
        f"Project file      : {project_path or '(not saved as a project)'}",
        "",
        "MODEL",
        "-" * 60,
    ]
    if model_info:
        lines += [
            f"File              : {model_info.get('path', '?')}",
            f"Type              : YOLO {model_info.get('task', '?')}",
            f"Classes           : {', '.join(class_names)}",
        ]
    else:
        lines.append("No model was loaded for this export.")

    lines += ["", "DETECTION PARAMETERS", "-" * 60]

    # Group annotated images by the settings that produced them.
    groups = {}
    for name in annotated:
        record = counted[name]
        key = (
            record.get("model") or "(unknown model)",
            json_key(record.get("params")),
        )
        groups.setdefault(key, []).append(name)

    if not groups:
        lines += [
            "No images were annotated by the model in this project.",
            "",
            "Settings currently selected in the app:",
        ] + _format_params(current_settings)
    elif len(groups) == 1:
        (model_name, params_key), names = next(iter(groups.items()))
        lines += _format_params(_unkey(params_key))
        lines.append(f"    Applied to           : {len(names)} image(s)")
    else:
        lines.append(
            f"Settings were changed during this session — {len(groups)} "
            "different sets were used:"
        )
        for index, ((model_name, params_key), names) in enumerate(
            sorted(groups.items(), key=lambda kv: -len(kv[1])), start=1
        ):
            lines += ["", f"  Set {index} — model {model_name}"]
            lines += _format_params(_unkey(params_key))
            lines.append(f"    Applied to           : {len(names)} image(s)")
            for name in sorted(names):
                lines.append(f"        {name}")

    lines += [
        "",
        "IMAGES",
        "-" * 60,
        f"Image folder      : {image_folder}",
        f"Images in folder  : {len(image_names)}",
    ]
    for key in STATUS_ORDER:
        if key in by_status:
            lines.append(f"  {key:<16}: {by_status[key]}")

    lines += ["", "FILES WRITTEN", "-" * 60]
    lines += [f"  {item}" for item in outputs_written] or ["  (none)"]
    lines += [f"  {INFO_NAME}  (this file)", ""]

    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def json_key(params):
    """Hashable, stable representation of a settings dict."""
    if not params:
        return ()
    return tuple(sorted((k, v) for k, v in params.items()))


def _unkey(key):
    return dict(key)


def write_yolo(output_folder, class_names, records, image_sizes):
    """Standard YOLO detection labels: one .txt per annotated image.

    Each line is `class_id cx cy w h`, normalised to 0-1. Also writes a
    `classes.txt` so the folder opens straight into labelImg.
    """
    target = yolo_dir(output_folder)
    target.mkdir(parents=True, exist_ok=True)

    written = 0
    for name, record in records.items():
        if not record.get("annotated"):
            continue
        if status_of(record) == CONTAMINATED:
            continue        # a discarded plate must not become training data
        size = image_sizes.get(name)
        if not size:
            continue
        width, height = size
        if width <= 0 or height <= 0:
            continue

        lines = []
        for box in record["boxes"]:
            x1, y1, x2, y2 = box["xyxy"]
            x1, x2 = sorted((max(0.0, x1), min(float(width), x2)))
            y1, y2 = sorted((max(0.0, y1), min(float(height), y2)))
            bw, bh = (x2 - x1) / width, (y2 - y1) / height
            cx, cy = (x1 + x2) / 2 / width, (y1 + y2) / 2 / height
            if bw <= 0 or bh <= 0:
                continue
            lines.append(
                f"{box['cls']} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"
            )

        (target / f"{Path(name).stem}.txt").write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
        )
        written += 1

    (target / "classes.txt").write_text(
        "\n".join(class_names) + "\n", encoding="utf-8"
    )
    return target, written
