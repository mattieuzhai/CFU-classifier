"""
Model wrapper for the CFU colony YOLO detector, with optional tiling.

The plate images are very large (6400x6400). Running the model on the whole
downscaled image shrinks the small colonies and hurts recall. Instead we slice
the image into overlapping tiles, run inference on each tile at (near) native
resolution, shift the detections back into full-image coordinates, and merge
them with a global per-class NMS to drop duplicates in the overlap regions.

Everything is controlled by env vars (set in function.yaml):

    YOLO_TILING       "1" to enable tiling (default), "0" for a single pass
    YOLO_TILE_SIZE    tile size in pixels (default 1600)
    YOLO_TILE_OVERLAP fractional overlap between tiles, 0-0.9 (default 0.2)
    YOLO_IMGSZ        imgsz fed to the model. Per tile when tiling; for the whole
                      image when not. Defaults to the tile size when tiling.
    YOLO_NMS_IOU      IoU threshold for merging across tiles (default 0.5)
"""

import os

import torch
from PIL import Image
from torchvision.ops import nms
from ultralytics import YOLO

TILING = os.environ.get("YOLO_TILING", "1") == "1"
TILE_SIZE = int(os.environ.get("YOLO_TILE_SIZE", "1600"))
TILE_OVERLAP = float(os.environ.get("YOLO_TILE_OVERLAP", "0.2"))
NMS_IOU = float(os.environ.get("YOLO_NMS_IOU", "0.45"))
# A box whose area is more than this fraction covered by a higher-confidence
# box is treated as a duplicate/fragment and dropped (catches tile-edge pieces
# that NMS misses because their IoU with the full box is low).
NMS_CONTAIN = float(os.environ.get("YOLO_NMS_CONTAIN", "0.6"))
# imgsz fed to predict(). Falls back to the tile size (native) when tiling.
_IMGSZ_ENV = os.environ.get("YOLO_IMGSZ")
IMGSZ = int(_IMGSZ_ENV) if _IMGSZ_ENV else None


class ModelHandler:
    def __init__(self, weights_path=None):
        if weights_path is None:
            weights_path = os.path.join(os.path.dirname(__file__), "best.pt")
        self.model = YOLO(weights_path)
        # {0: 'BFU', 1: 'GM', 2: 'E', 3: 'GEMM'}
        self.labels = self.model.names

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _tile_starts(total, tile, step):
        """Start coords along one axis, guaranteeing the final tile reaches the edge."""
        if total <= tile:
            return [0]
        starts = list(range(0, total - tile + 1, step))
        if starts[-1] != total - tile:
            starts.append(total - tile)
        return starts

    def _predict(self, img, conf, imgsz):
        kwargs = dict(source=img, conf=conf, verbose=False)
        if imgsz:
            kwargs["imgsz"] = imgsz
        return self.model.predict(**kwargs)[0]

    @staticmethod
    def _merge(boxes, scores, iou, contain):
        """Class-agnostic de-duplication of detections gathered from all tiles.

        1. Class-agnostic NMS: overlapping boxes are merged regardless of label,
           keeping the highest-confidence one (so a colony detected as two
           different classes in two tiles no longer yields two boxes).
        2. Containment pass: drop any remaining box whose own area is mostly
           inside an already-kept, higher-confidence box (tile-edge fragments).
        """
        boxes_t = torch.tensor(boxes, dtype=torch.float32)
        scores_t = torch.tensor(scores, dtype=torch.float32)

        # Step 1 — class-agnostic NMS, candidates ordered by confidence desc.
        candidates = nms(boxes_t, scores_t, iou).tolist()

        # Step 2 — containment suppression.
        kept = []
        for i in candidates:
            bi = boxes[i]
            area_i = max(0.0, bi[2] - bi[0]) * max(0.0, bi[3] - bi[1])
            duplicate = False
            for j in kept:
                bj = boxes[j]
                ix1, iy1 = max(bi[0], bj[0]), max(bi[1], bj[1])
                ix2, iy2 = min(bi[2], bj[2]), min(bi[3], bj[3])
                inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
                if area_i > 0 and inter / area_i > contain:
                    duplicate = True
                    break
            if not duplicate:
                kept.append(i)
        return kept

    def _to_cvat(self, x1, y1, x2, y2, score, class_id):
        return {
            "confidence": str(score),
            "label": self.labels[class_id],
            "points": [x1, y1, x2, y2],
            "type": "rectangle",
        }

    # -- public API ---------------------------------------------------------

    def infer(self, image: Image.Image, threshold: float):
        width, height = image.size

        # Single pass: tiling disabled, or the image already fits in one tile.
        if not TILING or (width <= TILE_SIZE and height <= TILE_SIZE):
            result = self._predict(image, threshold, IMGSZ)
            detections = []
            for box in result.boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                detections.append(
                    self._to_cvat(x1, y1, x2, y2, float(box.conf[0]), int(box.cls[0]))
                )
            return detections

        # Tiled inference.
        step = max(1, int(TILE_SIZE * (1.0 - TILE_OVERLAP)))
        per_tile_imgsz = IMGSZ or TILE_SIZE

        boxes, scores, classes = [], [], []
        for y0 in self._tile_starts(height, TILE_SIZE, step):
            for x0 in self._tile_starts(width, TILE_SIZE, step):
                x1, y1 = min(x0 + TILE_SIZE, width), min(y0 + TILE_SIZE, height)
                tile = image.crop((x0, y0, x1, y1))
                result = self._predict(tile, threshold, per_tile_imgsz)
                for box in result.boxes:
                    bx1, by1, bx2, by2 = box.xyxy[0].tolist()
                    # shift tile-local coords back to full-image coords
                    boxes.append([bx1 + x0, by1 + y0, bx2 + x0, by2 + y0])
                    scores.append(float(box.conf[0]))
                    classes.append(int(box.cls[0]))

        if not boxes:
            return []

        detections = []
        for i in self._merge(boxes, scores, NMS_IOU, NMS_CONTAIN):
            x1, y1, x2, y2 = boxes[i]
            detections.append(self._to_cvat(x1, y1, x2, y2, scores[i], classes[i]))
        return detections
