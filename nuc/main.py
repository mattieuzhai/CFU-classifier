"""
Nuclio handler for CVAT automatic annotation.

CVAT calls `handler()` with a base64-encoded image and a confidence
`threshold`, and expects back a JSON list of detections (one rectangle per
detected colony).
"""

import base64
import io
import json

from PIL import Image

from model_handler import ModelHandler


def init_context(context):
    context.logger.info("Init context...  0%")
    context.user_data.model = ModelHandler()
    context.logger.info("Init context...100%")


def handler(context, event):
    context.logger.info("Run CFU YOLO detector")

    data = event.body
    threshold = float(data.get("threshold", 0.5))

    buf = io.BytesIO(base64.b64decode(data["image"]))
    image = Image.open(buf).convert("RGB")

    detections = context.user_data.model.infer(image, threshold)

    return context.Response(
        body=json.dumps(detections),
        headers={},
        content_type="application/json",
        status_code=200,
    )
