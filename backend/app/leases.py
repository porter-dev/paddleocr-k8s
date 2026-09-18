import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from .storage import Storage

log = logging.getLogger(__name__)

LEASE_TTL = timedelta(seconds=45)
RENEW_EVERY_SECONDS = 15

OWNER = f"{os.environ.get('HOSTNAME', 'local')}-{os.getpid()}"


# A lease is a small S3 object naming the pod working on a chunk and when its claim expires.
# Another pod may only take the chunk once the lease has lapsed, so a pod that is draining
# keeps its in-flight chunks while a freshly started pod picks up the rest. Claims go through
# conditional writes, so two pods racing for an expired lease cannot both win.
class Lease:
    def __init__(self, storage: Storage, key: str):
        self._storage = storage
        self._key = key
        self._etag: str | None = None

    async def try_acquire(self) -> bool:
        current = await self._storage.get_versioned(self._key)
        if current is None:
            self._etag = await self._storage.put_if(self._key, self._body(), "application/json", if_none_match="*")
            log.info("lease %s: %s (was absent)", self._key, "acquired" if self._etag else "lost race")
            return self._etag is not None
        data, etag = current
        try:
            held = json.loads(data)
            expires = datetime.fromisoformat(held["expires_at"])
        except (ValueError, KeyError):
            expires = datetime.min.replace(tzinfo=timezone.utc)
            held = {}
        if expires > now() and held.get("owner") != OWNER:
            log.info("lease %s: held by %s until %s", self._key, held.get("owner"), held.get("expires_at"))
            return False
        self._etag = await self._storage.put_if(self._key, self._body(), "application/json", if_match=etag)
        log.info("lease %s: %s (previous owner %s, expired %s)", self._key, "taken over" if self._etag else "lost race", held.get("owner"), held.get("expires_at"))
        return self._etag is not None

    async def holder(self) -> str | None:
        current = await self._storage.get_versioned(self._key)
        if current is None:
            return None
        try:
            return json.loads(current[0]).get("owner")
        except ValueError:
            return None

    async def renew(self) -> bool:
        if self._etag is None:
            return False
        etag = await self._storage.put_if(self._key, self._body(), "application/json", if_match=self._etag)
        if etag is None:
            log.warning("lost lease %s to another owner", self._key)
            return False
        self._etag = etag
        return True

    async def release(self) -> None:
        self._etag = None
        await self._storage.delete(self._key)

    @asynccontextmanager
    async def held(self):
        async def keep_alive() -> None:
            while True:
                await asyncio.sleep(RENEW_EVERY_SECONDS)
                await self.renew()

        renewer = asyncio.create_task(keep_alive())
        try:
            yield self
        finally:
            renewer.cancel()
            await asyncio.gather(renewer, return_exceptions=True)

    def _body(self) -> bytes:
        return json.dumps({"owner": OWNER, "expires_at": (now() + LEASE_TTL).isoformat()}).encode()


def now() -> datetime:
    return datetime.now(timezone.utc)
