# CFU Classifier

A YOLO-based object detector that finds and classifies hematopoietic
colony-forming units (CFUs) — **BFU, GM, E, GEMM** — in whole-plate microscopy
images from human and mouse CFU assays. The plates are photographed at very
high resolution (6400×6400px) with colonies that are tiny relative to the
frame, so the whole pipeline (prep, training, and inference) is built around
**tiling**: slicing each plate into overlapping tiles, running the model per
tile, and merging detections back into full-plate coordinates.

## Pipeline overview

```
data/images/            raw whole-plate photos (44 experiment folders)
        │
        ▼  (test.ipynb: filter → resize → split)
data/split, split_AI/    train/val/test images + YOLO labels
        │                (split_AI = AI pre-annotated, pending human review)
        ▼  (training_prep.ipynb: SAHI tiling)
data/tiled*/             tiled train/val/test sets at various tile sizes
        │
        ▼  (train.py via train.bsub on the LSF cluster)
runs/detect/*/weights/best.pt   trained YOLO weights (one folder per attempt)
        │
        ├──▶ notebooks/inference.ipynb   batch inference + review tooling
        ├──▶ notebooks/app.ipynb         interactive Gradio demo
        └──▶ nuc/                        CVAT auto-annotation service (Nuclio)
```

## Directory layout

| Path | Purpose |
|------|---------|
| `data/images/` | Raw plate photos, one folder per experiment (donor/sample ID, plating date, timepoint encoded in the folder name). |
| `data/split/`, `data/split_AI/` | Train/val/test splits of full-size images + YOLO `.txt` labels. `split_AI` holds images that were pre-annotated by the model itself, awaiting human correction. |
| `data/tiled*/` (`tiled_1280`, `_1600`, `_2400`, `_3200`, `_3200_v2`, `_3200_v3`) | Output of `training_prep.ipynb`'s SAHI tiling step at increasing tile sizes/resolutions — each has its own `tiling_settings.txt` recording the grid/overlap used to produce it. These are the actual inputs to training. |
| `data.yaml` | Ultralytics dataset config: points at a `tiled_*` split and defines the 4 classes (`BFU`, `GM`, `E`, `GEMM`). |
| `train.py` | Fine-tunes `yolo26n.pt` (YOLO nano) on the configured tiled dataset. |
| `train.bsub` | LSF batch submission script that runs `train.py` on the cluster's `gpu_priority` queue (2 GPUs, 8hr wall time). |
| `logs/` | stdout/stderr from each `train.bsub` submission. |
| `runs/detect/` | Training outputs — one folder per attempt (`1600px_training`, `3200px_training`, `_v2`, `_v3`), each containing `cfu_detector/weights/best.pt` plus Ultralytics' training metrics/plots. The `_v2`/`_v3` progression reflects iterating on tile size and dataset quality across training runs. |
| `notebooks/` | Data prep, QC, and inference notebooks — see below. |
| `nuc/` | Deploys `best.pt` as a Nuclio serverless function so CVAT can auto-annotate new plates. Has its own [README](nuc/README.md) with deployment details. |
| `requirements.txt`, `.venv/` | Python environment (ultralytics, sahi, ensemble-boxes, gradio, labelImg, torch, etc). |

## Notebooks

- **`test.ipynb`** — Raw-data prep utilities: filter out unwanted images by
  filename tag (e.g. excluding `(df)`-tagged duplicate/mask images), resize
  everything to a uniform 6400×6400, and split into `train/val/test` folders
  in Ultralytics format. This is the first step that turns `data/images/`
  into a `data/split*` dataset.
- **`training_prep.ipynb`** — Turns a `split*` dataset into `data/tiled_*`.
  Starts with a **pre-flight check & cleanup** of the split: a read-only
  report (per-split counts, cross-split duplicate images with content hashes,
  unannotated images in train/val, and punctuation-insensitive coverage vs
  raw `data/images` — flagging both intentionally-excluded `(df)` masks and
  genuinely missing plates), followed by a `DRY_RUN`-guarded cleanup that
  routes every unannotated image to `test`, keeps each annotated image in its
  home split (train beats val on conflict), and deletes duplicate copies so
  nothing leaks across splits. Then tiles with SAHI: slices each image into a
  `grid × grid` set of overlapping tiles and remaps each YOLO annotation into
  the correct tile's coordinate space (dropping boxes that fall mostly outside
  a tile via `min_visibility`). Includes a visualization cell that draws the
  exact tile grid over a sample image so tiling parameters can be
  sanity-checked before committing to a full run.
- **`annotation_progress.ipynb`** — QC dashboard over a labeled split
  (currently `split_AI/train`): reports how many images are annotated vs.
  not, total boxes and image counts per class, a per-image class heatmap,
  and per-class crop mosaics (random thumbnail grids of every annotated box)
  so mislabeled or ambiguous colonies are easy to spot by eye.
- **`visualize_labels.ipynb`** — Label Visualizer: draws YOLO boxes over
  their source images for a given images/labels folder, plus a "sanity
  report" that flags orphan labels, images missing a label file,
  out-of-range class IDs, and the overall class distribution before doing a
  full visual pass.
- **`inference.ipynb`** — Runs a trained model (`runs/detect/.../best.pt`)
  over a folder of full-resolution plate images using the same
  tile → detect → merge approach as training (merge via NMS or weighted
  boxes fusion). Tiling `GRID`/`OVERLAP_RATIO` are read automatically from the
  `tiling_settings.txt` of the `tiled_*` dataset the model was trained on (set
  `TILED_DIR`), so inference tiling always matches training without hand-editing
  between runs. Produces per-image class counts, box-size statistics,
  annotated preview plots, and (commented-out, ready-to-uncomment) cells to
  save counts to CSV, save annotated images, and write predictions out as
  YOLO `.txt` pre-annotations for review in `labelImg` — this is how
  `data/split_AI` gets populated.
- **`app.ipynb`** — Interactive Gradio demo: upload a plate image, run
  tiled inference, and get an editable detections table (add/remove/correct
  boxes) with a live-updating preview and a button to save corrected
  annotations back out as YOLO labels. Same tiling/fusion logic as
  `inference.ipynb`, packaged as a human-in-the-loop review UI.

## Deployment

`nuc/` wraps the current best weights as a **Nuclio serverless function for
CVAT**, so new plates can be auto-annotated directly inside CVAT's labeling
UI instead of via the notebooks above. It re-implements the same
tile → detect → merge trick (a single whole-image pass at inference
resolution makes colonies vanish on a 6400px plate); on a test plate this
took recall from 20/142 boxes (single pass) to 136/142 (tiled). See
[nuc/README.md](nuc/README.md) for deployment steps, tuning knobs
(`YOLO_TILE_SIZE`, `YOLO_TILE_OVERLAP`, `YOLO_NMS_IOU`, etc.), and the
Apple-Silicon/Docker gotchas that were hit while setting it up.

## Training

```bash
# locally
.venv/bin/python train.py

# on the cluster (LSF)
bsub < train.bsub
```

`train.py` currently trains at `imgsz=3200` on `data/tiled_3200_v3` for 100
epochs across 2 GPUs, writing results to `runs/detect/3200px_training_v3/`.
