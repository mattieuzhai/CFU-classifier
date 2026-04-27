from pathlib import Path
from ultralytics import YOLO

model = YOLO("yolo26n.pt")  # nano detection model

model.train(
    data=str(Path(__file__).parent / "data.yaml"),
    epochs=100,
    imgsz=1600,
    batch=16,       # 1280px tiles use ~4x more VRAM than 640px; tune up if GPU allows
    device=0,
    project="1600px_training",
    name="cfu_detector",
    exist_ok=True,
)
