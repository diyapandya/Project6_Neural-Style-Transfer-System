# NST Studio

A premium web frontend + FastAPI backend for your Neural Style Transfer notebook
(VGG19 feature extraction, Gram-matrix style loss, L-BFGS pixel optimization).

```
nst-studio/
├── backend/
│   ├── main.py          # FastAPI app: job queue, endpoints, serves the frontend
│   ├── nst_engine.py     # Your notebook's NST logic, refactored into a reusable module
│   └── requirements.txt
└── frontend/
    ├── index.html
    ├── styles.css
    └── app.js
```

## What changed vs. the notebook

The optimization math is untouched — same VGG19 layers (`conv1_1…conv5_1`, content at
`conv4_2`), the same Gram-matrix style loss, and the same L-BFGS loop. What's new:

- It runs on in-memory uploaded images instead of a mounted dataset folder.
- Progress is reported via a callback after every L-BFGS closure call, so the frontend
  can show a live progress bar instead of printed loss values.
- Generation runs as a background job (`POST /api/generate` → poll `GET /api/jobs/{id}`
  → `GET /api/jobs/{id}/result`) since one run can take anywhere from a few seconds to a
  few minutes depending on device, resolution, and step count.

## Setup

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

> **GPU note:** the `torch`/`torchvision` versions pinned in `requirements.txt` are CPU
> wheels by default. For CUDA, install the matching build from
> https://pytorch.org/get-started/locally/ **before** running `pip install -r requirements.txt`,
> or edit the two lines afterward.

## Run

```bash
cd backend
uvicorn main:app --reload --port 8000
```

Open **http://localhost:8000** — the backend serves the frontend directly, so there's
nothing extra to start. The status pill in the top-right corner confirms the API is live.

The first generation call will be slow while VGG19's pretrained weights download and
load into memory; subsequent calls reuse the loaded model.

## Using the studio

1. Drop a content photo and a style reference into the two upload panels.
2. Optionally tune **style strength** (maps to the notebook's style/content loss
   weighting) and, under **Advanced settings**, resolution and step count. Lower
   resolution/steps = faster, rougher results; higher = slower, more refined.
3. Press **Generate**. A live progress bar tracks L-BFGS steps as they complete.
4. Compare the result against the original with the **Before / After** slider, view it
   **fullscreen**, **download** the PNG, or **Generate again** with new settings.

## API reference

| Method | Path                       | Purpose                                   |
|--------|----------------------------|--------------------------------------------|
| GET    | `/api/health`               | Backend/device check                       |
| POST   | `/api/generate`             | multipart form: `content_image`, `style_image`, `img_size`, `steps`, `style_strength` → `{job_id}` |
| GET    | `/api/jobs/{job_id}`        | Poll status/progress                       |
| GET    | `/api/jobs/{job_id}/result` | Fetch the generated PNG once `status=="done"` |
| DELETE | `/api/jobs/{job_id}`        | Drop a finished job from memory            |

Job state is kept in an in-process dict — fine for local/demo use. For multi-worker
production deployment, swap it for Redis or a small database and run the NST job in a
process pool (`ProcessPoolExecutor`) instead of a thread, since PyTorch CPU inference
doesn't parallelize well across threads in one process.
