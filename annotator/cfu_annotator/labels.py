"""Reading existing YOLO label files back in, to pre-load earlier annotations.

A label file is a `.txt` next to (or in a folder mirroring) the images, named
after the image it belongs to: `plate1.jpg` -> `plate1.txt`. Each line is
`class_id centre_x centre_y width height`, normalised 0-1, which is what this
app and labelImg both write.

Anything else in the folder is ignored — `classes.txt`, notes, spreadsheets,
stray labels with no matching image. Malformed lines are skipped and counted
rather than aborting the import, because a folder that is 95% good is still
worth loading.
"""

from pathlib import Path

from PyQt5.QtGui import QImageReader

LABEL_SUFFIX = ".txt"

# labelImg and Ultralytics both drop these next to the labels; they are not
# annotations for any image.
SIDECAR_NAMES = {"classes.txt", "notes.txt", "predefined_classes.txt"}


def read_class_list(path):
    """Read a labelImg-style class list: one class name per line.

    Blank lines and `#` comments are skipped. Returns (names, skipped_count).
    Raises OSError if the file can't be read, ValueError if it holds no names.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    names, skipped = [], 0
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            skipped += 1
            continue
        if line in names:
            skipped += 1          # a duplicate would shadow an earlier index
            continue
        names.append(line)
    if not names:
        raise ValueError(
            f"'{Path(path).name}' holds no class names. Expected one name per "
            f"line, as labelImg writes classes.txt."
        )
    return names, skipped


class ImportReport:
    """What happened during an import, for a plain-English summary."""

    def __init__(self):
        self.loaded = {}          # image name -> list of boxes
        self.images_with_labels = 0
        self.images_without_labels = []
        self.empty_label_files = 0
        self.boxes = 0
        self.ignored_files = 0     # not a .txt, or a .txt matching no image
        self.bad_lines = 0
        self.unreadable = []       # (filename, reason)
        self.out_of_range_classes = set()

    def summary(self, class_names=None):
        parts = [
            f"Loaded {self.boxes} box(es) from {self.images_with_labels} "
            f"label file(s)."
        ]
        if self.empty_label_files:
            parts.append(
                f"{self.empty_label_files} label file(s) were empty (recorded "
                f"as annotated with zero colonies)."
            )
        if self.images_without_labels:
            parts.append(
                f"{len(self.images_without_labels)} image(s) had no label file "
                f"and stay unannotated."
            )
        if self.ignored_files:
            parts.append(f"{self.ignored_files} file(s) ignored (not labels).")
        if self.bad_lines:
            parts.append(f"{self.bad_lines} line(s) skipped as unreadable.")
        if self.unreadable:
            listed = ", ".join(f"{n} ({why})" for n, why in self.unreadable[:4])
            parts.append(f"Could not read: {listed}.")
        if self.out_of_range_classes:
            ids = ", ".join(str(i) for i in sorted(self.out_of_range_classes))
            known = f" The model has {len(class_names)}." if class_names else ""
            parts.append(
                f"Class id(s) {ids} are outside the current class list.{known}"
            )
        return "\n\n".join(parts)


def _parse(text, width, height, report):
    """YOLO lines -> boxes in pixel coordinates."""
    boxes = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 5:
            report.bad_lines += 1
            continue
        try:
            cls = int(float(parts[0]))
            cx, cy, bw, bh = (float(v) for v in parts[1:5])
        except ValueError:
            report.bad_lines += 1
            continue
        if bw <= 0 or bh <= 0:
            report.bad_lines += 1
            continue
        # Written normalised; convert back to this image's pixels.
        x1 = (cx - bw / 2) * width
        y1 = (cy - bh / 2) * height
        x2 = (cx + bw / 2) * width
        y2 = (cy + bh / 2) * height
        boxes.append({
            "cls": cls,
            "conf": None,             # imported, not a fresh prediction
            "xyxy": [
                max(0.0, min(x1, width)), max(0.0, min(y1, height)),
                max(0.0, min(x2, width)), max(0.0, min(y2, height)),
            ],
        })
    return boxes


def image_size(path, known=None):
    """Pixel size of an image, from the header only."""
    if known:
        return known
    size = QImageReader(str(path)).size()
    return (size.width(), size.height()) if size.isValid() else None


def import_folder(labels_folder, image_paths, class_count=None, known_sizes=None):
    """Load YOLO labels for `image_paths` from `labels_folder`.

    Returns an ImportReport. Boxes come back in pixel coordinates, so they need
    each image's dimensions — read from the file header, which is cheap even for
    a 10000-pixel plate.
    """
    folder = Path(labels_folder)
    report = ImportReport()
    known_sizes = known_sizes or {}

    by_stem = {p.stem: p for p in image_paths}
    used = set()

    for entry in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if entry.is_dir() or entry.name.startswith("."):
            continue
        if entry.suffix.lower() != LABEL_SUFFIX or entry.name in SIDECAR_NAMES:
            report.ignored_files += 1
            continue
        image_path = by_stem.get(entry.stem)
        if image_path is None:
            report.ignored_files += 1     # a label with no image of that name
            continue

        size = image_size(image_path, known_sizes.get(image_path.name))
        if not size or size[0] <= 0 or size[1] <= 0:
            report.unreadable.append((entry.name, "image size unknown"))
            continue
        try:
            text = entry.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            report.unreadable.append((entry.name, str(exc)))
            continue

        boxes = _parse(text, size[0], size[1], report)
        if class_count:
            for box in boxes:
                if not 0 <= box["cls"] < class_count:
                    report.out_of_range_classes.add(box["cls"])

        report.loaded[image_path.name] = boxes
        report.images_with_labels += 1
        report.boxes += len(boxes)
        if not boxes:
            report.empty_label_files += 1
        used.add(image_path.name)

    report.images_without_labels = [p.name for p in image_paths if p.name not in used]
    return report
