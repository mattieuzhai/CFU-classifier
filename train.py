from pathlib import Path
from ultralytics import YOLO

model = YOLO("yolo26n.pt")  # nano detection model

model.train(
    data=str(Path(__file__).parent / "data.yaml"),
    epochs=100,
    imgsz=640,
    batch=16,
    device=0,
    project="runs",
    name="cfu_detector",
    exist_ok=True,
)
