"""Loading a YOLO .pt file and running (optionally tiled) detection on a plate.

Heavy imports (ultralytics/torch) happen lazily inside functions so the GUI can
appear immediately instead of after a ten-second import.

The tiling + merge strategy here mirrors `nuc/model_handler.py`, which is what
the CVAT auto-annotation service uses: plate photos are ~6400x6400 with colonies
that are tiny relative to the frame, so a single downscaled pass loses them.
"""

import os
from pathlib import Path

import numpy as np

# Let unsupported Apple-GPU (MPS) ops fall back to CPU instead of raising, so
# Apple Silicon Macs can use the GPU without op-coverage surprises. Must be set
# before torch is imported (ultralytics/torch are imported lazily below).
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

# Detection settings that work for the whole-plate CFU model. These match the
# values deployed in nuc/function.yaml.
DEFAULT_CONF = 0.25
DEFAULT_TILE_SIZE = 1600
DEFAULT_TILE_OVERLAP = 0.20
DEFAULT_NMS_IOU = 0.40
DEFAULT_NMS_CONTAIN = 0.40


class ModelError(Exception):
    """The chosen file can't be used as a detection model."""


def _select_device():
    """Pick the fastest available torch device: CUDA > Apple MPS > CPU."""
    try:
        import torch
    except Exception:
        return "cpu"
    try:
        if torch.cuda.is_available():
            return "cuda"
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def _nms(boxes, scores, iou_thresh):
    """Class-agnostic NMS. Returns kept indices, highest score first."""
    boxes = np.asarray(boxes, dtype=np.float64)
    scores = np.asarray(scores, dtype=np.float64)
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    order = scores.argsort()[::-1]

    kept = []
    while order.size:
        i = order[0]
        kept.append(int(i))
        if order.size == 1:
            break
        rest = order[1:]
        ix1 = np.maximum(x1[i], x1[rest])
        iy1 = np.maximum(y1[i], y1[rest])
        ix2 = np.minimum(x2[i], x2[rest])
        iy2 = np.minimum(y2[i], y2[rest])
        inter = np.clip(ix2 - ix1, 0, None) * np.clip(iy2 - iy1, 0, None)
        union = areas[i] + areas[rest] - inter
        iou = np.where(union > 0, inter / np.maximum(union, 1e-9), 0.0)
        order = rest[iou <= iou_thresh]
    return kept


def _suppress_contained(boxes, candidates, contain_thresh):
    """Drop boxes mostly swallowed by an already-kept, higher-confidence box.

    Catches tile-edge fragments, whose IoU with the full colony box is too low
    for NMS to merge them.
    """
    kept = []
    for i in candidates:
        bi = boxes[i]
        area_i = max(0.0, bi[2] - bi[0]) * max(0.0, bi[3] - bi[1])
        swallowed = False
        for j in kept:
            bj = boxes[j]
            ix1, iy1 = max(bi[0], bj[0]), max(bi[1], bj[1])
            ix2, iy2 = min(bi[2], bj[2]), min(bi[3], bj[3])
            inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
            if area_i > 0 and inter / area_i > contain_thresh:
                swallowed = True
                break
        if not swallowed:
            kept.append(i)
    return kept


def _tile_starts(total, tile, step):
    """Tile start offsets along one axis; the last tile always reaches the edge."""
    if total <= tile:
        return [0]
    starts = list(range(0, total - tile + 1, step))
    if starts[-1] != total - tile:
        starts.append(total - tile)
    return starts


class Detector:
    """A loaded YOLO detection model plus its class names."""

    def __init__(self, weights_path):
        self.path = Path(weights_path)
        if self.path.suffix.lower() != ".pt":
            raise ModelError(
                f"'{self.path.name}' is not a .pt file.\n\n"
                "Choose a PyTorch weights file exported by Ultralytics YOLO "
                "(usually named best.pt or last.pt)."
            )
        if not self.path.is_file():
            raise ModelError(f"'{self.path}' does not exist.")

        try:
            from ultralytics import YOLO
        except ImportError as exc:  # pragma: no cover - environment problem
            raise ModelError(
                "The 'ultralytics' package is not installed in this Python "
                f"environment.\n\n({exc})"
            ) from exc

        try:
            self.model = YOLO(str(self.path))
        except Exception as exc:
            raise ModelError(
                f"Could not load '{self.path.name}' as a YOLO model.\n\n{exc}"
            ) from exc

        # Run on the GPU when there is one (Apple MPS on this Mac, CUDA on a
        # server); Ultralytics otherwise defaults to CPU.
        self.device = _select_device()

        self.task = getattr(self.model, "task", None) or "unknown"
        if self.task != "detect":
            raise ModelError(
                f"'{self.path.name}' is a YOLO '{self.task}' model.\n\n"
                "This app only supports detection models — the kind that draw "
                "bounding boxes around each colony.\n\n"
                + {
                    "classify": "A 'classify' model labels a whole image rather "
                                "than finding individual colonies, so it cannot "
                                "be used for counting.",
                    "segment": "A 'segment' model outputs masks rather than "
                               "boxes. Retrain or export a detection model.",
                    "pose": "A 'pose' model outputs keypoints rather than boxes.",
                    "obb": "An 'obb' model outputs rotated boxes, which this app "
                           "cannot edit. Use a standard detection model.",
                }.get(self.task, "Choose a detection model instead.")
            )

        names = self.model.names
        if isinstance(names, dict):
            self.class_names = [names[k] for k in sorted(names)]
        else:
            self.class_names = list(names)
        if not self.class_names:
            raise ModelError(
                f"No class names could be read from '{self.path.name}'."
            )

    def __repr__(self):
        return (
            f"<Detector {self.path.name} task={self.task} "
            f"device={self.device} classes={self.class_names}>"
        )

    # -- inference ----------------------------------------------------------

    def predict(
        self,
        image_path,
        conf=DEFAULT_CONF,
        tiling=True,
        tile_size=DEFAULT_TILE_SIZE,
        tile_overlap=DEFAULT_TILE_OVERLAP,
        nms_iou=DEFAULT_NMS_IOU,
        nms_contain=DEFAULT_NMS_CONTAIN,
        progress=None,
        should_stop=None,
    ):
        """Detect colonies in one image.

        Returns a list of dicts: {"cls": int, "conf": float, "xyxy": [x1,y1,x2,y2]}
        in full-image pixel coordinates.

        `progress(done, total)` is called as tiles complete; `should_stop()` is
        polled so the UI can cancel a long run.
        """
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = None  # plate photos are legitimately huge
        # convert() copies even when the mode already matches, which on a
        # 10400px plate means holding two 400 MB decodes at once — enough
        # memory pressure to make the whole machine crawl part-way through a
        # folder. Plate photos are RGB JPEGs, so the copy is almost always
        # pure waste. load() decodes in place and releases the file handle.
        image = Image.open(image_path)
        if image.mode != "RGB":
            converted = image.convert("RGB")
            image.close()
            image = converted
        else:
            image.load()
        width, height = image.size

        single_pass = not tiling or (width <= tile_size and height <= tile_size)

        if single_pass:
            if progress:
                progress(0, 1)
            result = self.model.predict(
                source=image, conf=conf, device=self.device, verbose=False
            )[0]
            dets = [
                {
                    "cls": int(b.cls[0]),
                    "conf": float(b.conf[0]),
                    "xyxy": [float(v) for v in b.xyxy[0].tolist()],
                }
                for b in result.boxes
            ]
            if progress:
                progress(1, 1)
            return dets

        step = max(1, int(tile_size * (1.0 - tile_overlap)))
        ys = _tile_starts(height, tile_size, step)
        xs = _tile_starts(width, tile_size, step)
        total = len(xs) * len(ys)

        boxes, scores, classes = [], [], []
        done = 0
        for y0 in ys:
            for x0 in xs:
                if should_stop and should_stop():
                    return None
                tile = image.crop(
                    (x0, y0, min(x0 + tile_size, width), min(y0 + tile_size, height))
                )
                result = self.model.predict(
                    source=tile, conf=conf, imgsz=tile_size,
                    device=self.device, verbose=False
                )[0]
                for b in result.boxes:
                    bx1, by1, bx2, by2 = b.xyxy[0].tolist()
                    boxes.append([bx1 + x0, by1 + y0, bx2 + x0, by2 + y0])
                    scores.append(float(b.conf[0]))
                    classes.append(int(b.cls[0]))
                done += 1
                if progress:
                    progress(done, total)

        if not boxes:
            return []

        keep = _suppress_contained(boxes, _nms(boxes, scores, nms_iou), nms_contain)
        return [
            {"cls": classes[i], "conf": scores[i], "xyxy": boxes[i]} for i in keep
        ]
