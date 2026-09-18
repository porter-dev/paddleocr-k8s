import asyncio
import json
import logging
import secrets
from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel

from .storage import Storage

log = logging.getLogger(__name__)


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    assembling = "assembling"
    done = "done"
    failed = "failed"


class ChunkStatus(StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


class Attempt(BaseModel):
    owner: str
    started_at: datetime
    finished_at: datetime | None = None
    outcome: str | None = None


class Chunk(BaseModel):
    index: int
    first_page: int
    last_page: int
    status: ChunkStatus = ChunkStatus.queued
    attempts: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    owner: str | None = None
    timings: dict[str, float] = {}
    history: list[Attempt] = []

    @property
    def pages(self) -> int:
        return self.last_page - self.first_page + 1

    def close_open_attempts(self, outcome: str) -> None:
        for attempt in self.history:
            if attempt.finished_at is None:
                attempt.finished_at = now()
                attempt.outcome = outcome

    def seconds(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.finished_at if self.finished_at else now()
        return round((end - self.started_at).total_seconds(), 1)


class JobMetrics(BaseModel):
    pages_done: int
    chunks_done: int
    chunks_failed: int
    queued_seconds: float
    elapsed_seconds: float
    pages_per_second: float


class Backlog(BaseModel):
    pending_pages: int
    pending_chunks: int
    active_jobs: int
    jobs_done: int
    jobs_failed: int


class Job(BaseModel):
    id: str
    file_name: str
    pages: int
    bytes: int = 0
    chunk_pages: int
    concurrency: int
    restructure: bool
    status: JobStatus = JobStatus.queued
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    assembled_with: str | None = None
    timings: dict[str, float] = {}
    chunks: list[Chunk]

    def key(self, *parts: str) -> str:
        return "/".join(("jobs", self.id, *parts))

    def output_stem(self) -> str:
        return self.file_name.rsplit(".", 1)[0] or "document"

    def is_active(self) -> bool:
        return self.status in (JobStatus.queued, JobStatus.running, JobStatus.assembling)

    def metrics(self) -> JobMetrics:
        done = [c for c in self.chunks if c.status == ChunkStatus.done]
        pages_done = sum(c.pages for c in done)
        if self.started_at is None:
            queued = (now() - self.created_at).total_seconds()
            elapsed = 0.0
        else:
            queued = (self.started_at - self.created_at).total_seconds()
            end = self.finished_at or now()
            elapsed = (end - self.started_at).total_seconds()
        return JobMetrics(
            pages_done=pages_done,
            chunks_done=len(done),
            chunks_failed=sum(1 for c in self.chunks if c.status == ChunkStatus.failed),
            queued_seconds=round(max(queued, 0.0), 1),
            elapsed_seconds=round(elapsed, 1),
            pages_per_second=round(pages_done / elapsed, 3) if elapsed > 0 else 0.0,
        )


def new_job(file_name: str, pages: int, size: int, chunk_pages: int, concurrency: int, restructure: bool) -> Job:
    chunks = [
        Chunk(index=i, first_page=first, last_page=min(first + chunk_pages - 1, pages))
        for i, first in enumerate(range(1, pages + 1, chunk_pages))
    ]
    return Job(
        id=secrets.token_hex(6),
        file_name=file_name,
        pages=pages,
        bytes=size,
        chunk_pages=chunk_pages,
        concurrency=concurrency,
        restructure=restructure,
        created_at=now(),
        chunks=chunks,
    )


def now() -> datetime:
    return datetime.now(timezone.utc)


class JobStore:
    def __init__(self, storage: Storage):
        self._storage = storage
        self._jobs: dict[str, Job] = {}
        self._save_lock = asyncio.Lock()

    async def load(self) -> None:
        for job_id in await self._storage.list_dirs("jobs"):
            key = f"jobs/{job_id}/job.json"
            if not await self._storage.exists(key):
                continue
            try:
                self._jobs[job_id] = Job.model_validate(json.loads(await self._storage.get(key)))
            except Exception as exc:
                log.warning("skipping job %s: unreadable manifest (%s)", job_id, exc)
        log.info("loaded %d jobs", len(self._jobs))

    def list(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    # Work accepted but not finished, the signal an autoscaler should size the OCR fleet on.
    # Running chunks count because they still occupy a pod until they complete.
    def backlog(self) -> Backlog:
        active = [job for job in self._jobs.values() if job.is_active()]
        pending = [c for job in active for c in job.chunks if c.status in (ChunkStatus.queued, ChunkStatus.running)]
        return Backlog(
            pending_pages=sum(c.pages for c in pending),
            pending_chunks=len(pending),
            active_jobs=len(active),
            jobs_done=sum(1 for job in self._jobs.values() if job.status == JobStatus.done),
            jobs_failed=sum(1 for job in self._jobs.values() if job.status == JobStatus.failed),
        )

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    async def save(self, job: Job) -> None:
        self._jobs[job.id] = job
        async with self._save_lock:
            await self._storage.put(job.key("job.json"), job.model_dump_json(indent=2).encode(), "application/json")

    async def delete(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)
        await self._storage.delete_prefix(f"jobs/{job_id}")
