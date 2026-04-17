from ultralytics import YOLO

model = YOLO("yolov8n.pt")  # nano model; swap for yolov8s.pt / yolov8m.pt if needed

model.train(
    data="data.yaml",
    epochs=100,
    imgsz=640,
    batch=16,
    project="runs",
    name="cfu_detector",
    exist_ok=True,
)
