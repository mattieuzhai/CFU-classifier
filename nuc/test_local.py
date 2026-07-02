"""
Local smoke test for the handler — runs the model the same way the Nuclio
function will, but without Docker/CVAT. Use this to confirm inference works
and to eyeball detections before deploying.

Usage:
    python test_local.py path/to/image.jpg [threshold]
"""

import sys

from PIL import Image

from model_handler import ModelHandler


def main():
    if len(sys.argv) < 2:
        print("Usage: python test_local.py <image_path> [threshold]")
        sys.exit(1)

    image_path = sys.argv[1]
    threshold = float(sys.argv[2]) if len(sys.argv) > 2 else 0.5

    model = ModelHandler()
    image = Image.open(image_path).convert("RGB")
    detections = model.infer(image, threshold)

    print(f"{len(detections)} detection(s) at threshold {threshold}:")
    counts = {}
    for det in detections:
        counts[det["label"]] = counts.get(det["label"], 0) + 1
    for label, n in sorted(counts.items()):
        print(f"  {label}: {n}")

    if detections:
        print("\nFirst detection (CVAT format):")
        print(" ", detections[0])


if __name__ == "__main__":
    main()
