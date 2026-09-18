import base64
from dataclasses import dataclass

import httpx


class OCRError(RuntimeError):
    pass


@dataclass
class PageResult:
    pruned_result: dict
    markdown: str
    images: dict[str, bytes]


# Client for the PaddleOCR-VL serving API (basic serving and the HPS gateway expose the
# same contract): POST /layout-parsing for OCR, POST /restructure-pages to merge tables
# and re-level titles across pages once every page has been recognised.
class PaddleOCRClient:
    def __init__(self, base_url: str, timeout_seconds: float):
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def ready(self) -> bool:
        try:
            response = await self._http.get("/health/ready", timeout=5.0)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def layout_parsing(self, pdf: bytes) -> list[PageResult]:
        body = {
            "file": base64.b64encode(pdf).decode(),
            "fileType": 0,
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "visualize": False,
            "returnMarkdownImages": True,
        }
        result = await self._call("/layout-parsing", body)
        return [
            PageResult(
                pruned_result=page["prunedResult"],
                markdown=page["markdown"]["text"],
                images={key: base64.b64decode(value) for key, value in (page["markdown"].get("images") or {}).items()},
            )
            for page in result["layoutParsingResults"]
        ]

    async def restructure_pages(self, pruned_results: list[dict]) -> str:
        body = {
            "pages": [{"prunedResult": pruned} for pruned in pruned_results],
            "concatenatePages": True,
            "mergeTables": True,
            "relevelTitles": True,
        }
        result = await self._call("/restructure-pages", body)
        return result["layoutParsingResults"][0]["markdown"]["text"]

    async def _call(self, path: str, body: dict) -> dict:
        response = await self._http.post(path, json=body)
        if response.status_code != 200:
            raise OCRError(f"{path} returned HTTP {response.status_code}: {response.text[:300]}")
        payload = response.json()
        if payload.get("errorCode", 0) != 0:
            raise OCRError(f"{path} failed: {payload.get('errorMsg', 'unknown error')}")
        return payload["result"]
