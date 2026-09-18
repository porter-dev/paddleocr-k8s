import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    port: int
    paddleocr_url: str
    ocr_timeout_seconds: float
    ocr_attempts: int
    ocr_ready_wait_seconds: float
    s3_bucket: str
    s3_prefix: str
    s3_endpoint_url: str
    default_chunk_pages: int
    default_concurrency: int
    job_concurrency: int
    ocr_batch_size: int
    ocr_batch_wait_seconds: float
    restructure_pages: bool
    max_upload_bytes: int
    drain_timeout_seconds: float


def load_settings() -> Settings:
    env = os.environ
    bucket = env.get("S3_BUCKET", "")
    if not bucket:
        raise SystemExit("S3_BUCKET is required: every input, intermediate and result is persisted in S3")
    return Settings(
        port=int(env.get("PORT", "8080")),
        paddleocr_url=env.get("PADDLEOCR_URL", "http://localhost:8118"),
        ocr_timeout_seconds=float(env.get("OCR_TIMEOUT_SECONDS", "900")),
        ocr_attempts=int(env.get("OCR_ATTEMPTS", "5")),
        ocr_ready_wait_seconds=float(env.get("OCR_READY_WAIT_SECONDS", "900")),
        s3_bucket=bucket,
        s3_prefix=env.get("S3_PREFIX", "paddleocr-app"),
        s3_endpoint_url=env.get("S3_ENDPOINT_URL", ""),
        default_chunk_pages=int(env.get("DEFAULT_CHUNK_PAGES", "50")),
        default_concurrency=int(env.get("DEFAULT_CONCURRENCY", "2")),
        job_concurrency=int(env.get("JOB_CONCURRENCY", "50")),
        # Match the serving chart's pipeline.maxBatchSize: releasing more chunks at once than
        # Triton will batch only queues them behind each other.
        ocr_batch_size=int(env.get("OCR_BATCH_SIZE", "8")),
        ocr_batch_wait_seconds=float(env.get("OCR_BATCH_WAIT_SECONDS", "5")),
        restructure_pages=env.get("RESTRUCTURE_PAGES", "true").lower() in ("1", "true", "yes"),
        max_upload_bytes=int(env.get("MAX_UPLOAD_BYTES", str(512 * 1024 * 1024))),
        drain_timeout_seconds=float(env.get("DRAIN_TIMEOUT_SECONDS", "600")),
    )
