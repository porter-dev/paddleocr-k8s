import asyncio
import json
import logging
import mimetypes
import time
from dataclasses import dataclass

import httpx

from . import assemble, pdf
from .config import Settings
from .jobs import Attempt, Chunk, ChunkStatus, Job, JobStatus, JobStore, now
from .leases import OWNER, Lease
from .ocr import OCRError, PaddleOCRClient, PageResult
from .storage import Storage

log = logging.getLogger(__name__)

RETRY_DELAYS_SECONDS = (5, 15, 45, 120, 120)
READY_POLL_SECONDS = 10
LEASE_POLL_SECONDS = 10


@dataclass
class StoredPage:
    page_number: int
    pruned_result: dict
    markdown: str
    image_keys: list[str]


# Triton batches the requests that arrive inside its queue-delay window, but the chunks of one
# document finish splitting and uploading at different moments, so their OCR calls trickle in
# and each one is dispatched alone. This holds finished chunks until a group is ready and then
# releases them together. One gate per job: a group never mixes documents.
class BatchGate:
    def __init__(self, size: int, wait_seconds: float, expected: int):
        self._size = size
        self._wait_seconds = wait_seconds
        self._expected = expected
        self._waiting: list[asyncio.Future] = []
        self._timer: asyncio.Task | None = None

    async def hold(self) -> None:
        if self._size < 2:
            return
        released = asyncio.get_running_loop().create_future()
        self._waiting.append(released)
        self._release_full_group()
        if not released.done() and self._timer is None:
            self._timer = asyncio.create_task(self._release_stale_group())
        try:
            await released
        except asyncio.CancelledError:
            if released in self._waiting:
                self._waiting.remove(released)
            raise

    # A chunk that will never reach the gate: already done, adopted from another pod, dropped
    # by a drain, or failed while splitting. The group shrinks so the rest are not stranded.
    def withdraw(self) -> None:
        self._expected -= 1
        self._release_full_group()

    def _release_full_group(self) -> None:
        if self._waiting and len(self._waiting) >= min(self._size, self._expected):
            self._release()

    # The safety valve for chunks still queued behind a lease or a slow split, which would
    # otherwise hold a partial group indefinitely.
    async def _release_stale_group(self) -> None:
        await asyncio.sleep(self._wait_seconds)
        self._timer = None
        if self._waiting:
            self._release()

    def _release(self) -> None:
        group, self._waiting = self._waiting, []
        self._expected -= len(group)
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        for released in group:
            if not released.done():
                released.set_result(None)


class Worker:
    def __init__(self, settings: Settings, storage: Storage, store: JobStore, ocr: PaddleOCRClient):
        self._settings = settings
        self._storage = storage
        self._store = store
        self._ocr = ocr
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._consumers: list[asyncio.Task] = []
        self._draining = False

    async def start(self) -> None:
        for job in reversed(self._store.list()):
            if job.is_active():
                await self._requeue(job)
        self._consumers = [
            asyncio.create_task(self._consume(), name=f"ocr-job-consumer-{i}")
            for i in range(self._settings.job_concurrency)
        ]

    # Shutdown lets chunks already sent to PaddleOCR finish, because their results only
    # exist once the request returns, but starts nothing new; a job caught mid-way is saved
    # as queued with its finished chunks intact and resumes in the next process.
    async def stop(self) -> None:
        self._draining = True
        for task in self._consumers:
            self._queue.put_nowait("")
        done, pending = await asyncio.wait(self._consumers, timeout=self._settings.drain_timeout_seconds)
        if pending:
            log.warning("drain timed out after %.0fs, abandoning %d in-flight chunk(s)", self._settings.drain_timeout_seconds, len(pending))
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    @property
    def draining(self) -> bool:
        return self._draining

    def enqueue(self, job_id: str) -> None:
        self._queue.put_nowait(job_id)

    async def retry(self, job: Job) -> None:
        for chunk in job.chunks:
            if chunk.status != ChunkStatus.done:
                chunk.status = ChunkStatus.queued
                chunk.error = None
                chunk.attempts = 0
        job.status = JobStatus.queued
        job.error = None
        job.finished_at = None
        await self._store.save(job)
        self.enqueue(job.id)

    async def _requeue(self, job: Job) -> None:
        log.info("job %s was %s at startup, requeuing", job.id, job.status)
        for chunk in job.chunks:
            if chunk.status != ChunkStatus.done:
                chunk.status = ChunkStatus.queued
                chunk.error = None
        job.status = JobStatus.queued
        await self._store.save(job)
        self.enqueue(job.id)

    async def _consume(self) -> None:
        while not self._draining:
            job_id = await self._queue.get()
            job = self._store.get(job_id)
            if job is None or self._draining:
                continue
            try:
                await self._run(job)
            except Exception as exc:
                log.exception("job %s failed", job.id)
                job.status = JobStatus.failed
                job.error = str(exc)[:1000]
                job.finished_at = now()
                await self._store.save(job)

    async def _run(self, job: Job) -> None:
        job.status = JobStatus.running
        job.started_at = job.started_at or now()
        job.finished_at = None
        job.error = None
        await self._store.save(job)
        source = await self._storage.get(job.key("input.pdf"))
        limiter = asyncio.Semaphore(job.concurrency)
        # A group can never exceed what this job is allowed to have in flight, or the gate
        # would wait for chunks the semaphore is still holding back.
        gate = BatchGate(
            min(self._settings.ocr_batch_size, job.concurrency),
            self._settings.ocr_batch_wait_seconds,
            len(job.chunks),
        )
        outcomes = await asyncio.gather(
            *(self._run_chunk(job, chunk, source, limiter, gate) for chunk in job.chunks),
            return_exceptions=True,
        )
        failures = [o for o in outcomes if isinstance(o, BaseException)]
        if failures:
            raise RuntimeError(f"{len(failures)} of {len(job.chunks)} chunks failed: {failures[0]}")
        if any(o is None for o in outcomes):
            job.status = JobStatus.queued
            await self._store.save(job)
            log.info("job %s parked with %d/%d chunks done for the next process", job.id, job.metrics().chunks_done, len(job.chunks))
            return
        pages = [page for chunk_pages in outcomes for page in chunk_pages]

        job.status = JobStatus.assembling
        await self._store.save(job)
        lease = Lease(self._storage, job.key("assembly.lease"))
        while not await lease.try_acquire():
            if await self._storage.exists(job.key("result.zip")):
                log.info("job %s was assembled by %s", job.id, await lease.holder())
                await self._finish(job, "other pod")
                return
            await asyncio.sleep(LEASE_POLL_SECONDS)
        async with lease.held():
            clock = time.monotonic()
            markdown, method = await self._assemble(job, pages)
            job.timings["assemble"] = round(time.monotonic() - clock, 2)
            clock = time.monotonic()
            await self._storage.put(job.key("result.md"), markdown.encode(), "text/markdown")
            page_index = [{"page": p.page_number, "markdown": p.markdown, "images": p.image_keys} for p in pages]
            await self._storage.put(job.key("result.pages.json"), json.dumps(page_index).encode(), "application/json")
            images = {}
            for page in pages:
                for key in page.image_keys:
                    images[key] = await self._storage.get(job.key(key))
            archive = await asyncio.to_thread(assemble.build_zip, markdown, images)
            await self._storage.put(job.key("result.zip"), archive, "application/zip")
            job.timings["archive"] = round(time.monotonic() - clock, 2)
            await self._finish(job, method)
        await lease.release()

    async def _finish(self, job: Job, method: str) -> None:
        job.assembled_with = method
        job.status = JobStatus.done
        job.finished_at = now()
        await self._store.save(job)
        log.info("job %s done: %d pages in %.1fs (%s)", job.id, job.pages, job.metrics().elapsed_seconds, method)

    async def _run_chunk(self, job: Job, chunk: Chunk, source: bytes, limiter: asyncio.Semaphore, gate: BatchGate) -> list[StoredPage] | None:
        held = False
        try:
            pages_key = job.key("chunks", str(chunk.index), "pages.json")
            if chunk.status == ChunkStatus.done or await self._storage.exists(pages_key):
                return await self._adopt_finished_chunk(job, chunk)
            async with limiter:
                lease = Lease(self._storage, job.key("chunks", str(chunk.index), "lease.json"))
                clock = time.monotonic()
                while True:
                    if self._draining:
                        return None
                    if await self._storage.exists(pages_key):
                        return await self._adopt_finished_chunk(job, chunk)
                    if await lease.try_acquire():
                        chunk.close_open_attempts("abandoned")
                        break
                    holder = await lease.holder()
                    if chunk.owner != holder:
                        chunk.owner = holder
                        chunk.status = ChunkStatus.running
                        await self._store.save(job)
                        log.info("job %s chunk %d is held by %s, waiting", job.id, chunk.index, holder)
                    await asyncio.sleep(LEASE_POLL_SECONDS)
                chunk.status = ChunkStatus.running
                chunk.owner = OWNER
                chunk.started_at = now()
                chunk.finished_at = None
                chunk.error = None
                chunk.timings = {}
                chunk.attempts += 1
                chunk.history.append(Attempt(owner=OWNER, started_at=chunk.started_at))
                waited = time.monotonic() - clock
                if waited > 1:
                    chunk.timings["lease_wait"] = round(waited, 2)
                await self._store.save(job)
                try:
                    async with lease.held():
                        clock = time.monotonic()
                        chunk_pdf = await asyncio.to_thread(pdf.extract_pages, source, chunk.first_page, chunk.last_page)
                        chunk.timings["split"] = round(time.monotonic() - clock, 2)
                        clock = time.monotonic()
                        await self._storage.put(job.key("chunks", str(chunk.index), "input.pdf"), chunk_pdf, "application/pdf")
                        chunk.timings["upload"] = round(time.monotonic() - clock, 2)
                        clock = time.monotonic()
                        await gate.hold()
                        held = True
                        chunk.timings["batch_wait"] = round(time.monotonic() - clock, 2)
                        clock = time.monotonic()
                        results = await self._ocr_with_retries(job, chunk, chunk_pdf)
                        chunk.timings["ocr"] = round(time.monotonic() - clock, 2)
                        if len(results) != chunk.pages:
                            raise OCRError(f"expected {chunk.pages} pages, got {len(results)}")
                        clock = time.monotonic()
                        stored = await self._store_pages(job, chunk, results)
                        chunk.timings["store"] = round(time.monotonic() - clock, 2)
                    chunk.status = ChunkStatus.done
                    chunk.finished_at = now()
                    chunk.close_open_attempts("done")
                    await self._storage.put(job.key("chunks", str(chunk.index), "chunk.json"), chunk.model_dump_json().encode(), "application/json")
                    await self._store.save(job)
                    await lease.release()
                    return stored
                except Exception as exc:
                    chunk.status = ChunkStatus.failed
                    chunk.finished_at = now()
                    chunk.error = str(exc)[:500]
                    chunk.close_open_attempts("failed")
                    await self._store.save(job)
                    await lease.release()
                    raise
        finally:
            if not held:
                gate.withdraw()

    # A chunk finished by another pod is adopted from the record that pod wrote next to its
    # pages, so the manifest shows who ran it and how long it took rather than a stale copy.
    async def _adopt_finished_chunk(self, job: Job, chunk: Chunk) -> list[StoredPage]:
        stored = await self._load_stored_pages(job, chunk)
        if chunk.status != ChunkStatus.done:
            record_key = job.key("chunks", str(chunk.index), "chunk.json")
            if await self._storage.exists(record_key):
                finished = Chunk.model_validate_json(await self._storage.get(record_key))
                for field in ("status", "attempts", "started_at", "finished_at", "error", "owner", "timings", "history"):
                    setattr(chunk, field, getattr(finished, field))
            else:
                chunk.status = ChunkStatus.done
                chunk.finished_at = chunk.finished_at or now()
                chunk.error = None
            await self._store.save(job)
            log.info("job %s chunk %d was finished by %s", job.id, chunk.index, chunk.owner or "another pod")
        return stored

    # A failed request is retried with growing delays, and never before the OCR service
    # reports ready again: a pod replacement takes minutes and must not burn the attempts.
    async def _ocr_with_retries(self, job: Job, chunk: Chunk, chunk_pdf: bytes) -> list[PageResult]:
        while True:
            try:
                return await self._ocr.layout_parsing(chunk_pdf)
            except (OCRError, httpx.HTTPError) as exc:
                if chunk.attempts >= self._settings.ocr_attempts:
                    raise
                log.warning("job %s chunk %d attempt %d failed: %s", job.id, chunk.index, chunk.attempts, exc)
                chunk.attempts += 1
                await self._store.save(job)
                await asyncio.sleep(RETRY_DELAYS_SECONDS[min(chunk.attempts - 2, len(RETRY_DELAYS_SECONDS) - 1)])
                await self._wait_for_ocr_ready(job, chunk)

    async def _wait_for_ocr_ready(self, job: Job, chunk: Chunk) -> None:
        deadline = time.monotonic() + self._settings.ocr_ready_wait_seconds
        waited = False
        while not await self._ocr.ready():
            if time.monotonic() > deadline:
                log.warning("job %s chunk %d: OCR service still not ready after %.0fs, retrying anyway", job.id, chunk.index, self._settings.ocr_ready_wait_seconds)
                return
            if not waited:
                log.info("job %s chunk %d: OCR service not ready, waiting", job.id, chunk.index)
                waited = True
            await asyncio.sleep(READY_POLL_SECONDS)

    async def _store_pages(self, job: Job, chunk: Chunk, results: list[PageResult]) -> list[StoredPage]:
        stored = []
        for offset, page in enumerate(results):
            page_number = chunk.first_page + offset
            page = assemble.namespace_images(page_number, page)
            for key, data in page.images.items():
                await self._storage.put(job.key(key), data, mimetypes.guess_type(key)[0] or "image/jpeg")
            stored.append(StoredPage(page_number, page.pruned_result, page.markdown, list(page.images)))
        manifest = [
            {"pageNumber": p.page_number, "prunedResult": p.pruned_result, "markdown": p.markdown, "imageKeys": p.image_keys}
            for p in stored
        ]
        await self._storage.put(job.key("chunks", str(chunk.index), "pages.json"), json.dumps(manifest).encode(), "application/json")
        return stored

    async def _load_stored_pages(self, job: Job, chunk: Chunk) -> list[StoredPage]:
        manifest = json.loads(await self._storage.get(job.key("chunks", str(chunk.index), "pages.json")))
        return [StoredPage(p["pageNumber"], p["prunedResult"], p["markdown"], p["imageKeys"]) for p in manifest]

    async def _assemble(self, job: Job, pages: list[StoredPage]) -> tuple[str, str]:
        if job.restructure:
            try:
                markdown = await self._ocr.restructure_pages([p.pruned_result for p in pages])
                markdown = assemble.relink_images(markdown, [(p.page_number, p.image_keys) for p in pages])
                return markdown, "restructure-pages"
            except (OCRError, httpx.HTTPError) as exc:
                log.warning("job %s: restructure-pages failed (%s), concatenating instead", job.id, exc)
        return assemble.concatenate([p.markdown for p in pages]), "concatenation"
