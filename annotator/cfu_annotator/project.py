"""Saving and reloading a counting session.

A project is a single JSON file (`.cfuproj`) holding the folders, the model, the
detection settings, and every box and status for every image. Boxes are stored
in image pixel coordinates, so a project stays valid even if the images are
later moved to a different folder — only the folder path needs re-pointing.
"""

import json
from datetime import datetime
from pathlib import Path

FORMAT = "cfu-annotator-project"
VERSION = 1
EXTENSION = ".cfuproj"
FILE_FILTER = f"CFU Annotator project (*{EXTENSION})"


class ProjectError(Exception):
    """The file isn't a project file, or can't be read."""


def _clean_boxes(boxes):
    out = []
    for box in boxes or []:
        xyxy = [float(v) for v in box["xyxy"]]
        conf = box.get("conf")
        entry = {
            "cls": int(box["cls"]),
            "conf": None if conf is None else round(float(conf), 4),
            "xyxy": [round(v, 2) for v in xyxy],
        }
        if box.get("unconfirmed"):
            entry["unconfirmed"] = True
        out.append(entry)
    return out


def save(path, *, image_folder, model_path, output_folder, export_options,
         detection, class_names, records, image_sizes,
         custom_classes=False, class_list_source=None):
    """Write the project. Returns the path actually written."""
    path = Path(path)
    if path.suffix != EXTENSION:
        path = path.with_suffix(EXTENSION)

    images = {}
    for name, record in sorted(records.items()):
        entry = {
            "boxes": _clean_boxes(record.get("boxes")),
            "annotated": bool(record.get("annotated")),
            "edited": bool(record.get("edited")),
            "finalized": bool(record.get("finalized")),
            "contaminated": bool(record.get("contaminated")),
        }
        if name in image_sizes:
            entry["size"] = list(image_sizes[name])
        if record.get("model"):
            entry["model"] = record["model"]
        if record.get("params"):
            entry["params"] = record["params"]
        images[name] = entry

    payload = {
        "format": FORMAT,
        "version": VERSION,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "image_folder": str(image_folder) if image_folder else None,
        "model_path": str(model_path) if model_path else None,
        "output_folder": str(output_folder) if output_folder else None,
        "export_options": dict(export_options),
        "detection": dict(detection),
        "class_names": list(class_names),
        "custom_classes": bool(custom_classes),
        "class_list_source": class_list_source,
        "images": images,
    }

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    tmp.replace(path)      # atomic: a crash mid-write can't corrupt the project
    return path


def load(path):
    """Read a project file into a plain dict. Raises ProjectError on junk."""
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProjectError(f"'{path}' does not exist.") from exc
    except json.JSONDecodeError as exc:
        raise ProjectError(
            f"'{path.name}' is not a valid project file (it isn't readable as "
            f"JSON).\n\n{exc}"
        ) from exc
    except OSError as exc:
        raise ProjectError(f"Could not read '{path}':\n\n{exc}") from exc

    if not isinstance(payload, dict) or payload.get("format") != FORMAT:
        raise ProjectError(
            f"'{path.name}' is not a CFU Annotator project file."
        )
    if payload.get("version", 0) > VERSION:
        raise ProjectError(
            f"'{path.name}' was saved by a newer version of the app "
            f"(project version {payload['version']}, this app understands "
            f"{VERSION}). Update the app to open it."
        )

    records, sizes = {}, {}
    for name, entry in (payload.get("images") or {}).items():
        records[name] = {
            "boxes": _clean_boxes(entry.get("boxes")),
            "annotated": bool(entry.get("annotated")),
            "edited": bool(entry.get("edited")),
            "finalized": bool(entry.get("finalized")),
            "contaminated": bool(entry.get("contaminated")),
            "model": entry.get("model"),
            "params": entry.get("params"),
        }
        size = entry.get("size")
        if size and len(size) == 2:
            sizes[name] = (int(size[0]), int(size[1]))

    return {
        "image_folder": payload.get("image_folder"),
        "model_path": payload.get("model_path"),
        "output_folder": payload.get("output_folder"),
        "export_options": payload.get("export_options") or {},
        "detection": payload.get("detection") or {},
        "class_names": payload.get("class_names") or [],
        "custom_classes": bool(payload.get("custom_classes")),
        "class_list_source": payload.get("class_list_source"),
        "records": records,
        "image_sizes": sizes,
        "saved_at": payload.get("saved_at"),
    }
