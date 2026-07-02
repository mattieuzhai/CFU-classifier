# CFU YOLO Detector — CVAT auto-annotation function

Wraps `best.pt` (Ultralytics YOLO, classes **BFU / GM / E / GEMM**) as a
Nuclio serverless function so CVAT can auto-annotate images.

## Files

| File | Purpose |
|------|---------|
| `best.pt` | Trained YOLO weights (detection). |
| `model_handler.py` | Loads the model, runs inference, returns CVAT-format boxes. |
| `main.py` | Nuclio handler — decodes the image, calls the model, returns JSON. |
| `function.yaml` | Nuclio/CVAT function spec (labels, base image, build steps). |
| `deploy.sh` | Deploys the function to CVAT's Nuclio. |
| `test_local.py` | Run inference locally (no Docker) to sanity-check. |

## 1. Test locally first (optional but recommended)

```bash
# from the repo root, using the project venv
YOLO_IMGSZ=1280 .venv/bin/python nuc/test_local.py path/to/image.jpg 0.5
```

You should see a detection count and a sample box. ✓ Already verified working.

## 2. Deploy to CVAT

**Prereqs**

1. CVAT running **with the serverless profile**:
   ```bash
   docker compose \
     -f docker-compose.yml \
     -f components/serverless/docker-compose.serverless.yml \
     up -d
   ```
2. `nuctl` CLI installed (match the version of CVAT's `nuclio` container):
   ```bash
   # macOS example — adjust version to match your nuclio container
   wget https://github.com/nuclio/nuclio/releases/download/<VERSION>/nuctl-<VERSION>-darwin-amd64 \
     -O /usr/local/bin/nuctl && chmod +x /usr/local/bin/nuctl
   ```

**Deploy**

```bash
./nuc/deploy.sh
```

The first build is slow (it pulls the `ultralytics` base image). When it finishes:

```bash
nuctl get function          # STATE should be "ready"
```

## 3. Use it in CVAT

1. Open a task → **Actions → Automatic annotation**.
2. Model: **YOLO CFU Colony Detector**.
3. The 4 model labels appear — map each to your task's labels
   (BFU→BFU, GM→GM, E→E, GEMM→GEMM).
4. Set a threshold and run. Boxes appear on every frame; review and save.

You can also use it interactively per-frame via the **AI Tools → Detectors** panel.

## Tiling (important for these 6400x6400 plates)

A single whole-image pass downscales 6400px → the imgsz (e.g. 1280), a ~5x
shrink that makes the small colonies vanish. The handler instead **tiles** the
image: it slices it into overlapping tiles, runs the model on each at native
resolution, shifts the boxes back to full-image coords, and merges them with a
global per-class NMS. On a sample plate this took recall from **20/142 boxes
(single pass) to 136/142 (tiled)** at the same threshold, in ~14s.

Controlled by env vars in `function.yaml`:

| Env | Default | Meaning |
|-----|---------|---------|
| `YOLO_TILING` | `1` | `1` = tile, `0` = single whole-image pass. |
| `YOLO_TILE_SIZE` | `1600` | Tile size in px. Smaller = more tiles, slower, finer. |
| `YOLO_TILE_OVERLAP` | `0.2` | Fractional overlap so colonies on seams aren't missed. |
| `YOLO_NMS_IOU` | `0.45` | IoU for class-agnostic NMS across overlaps. |
| `YOLO_NMS_CONTAIN` | `0.6` | Drop a box if more than this fraction of its area is inside a higher-confidence box (removes tile-edge fragments / stacked duplicates). |
| `YOLO_IMGSZ` | tile size | imgsz fed per tile. Set `800` to ~match training scale. |

More/smaller tiles → higher recall but slower (and more redundant boxes to
merge). After changing any of these, redeploy with `./deploy.sh`.

## Tuning

- **`YOLO_IMGSZ`** (env in `function.yaml`, default `1280`): inference image
  size. The model was *trained* at 3200px, but running at 3200 under x86
  emulation on Apple Silicon is too slow and trips the event timeout, so the
  default is 1280 (fast: ~2s/image, still detects colonies well). Raise it back
  toward 3200 only on a native amd64 / GPU host where speed isn't an issue.
- **`eventTimeout`** (`function.yaml`, default `300s`): max time per request.
- For real speed/accuracy, deploy on a GPU host: change `baseImage` to an
  appropriate CUDA image and use CVAT's `deploy_gpu.sh` flow.

### Gotchas hit while deploying on Apple Silicon (for future reference)
This Mac is arm64 but the build/run path needs amd64 emulation, and the network
does TLS interception. The working `function.yaml` already accounts for all of:
1. **amd64 emulation** — set `DOCKER_DEFAULT_PLATFORM=linux/amd64` on the nuclio
   dashboard container (Docker Desktop → enable Rosetta).
2. **Python version** — use `baseImage: python:3.9` (matches Nuclio's wheels);
   the `ultralytics` base image ships a newer Python that breaks Nuclio's build.
3. **TLS interception** — pip installs use `--trusted-host pypi.org
   --trusted-host files.pythonhosted.org` (and we install torch from PyPI, not
   `download.pytorch.org`, which redirects to an untrusted CDN host).
4. **Offline wheels** — pre-stage `msgpack`/`nuclio-sdk` into
   `/home/nuclio/bin/py3.9-whl` so Nuclio's offline install step succeeds.
5. **OpenCV** — `apt-get install libgl1 libglib2.0-0` so `import cv2` works.

## Notes

- The label **`spec`** in `function.yaml` must match the order/IDs the model was
  trained with: `0=BFU, 1=GM, 2=E, 3=GEMM`. Don't reorder it.
- Output boxes are absolute pixel coords `[x_tl, y_tl, x_br, y_br]`, which is
  what CVAT's detector contract expects.
