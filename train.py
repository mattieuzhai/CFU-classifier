from pathlib import Path
from ultralytics import YOLO

model = YOLO("yolo26n.pt")  # nano detection model

model.train(
    data=str(Path(__file__).parent / "data.yaml"),
    epochs=100,
    imgsz=3200,
    batch=16,       # 1280px tiles use ~4x more VRAM than 640px; tune up if GPU allows
    device=[0,1],  # use 1 gpu
    project="3200px_training_v4",
    name="cfu_detector",
    exist_ok=True,
)
