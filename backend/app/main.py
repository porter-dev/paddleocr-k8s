import json
import logging
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings, load_settings
from .jobs import Job, JobStore, new_job
from .ocr import PaddleOCRClient
from .pdf import count_pages
from .storage import Storage
from .worker import Worker

STATIC_DIR = Path(__file__).parent / "static"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    storage = Storage(settings)
    store = JobStore(storage)
    await store.load()
    ocr = PaddleOCRClient(settings.paddleocr_url, settings.ocr_timeout_seconds)
    worker = Worker(settings, storage, store, ocr)
    await worker.start()
    app.state.settings = settings
    app.state.storage = storage
    app.state.store = store
    app.state.ocr = ocr
    app.state.worker = worker
    yield
    await worker.stop()
    await ocr.aclose()


app = FastAPI(title="PaddleOCR document service", lifespan=lifespan)


@app.get("/api/healthz")
async def healthz(request: Request) -> dict:
    return {"ok": True, "ocrReady": await request.app.state.ocr.ready(), "draining": request.app.state.worker.draining}


# Readiness turns off while draining so the Service stops routing new uploads here while
# in-flight chunks finish; liveness above stays up the whole time.
@app.get("/api/readyz")
async def readyz(request: Request) -> Response:
    if request.app.state.worker.draining:
        return Response('{"ready": false, "reason": "draining"}', status_code=503, media_type="application/json")
    return Response('{"ready": true}', media_type="application/json")


@app.get("/api/config")
async def config(request: Request) -> dict:
    settings: Settings = request.app.state.settings
    return {
        "paddleocrUrl": settings.paddleocr_url,
        "storage": request.app.state.storage.describe(),
        "defaultChunkPages": settings.default_chunk_pages,
        "defaultConcurrency": settings.default_concurrency,
        "defaultRestructure": settings.restructure_pages,
        "maxUploadBytes": settings.max_upload_bytes,
    }


# Prometheus exposition format so Porter's metrics scraping (porter.yaml metricsScraping)
# and a KEDA prometheus trigger can read it directly.
@app.get("/api/metrics")
async def metrics(request: Request) -> Response:
    backlog = request.app.state.store.backlog()
    draining = 1 if request.app.state.worker.draining else 0
    lines = [
        "# HELP paddleocr_pending_pages Pages in queued or running OCR requests across unfinished jobs.",
        "# TYPE paddleocr_pending_pages gauge",
        f"paddleocr_pending_pages {backlog.pending_pages}",
        "# HELP paddleocr_pending_chunks OCR requests queued or running across unfinished jobs.",
        "# TYPE paddleocr_pending_chunks gauge",
        f"paddleocr_pending_chunks {backlog.pending_chunks}",
        "# HELP paddleocr_active_jobs Jobs that are queued, running or assembling.",
        "# TYPE paddleocr_active_jobs gauge",
        f"paddleocr_active_jobs {backlog.active_jobs}",
        "# HELP paddleocr_jobs_total Jobs by final status.",
        "# TYPE paddleocr_jobs_total gauge",
        f'paddleocr_jobs_total{{status="done"}} {backlog.jobs_done}',
        f'paddleocr_jobs_total{{status="failed"}} {backlog.jobs_failed}',
        "# HELP paddleocr_draining Whether this replica is shutting down and refusing new work.",
        "# TYPE paddleocr_draining gauge",
        f"paddleocr_draining {draining}",
    ]
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4; charset=utf-8")


@app.get("/api/jobs")
async def list_jobs(request: Request) -> list[dict]:
    return [job_view(job) for job in request.app.state.store.list()]


@app.post("/api/jobs", status_code=201)
async def create_job(
    request: Request,
    pdf: UploadFile = File(...),
    chunkPages: int = Form(...),
    concurrency: int = Form(...),
    restructure: bool = Form(False),
) -> dict:
    settings: Settings = request.app.state.settings
    if request.app.state.worker.draining:
        raise HTTPException(503, "shutting down, retry shortly")
    if chunkPages < 1 or concurrency < 1:
        raise HTTPException(400, "chunkPages and concurrency must be at least 1")
    data = await pdf.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, f"file exceeds {settings.max_upload_bytes} bytes")
    try:
        pages = count_pages(data)
    except Exception as exc:
        raise HTTPException(400, f"not a readable PDF: {exc}") from exc
    if pages == 0:
        raise HTTPException(400, "PDF has no pages")
    job = new_job(pdf.filename or "document.pdf", pages, len(data), chunkPages, concurrency, restructure)
    await request.app.state.storage.put(job.key("input.pdf"), data, "application/pdf")
    await request.app.state.store.save(job)
    request.app.state.worker.enqueue(job.id)
    return job_view(job)


@app.get("/api/jobs/{job_id}")
async def get_job(request: Request, job_id: str) -> dict:
    return job_view(find_job(request, job_id))


@app.post("/api/jobs/{job_id}/retry")
async def retry_job(request: Request, job_id: str) -> dict:
    job = find_job(request, job_id)
    if job.status != "failed":
        raise HTTPException(409, f"job is {job.status}, only failed jobs can be retried")
    if request.app.state.worker.draining:
        raise HTTPException(503, "shutting down, retry shortly")
    await request.app.state.worker.retry(job)
    return job_view(job)


@app.delete("/api/jobs/{job_id}", status_code=204)
async def delete_job(request: Request, job_id: str) -> Response:
    job = find_job(request, job_id)
    if job.is_active():
        raise HTTPException(409, "job is still running")
    await request.app.state.store.delete(job.id)
    return Response(status_code=204)


@app.get("/api/jobs/{job_id}/input.pdf")
async def input_pdf(request: Request, job_id: str) -> StreamingResponse:
    job = find_job(request, job_id)
    return await stream_file(request, job, "input.pdf", "application/pdf", f'inline; filename="{job.file_name}"')


@app.get("/api/jobs/{job_id}/result.md")
async def result_markdown(request: Request, job_id: str) -> StreamingResponse:
    job = find_done_job(request, job_id)
    return await stream_file(request, job, "result.md", "text/markdown; charset=utf-8", f'inline; filename="{job.output_stem()}.md"')


@app.get("/api/jobs/{job_id}/result.zip")
async def result_zip(request: Request, job_id: str) -> StreamingResponse:
    job = find_done_job(request, job_id)
    return await stream_file(request, job, "result.zip", "application/zip", f'attachment; filename="{job.output_stem()}.zip"')


@app.get("/api/jobs/{job_id}/pages")
async def result_pages(request: Request, job_id: str) -> Response:
    job = find_done_job(request, job_id)
    storage = request.app.state.storage
    if not await storage.exists(job.key("result.pages.json")):
        await storage.put(job.key("result.pages.json"), await build_page_index(storage, job), "application/json")
    return Response(await storage.get(job.key("result.pages.json")), media_type="application/json")


async def build_page_index(storage: Storage, job: Job) -> bytes:
    pages = []
    for chunk in job.chunks:
        manifest = json.loads(await storage.get(job.key("chunks", str(chunk.index), "pages.json")))
        pages.extend({"page": p["pageNumber"], "markdown": p["markdown"], "images": p["imageKeys"]} for p in manifest)
    return json.dumps(pages).encode()


@app.get("/api/jobs/{job_id}/files/{key:path}")
async def job_file(request: Request, job_id: str, key: str) -> StreamingResponse:
    job = find_job(request, job_id)
    if ".." in key.split("/") or key.startswith("/"):
        raise HTTPException(400, "invalid key")
    if not await request.app.state.storage.exists(job.key(key)):
        raise HTTPException(404, "no such file")
    return await stream_file(request, job, key, mimetypes.guess_type(key)[0] or "application/octet-stream", None)


def find_job(request: Request, job_id: str) -> Job:
    job = request.app.state.store.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    return job


def find_done_job(request: Request, job_id: str) -> Job:
    job = find_job(request, job_id)
    if job.status != "done":
        raise HTTPException(409, f"job is {job.status}")
    return job


async def stream_file(request: Request, job: Job, key: str, media_type: str, disposition: str | None) -> StreamingResponse:
    stored = await request.app.state.storage.open(job.key(key))
    headers = {"Content-Length": str(stored.size)}
    if disposition:
        headers["Content-Disposition"] = disposition
    return StreamingResponse(stored.chunks, media_type=media_type, headers=headers)


def job_view(job: Job) -> dict:
    view = job.model_dump(mode="json")
    view["metrics"] = job.metrics().model_dump()
    for chunk_view, chunk in zip(view["chunks"], job.chunks):
        chunk_view["seconds"] = chunk.seconds()
    return view


if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        candidate = STATIC_DIR / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
