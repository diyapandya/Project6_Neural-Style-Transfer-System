"""
main.py — FastAPI backend for the Neural Style Transfer studio.

Because optimization-based NST takes anywhere from several seconds to a few
minutes depending on device/steps/size, generation runs as a background job:

    1. POST /api/generate         -> validates images, starts a background
                                      thread, returns {job_id}
    2. GET  /api/jobs/{job_id}    -> {status, progress, step, total_steps,
                                      content_loss, style_loss}
    3. GET  /api/jobs/{job_id}/result -> the generated PNG, once status=="done"

The frontend polls (2) while showing a loading animation, then fetches (3)
and swaps in the result. Job state lives in memory, which is fine for a
single-process demo/portfolio deployment; swap JOBS for Redis/DB for
multi-worker production use.
"""

import io
import threading
import time
import uuid
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

import nst_engine

app = FastAPI(title="NST Studio API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# In-memory job store
# ---------------------------------------------------------------------------

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()

MAX_IMAGE_SIDE = 1600  # reject absurdly large uploads before they hit the model


class JobStatus(BaseModel):
    job_id: str
    status: str  # "queued" | "running" | "done" | "error"
    progress: float  # 0..100
    step: int
    total_steps: int
    content_loss: Optional[float] = None
    style_loss: Optional[float] = None
    error: Optional[str] = None
    elapsed_seconds: float = 0.0


def _read_upload_as_image(upload_bytes: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(upload_bytes))
        img.load()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read image: {exc}")
    if max(img.size) > MAX_IMAGE_SIDE:
        raise HTTPException(
            status_code=400,
            detail=f"Image too large (max side {MAX_IMAGE_SIDE}px).",
        )
    return img.convert("RGB")


def _run_job(job_id: str, content_img: Image.Image, style_img: Image.Image,
             img_size: int, steps: int, style_strength: float):
    start = time.time()

    def on_progress(step, total_steps, c_loss, s_loss):
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job is None:
                return
            job.update(
                status="running",
                progress=round(100 * step / total_steps, 1),
                step=step,
                total_steps=total_steps,
                content_loss=c_loss,
                style_loss=s_loss,
                elapsed_seconds=round(time.time() - start, 1),
            )

    try:
        # style_strength (0-100 from the UI) maps onto the beta/style_weight
        # term from the notebook (alpha=1, beta=1e6 by default there).
        style_weight = 1e5 + (style_strength / 100.0) * 1.9e6

        result_img = nst_engine.run_style_transfer(
            content_img,
            style_img,
            img_size=img_size,
            steps=steps,
            content_weight=1.0,
            style_weight=style_weight,
            progress_callback=on_progress,
        )

        buf = io.BytesIO()
        result_img.save(buf, format="PNG")

        with JOBS_LOCK:
            JOBS[job_id].update(
                status="done",
                progress=100.0,
                result_bytes=buf.getvalue(),
                elapsed_seconds=round(time.time() - start, 1),
            )
    except Exception as exc:  # noqa: BLE001 - surface any failure to the client
        with JOBS_LOCK:
            JOBS[job_id].update(status="error", error=str(exc))


@app.get("/api/health")
def health():
    return {"status": "ok", "device": str(nst_engine.device)}


@app.post("/api/generate")
async def generate(
    content_image: UploadFile = File(...),
    style_image: UploadFile = File(...),
    img_size: int = Form(384),
    steps: int = Form(200),
    style_strength: float = Form(65.0),
):
    img_size = max(128, min(img_size, 768))
    steps = max(10, min(steps, 500))
    style_strength = max(0.0, min(style_strength, 100.0))

    content_bytes = await content_image.read()
    style_bytes = await style_image.read()
    content_img = _read_upload_as_image(content_bytes)
    style_img = _read_upload_as_image(style_bytes)

    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "queued",
            "progress": 0.0,
            "step": 0,
            "total_steps": steps,
            "content_loss": None,
            "style_loss": None,
            "error": None,
            "elapsed_seconds": 0.0,
            "result_bytes": None,
        }

    thread = threading.Thread(
        target=_run_job,
        args=(job_id, content_img, style_img, img_size, steps, style_strength),
        daemon=True,
    )
    thread.start()

    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}", response_model=JobStatus)
def job_status(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job_id")
        return JobStatus(
            job_id=job_id,
            status=job["status"],
            progress=job["progress"],
            step=job["step"],
            total_steps=job["total_steps"],
            content_loss=job["content_loss"],
            style_loss=job["style_loss"],
            error=job["error"],
            elapsed_seconds=job["elapsed_seconds"],
        )


@app.get("/api/jobs/{job_id}/result")
def job_result(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job_id")
        if job["status"] != "done":
            raise HTTPException(status_code=409, detail=f"Job status is '{job['status']}', not done")
        result_bytes = job["result_bytes"]
    return Response(content=result_bytes, media_type="image/png")


@app.delete("/api/jobs/{job_id}")
def job_cleanup(job_id: str):
    with JOBS_LOCK:
        JOBS.pop(job_id, None)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Serve the frontend (static build) from the same server for simplicity
# ---------------------------------------------------------------------------

app.mount("/", StaticFiles(directory="../frontend", html=True), name="frontend")
