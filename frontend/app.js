// ============================================================
// Config
// ============================================================
// When served by the FastAPI backend itself (StaticFiles mount), same-origin
// requests just work. If you open this file directly (file://) instead of
// through the server, point API_BASE at your running backend.
const API_BASE = (location.protocol === "http:" || location.protocol === "https:")
  ? ""
  : "http://localhost:8000";

const POLL_INTERVAL_MS = 700;

// ============================================================
// Element refs
// ============================================================
const el = (id) => document.getElementById(id);

const apiStatus = el("apiStatus");

const contentInput = el("contentInput");
const styleInput = el("styleInput");
const contentDrop = el("contentDrop");
const styleDrop = el("styleDrop");
const contentPreview = el("contentPreview");
const stylePreview = el("stylePreview");
const contentClear = el("contentClear");
const styleClear = el("styleClear");
const mergeGlyph = el("mergeGlyph");

const strengthSlider = el("strengthSlider");
const strengthValue = el("strengthValue");
const sizeSelect = el("sizeSelect");
const stepsSelect = el("stepsSelect");

const generateBtn = el("generateBtn");
const formNote = el("formNote");

const resultSection = el("resultSection");
const viewToggle = el("viewToggle");
const loadingState = el("loadingState");
const loadingStage = el("loadingStage");
const progressFill = el("progressFill");
const loadingMeta = el("loadingMeta");

const canvasSingle = el("canvasSingle");
const resultImage = el("resultImage");
const fullscreenBtn = el("fullscreenBtn");

const canvasCompare = el("canvasCompare");
const compareBefore = el("compareBefore");
const compareAfter = el("compareAfter");
const compareClip = el("compareClip");
const compareHandle = el("compareHandle");

const resultActions = el("resultActions");
const regenerateBtn = el("regenerateBtn");
const downloadBtn = el("downloadBtn");
const errorMsg = el("errorMsg");

const lightbox = el("lightbox");
const lightboxImage = el("lightboxImage");
const lightboxClose = el("lightboxClose");

// ============================================================
// State
// ============================================================
let contentFile = null;
let styleFile = null;
let contentDataUrl = null;
let currentJobId = null;
let pollTimer = null;
let currentView = "result";

const STAGE_MESSAGES = [
  [0, "Warming up VGG19…"],
  [10, "Extracting content features…"],
  [20, "Extracting style features…"],
  [30, "Sculpting pixels with L-BFGS…"],
  [70, "Refining texture and structure…"],
  [92, "Finishing touches…"],
];

function stageForProgress(pct) {
  let msg = STAGE_MESSAGES[0][1];
  for (const [threshold, text] of STAGE_MESSAGES) {
    if (pct >= threshold) msg = text;
  }
  return msg;
}

// ============================================================
// API health check
// ============================================================
async function checkHealth() {
  try {
    const res = await fetch(`${API_BASE}/api/health`);
    if (!res.ok) throw new Error("bad status");
    apiStatus.classList.remove("is-down");
    apiStatus.classList.add("is-live");
    apiStatus.querySelector(".status__text").textContent = "backend online";
  } catch {
    apiStatus.classList.remove("is-live");
    apiStatus.classList.add("is-down");
    apiStatus.querySelector(".status__text").textContent = "backend unreachable";
  }
}
checkHealth();

// ============================================================
// Upload handling
// ============================================================
function readAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function setPreview(kind, file) {
  const dataUrl = await readAsDataUrl(file);
  if (kind === "content") {
    contentFile = file;
    contentDataUrl = dataUrl;
    contentPreview.src = dataUrl;
    contentPreview.hidden = false;
    contentDrop.querySelector(".dropzone__empty").style.display = "none";
    contentClear.hidden = false;
  } else {
    styleFile = file;
    stylePreview.src = dataUrl;
    stylePreview.hidden = false;
    styleDrop.querySelector(".dropzone__empty").style.display = "none";
    styleClear.hidden = false;
  }
  updateGenerateAvailability();
}

function clearPreview(kind) {
  if (kind === "content") {
    contentFile = null;
    contentDataUrl = null;
    contentPreview.src = "";
    contentPreview.hidden = true;
    contentDrop.querySelector(".dropzone__empty").style.display = "";
    contentClear.hidden = true;
    contentInput.value = "";
  } else {
    styleFile = null;
    stylePreview.src = "";
    stylePreview.hidden = true;
    styleDrop.querySelector(".dropzone__empty").style.display = "";
    styleClear.hidden = true;
    styleInput.value = "";
  }
  updateGenerateAvailability();
}

function updateGenerateAvailability() {
  const ready = Boolean(contentFile && styleFile);
  generateBtn.disabled = !ready;
  formNote.textContent = ready
    ? "Ready — press Generate to start the optimization."
    : "Add a content and a style image to begin.";
}

contentInput.addEventListener("change", (e) => {
  if (e.target.files[0]) setPreview("content", e.target.files[0]);
});
styleInput.addEventListener("change", (e) => {
  if (e.target.files[0]) setPreview("style", e.target.files[0]);
});
contentClear.addEventListener("click", (e) => { e.preventDefault(); clearPreview("content"); });
styleClear.addEventListener("click", (e) => { e.preventDefault(); clearPreview("style"); });

// Drag & drop
function wireDropzone(dropEl, kind, inputEl) {
  ["dragenter", "dragover"].forEach((evt) =>
    dropEl.addEventListener(evt, (e) => {
      e.preventDefault();
      dropEl.classList.add("is-dragover");
    })
  );
  ["dragleave", "drop"].forEach((evt) =>
    dropEl.addEventListener(evt, (e) => {
      e.preventDefault();
      dropEl.classList.remove("is-dragover");
    })
  );
  dropEl.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files[0];
    if (file && file.type.startsWith("image/")) setPreview(kind, file);
  });
  dropEl.addEventListener("click", (e) => {
    if (e.target.closest(".dropzone__clear")) return;
    inputEl.click();
  });
  dropEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); inputEl.click(); }
  });
}
wireDropzone(contentDrop, "content", contentInput);
wireDropzone(styleDrop, "style", styleInput);

// Prevent the label's implicit click from double-firing the file picker.
contentDrop.addEventListener("click", (e) => e.preventDefault());
styleDrop.addEventListener("click", (e) => e.preventDefault());

// ============================================================
// Controls
// ============================================================
strengthSlider.addEventListener("input", () => {
  strengthValue.textContent = strengthSlider.value;
});

// ============================================================
// Generation flow
// ============================================================
generateBtn.addEventListener("click", startGeneration);
regenerateBtn.addEventListener("click", startGeneration);

async function startGeneration() {
  if (!contentFile || !styleFile) return;

  resultSection.hidden = false;
  resultSection.style.animation = "none";
  void resultSection.offsetWidth;
  resultSection.style.animation = "";
  errorMsg.hidden = true;
  resultActions.hidden = true;
  canvasSingle.hidden = true;
  canvasCompare.hidden = true;
  loadingState.hidden = false;
  progressFill.style.width = "0%";
  loadingMeta.textContent = "step 0 / 0";
  loadingStage.textContent = stageForProgress(0);

  generateBtn.disabled = true;
  generateBtn.classList.add("is-loading");
  mergeGlyph.classList.add("is-active");

  resultSection.scrollIntoView({ behavior: "smooth", block: "start" });

  const formData = new FormData();
  formData.append("content_image", contentFile);
  formData.append("style_image", styleFile);
  formData.append("img_size", sizeSelect.value);
  formData.append("steps", stepsSelect.value);
  formData.append("style_strength", strengthSlider.value);

  try {
    const res = await fetch(`${API_BASE}/api/generate`, { method: "POST", body: formData });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `Server responded ${res.status}`);
    }
    const { job_id } = await res.json();
    currentJobId = job_id;
    pollJob(job_id);
  } catch (err) {
    showError(err.message || "Could not reach the backend.");
    resetGenerateButton();
  }
}

function pollJob(jobId) {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/jobs/${jobId}`);
      if (!res.ok) throw new Error("Lost track of the job.");
      const job = await res.json();

      progressFill.style.width = `${job.progress}%`;
      loadingMeta.textContent = `step ${job.step} / ${job.total_steps} · ${job.elapsed_seconds}s`;
      loadingStage.textContent = stageForProgress(job.progress);

      if (job.status === "done") {
        clearInterval(pollTimer);
        await fetchResult(jobId);
      } else if (job.status === "error") {
        clearInterval(pollTimer);
        showError(job.error || "Generation failed.");
        resetGenerateButton();
      }
    } catch (err) {
      clearInterval(pollTimer);
      showError(err.message);
      resetGenerateButton();
    }
  }, POLL_INTERVAL_MS);
}

async function fetchResult(jobId) {
  const res = await fetch(`${API_BASE}/api/jobs/${jobId}/result`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);

  resultImage.src = url;
  compareAfter.src = url;
  compareBefore.src = contentDataUrl;
  downloadBtn.href = url;

  loadingState.hidden = true;
  resultActions.hidden = false;
  showView(currentView);

  resetGenerateButton();
}

function resetGenerateButton() {
  generateBtn.disabled = !(contentFile && styleFile);
  generateBtn.classList.remove("is-loading");
  mergeGlyph.classList.remove("is-active");
}

function showError(message) {
  errorMsg.textContent = message;
  errorMsg.hidden = false;
  loadingState.hidden = true;
}

// ============================================================
// View toggle: single result vs before/after compare
// ============================================================
viewToggle.addEventListener("click", (e) => {
  const btn = e.target.closest(".view-toggle__btn");
  if (!btn) return;
  viewToggle.querySelectorAll(".view-toggle__btn").forEach((b) => b.classList.remove("is-active"));
  btn.classList.add("is-active");
  showView(btn.dataset.view);
});

function showView(view) {
  currentView = view;
  canvasSingle.hidden = view !== "result";
  canvasCompare.hidden = view !== "compare";
}

// ============================================================
// Before/after slider drag
// ============================================================
let dragging = false;

function setClip(clientX) {
  const rect = canvasCompare.getBoundingClientRect();
  let pct = ((clientX - rect.left) / rect.width) * 100;
  pct = Math.min(100, Math.max(0, pct));
  compareClip.style.width = `${pct}%`;
  compareHandle.style.left = `${pct}%`;
}

compareHandle.addEventListener("pointerdown", (e) => {
  dragging = true;
  compareHandle.setPointerCapture(e.pointerId);
});
compareHandle.addEventListener("pointerup", () => { dragging = false; });
compareHandle.addEventListener("pointermove", (e) => {
  if (dragging) setClip(e.clientX);
});
canvasCompare.addEventListener("click", (e) => {
  if (e.target === compareHandle || e.target.closest(".compare__handle")) return;
  setClip(e.clientX);
});

// ============================================================
// Fullscreen lightbox
// ============================================================
fullscreenBtn.addEventListener("click", () => {
  lightboxImage.src = resultImage.src;
  lightbox.hidden = false;
});
lightboxClose.addEventListener("click", () => { lightbox.hidden = true; });
lightbox.addEventListener("click", (e) => {
  if (e.target === lightbox) lightbox.hidden = true;
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !lightbox.hidden) lightbox.hidden = true;
});
