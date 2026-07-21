from pathlib import Path
from ultralytics import YOLO

# Augmentation A/B experiment vs the baseline train.py.
# Everything (data, model, imgsz, epochs, batch) is held identical to the
# baseline tiled run so the ONLY variable is augmentation:
#   - hsv_s lowered 0.7 -> 0.35   : erythroid (E) / BFU colonies are identified
#                                   by red hemoglobin color; strong saturation
#                                   jitter erases the E-vs-GM color cue.
#   - auto_augment disabled       : randaugment adds more color ops on top.
#   - flipud 0 -> 0.5, degrees    : plates have no canonical orientation, so
#     0 -> 180                       vertical flip + full rotation are free,
#                                   domain-valid variety.
# Writes to its own project folder so it never overwrites the baseline v4 run.

model = YOLO("yolo26n.pt")  # nano detection model

model.train(
    data=str(Path(__file__).parent / "data.yaml"),  # same tiled dataset as baseline
    epochs=100,
    imgsz=3200,
    batch=16,
    device=[0, 1],   # 2 GPUs (matches train.bsub num=2)

    # --- augmentation overrides (the experiment) ---
    hsv_h=0.015,        # keep hue shift tiny — never turn red into green
    hsv_s=0.35,         # was 0.7 — ease off, colony class is color-dependent
    hsv_v=0.4,          # brightness jitter, fine (mimics lighting)
    auto_augment=None,  # was 'randaugment' — drop extra color ops
    flipud=0.5,         # plate has no up/down — free variety
    fliplr=0.5,
    degrees=180.0,      # round plate is rotation-invariant — free variety

    project="3200px_aug_training",
    name="cfu_detector",
    exist_ok=True,
)
