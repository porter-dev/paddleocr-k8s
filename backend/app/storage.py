import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import boto3
from botocore.exceptions import ClientError

from .config import Settings

STREAM_CHUNK_BYTES = 1 << 20


@dataclass
class StoredObject:
    size: int
    content_type: str
    chunks: AsyncIterator[bytes]


class Storage:
    def __init__(self, settings: Settings):
        self._bucket = settings.s3_bucket
        self._prefix = settings.s3_prefix.strip("/")
        self._client = boto3.client("s3", endpoint_url=settings.s3_endpoint_url or None)

    def describe(self) -> str:
        return f"s3://{self._bucket}/{self._prefix}"

    def _key(self, key: str) -> str:
        return f"{self._prefix}/{key}" if self._prefix else key

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self._client.put_object, Bucket=self._bucket, Key=self._key(key), Body=data, ContentType=content_type
        )

    async def get(self, key: str) -> bytes:
        def read() -> bytes:
            return self._client.get_object(Bucket=self._bucket, Key=self._key(key))["Body"].read()

        return await asyncio.to_thread(read)

    async def get_versioned(self, key: str) -> tuple[bytes, str] | None:
        def read() -> tuple[bytes, str] | None:
            try:
                response = self._client.get_object(Bucket=self._bucket, Key=self._key(key))
            except self._client.exceptions.NoSuchKey:
                return None
            return response["Body"].read(), response["ETag"]

        return await asyncio.to_thread(read)

    # Conditional write: only succeeds if the object is absent (if_none_match="*") or still
    # has the given ETag (if_match), which is what makes lease claims safe between pods.
    async def put_if(self, key: str, data: bytes, content_type: str, if_match: str | None = None, if_none_match: str | None = None) -> str | None:
        def write() -> str | None:
            params = {"Bucket": self._bucket, "Key": self._key(key), "Body": data, "ContentType": content_type}
            if if_match:
                params["IfMatch"] = if_match
            if if_none_match:
                params["IfNoneMatch"] = if_none_match
            try:
                return self._client.put_object(**params)["ETag"]
            except ClientError as exc:
                if exc.response["Error"]["Code"] in ("PreconditionFailed", "ConditionalRequestConflict"):
                    return None
                raise

        return await asyncio.to_thread(write)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=self._key(key))

    async def open(self, key: str) -> StoredObject:
        response = await asyncio.to_thread(self._client.get_object, Bucket=self._bucket, Key=self._key(key))
        body = response["Body"]

        async def chunks() -> AsyncIterator[bytes]:
            try:
                while chunk := await asyncio.to_thread(body.read, STREAM_CHUNK_BYTES):
                    yield chunk
            finally:
                body.close()

        return StoredObject(size=response["ContentLength"], content_type=response.get("ContentType", ""), chunks=chunks())

    async def exists(self, key: str) -> bool:
        def head() -> bool:
            try:
                self._client.head_object(Bucket=self._bucket, Key=self._key(key))
            except ClientError:
                return False
            return True

        return await asyncio.to_thread(head)

    async def list_dirs(self, prefix: str) -> list[str]:
        def ls() -> list[str]:
            base = self._key(prefix).rstrip("/") + "/"
            names = []
            for page in self._client.get_paginator("list_objects_v2").paginate(
                Bucket=self._bucket, Prefix=base, Delimiter="/"
            ):
                names.extend(cp["Prefix"].rstrip("/").rsplit("/", 1)[-1] for cp in page.get("CommonPrefixes", []))
            return names

        return await asyncio.to_thread(ls)

    async def delete_prefix(self, prefix: str) -> None:
        def rm() -> None:
            base = self._key(prefix).rstrip("/") + "/"
            for page in self._client.get_paginator("list_objects_v2").paginate(Bucket=self._bucket, Prefix=base):
                keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
                if keys:
                    self._client.delete_objects(Bucket=self._bucket, Delete={"Objects": keys})

        await asyncio.to_thread(rm)
