"""Saving and reloading a counting session.

A project is a single file (`.cfuproj`) holding the folders, the model, the
detection settings, and every box and status for every image. Boxes are stored
in image pixel coordinates, so a project stays valid even if the images are
later moved to a different folder — only the folder path needs re-pointing.

The file is gzipped JSON. Files written before compression was added are plain
JSON and still load — `load` tells them apart by looking at the first two bytes
— but a file written now will not open in a build older than 1.6.3.
"""

import gzip
import json
import zlib
from datetime import datetime
from pathlib import Path

#: gzip's own magic number. A project written before compression was added is
#: plain JSON and starts with '{', so the two are told apart by looking.
GZIP_MAGIC = b"\x1f\x8b"

#: Level 4 rather than gzip's default 6: on a 300,000-box project it gives
#: 5.9 MB against 5.6 MB, for half the time. Level 9 buys another 1% for twice
#: as long again. Saving already blocks the window, so the milliseconds count
#: for more here than the last few hundred kilobytes.
COMPRESSION = 4

FORMAT = "cfu-annotator-project"
VERSION = 1
EXTENSION = ".cfuproj"
FILE_FILTER = f"CFU Annotator project (*{EXTENSION})"


class ProjectError(Exception):
    """The file isn't a project file, or can't be read."""


def _clean_box(box):
    """Normalise one box: known types, and coordinates rounded to 0.01 px."""
    conf = box.get("conf")
    entry = {
        "cls": int(box["cls"]),
        "conf": None if conf is None else round(float(conf), 4),
        "xyxy": [round(float(v), 2) for v in box["xyxy"]],
    }
    if box.get("unconfirmed"):
        entry["unconfirmed"] = True
    return entry


def _clean_boxes(boxes):
    return [_clean_box(box) for box in boxes or []]


def _is_clean(box):
    """True if a parsed box is already exactly what _clean_box would produce.

    Bools are excluded deliberately: `type(True) is int` is False, so a box
    carrying `true` as a class takes the repair path rather than sneaking past.
    """
    if type(box) is not dict:
        return False
    xyxy = box.get("xyxy")
    return (
        type(box.get("cls")) is int
        and type(xyxy) is list
        and len(xyxy) == 4
        and all(type(v) is float or type(v) is int for v in xyxy)
    )


def _adopt_boxes(raw, image_name):
    """Take boxes from a parsed project, checking them without rebuilding them.

    `save` rounds and types every value on the way out, so for a file this app
    wrote, `json.loads` has already produced precisely the dicts wanted here.
    Rebuilding all of them used to cost more than parsing the whole file did —
    most of the time spent opening a large project — so a box that is already
    in shape is adopted as it stands.

    Anything else is put through `_clean_box`, and anything that cannot be
    repaired raises rather than going quietly missing: a project that silently
    lost a colony would be worse than one that refuses to open.
    """
    out = []
    for index, box in enumerate(raw or []):
        if _is_clean(box):
            out.append(box)
            continue
        if not isinstance(box, dict):
            raise ProjectError(
                f"Box {index + 1} of '{image_name}' is not a box — the file has "
                f"a {type(box).__name__} where a box should be.\n\n"
                "The project file may have been edited by hand or truncated."
            )
        try:
            repaired = _clean_box(box)
            if len(repaired["xyxy"]) != 4:
                raise ValueError(
                    f"a box needs four coordinates, got {len(repaired['xyxy'])}"
                )
            out.append(repaired)
        except (AttributeError, TypeError, ValueError, KeyError, IndexError) as exc:
            raise ProjectError(
                f"Box {index + 1} of '{image_name}' could not be read: {exc}.\n\n"
                "The project file may have been edited by hand or truncated."
            ) from exc
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
    # Compact, not indented: a project for a full experiment runs to hundreds of
    # thousands of boxes, and indenting it made half the file spaces and
    # newlines, for formatting nobody ever reads. Then gzipped, because JSON
    # this repetitive compresses better than three to one. `load` still reads
    # the plain files older versions wrote; the reverse is not true, which is
    # the deliberate trade.
    blob = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    tmp.write_bytes(gzip.compress(blob, COMPRESSION))
    tmp.replace(path)      # atomic: a crash mid-write can't corrupt the project
    return path


def load(path):
    """Read a project file into a plain dict. Raises ProjectError on junk."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise ProjectError(f"'{path}' does not exist.") from exc
    except OSError as exc:
        raise ProjectError(f"Could not read '{path}':\n\n{exc}") from exc

    if raw[:2] == GZIP_MAGIC:
        try:
            raw = gzip.decompress(raw)
        except (OSError, EOFError, zlib.error) as exc:
            raise ProjectError(
                f"'{path.name}' is compressed but could not be unpacked — the "
                f"file looks truncated or damaged.\n\n{exc}"
            ) from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProjectError(
            f"'{path.name}' is not a valid project file (it isn't readable as "
            f"JSON).\n\n{exc}"
        ) from exc

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
            "boxes": _adopt_boxes(entry.get("boxes"), name),
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
