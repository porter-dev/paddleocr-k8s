import io
import posixpath
import re
import zipfile

from .ocr import PageResult


# Image keys coming back from PaddleOCR are derived from the crop's bounding box, so two
# pages can produce the same key. Prefixing with the page number keeps every image of the
# document distinct; the same rename is applied inside prunedResult so restructure-pages
# regenerates markdown that still points at the stored files.
def namespace_images(page_number: int, page: PageResult) -> PageResult:
    renames = {key: f"imgs/p{page_number:05d}_{posixpath.basename(key)}" for key in page.images}
    return PageResult(
        pruned_result=rename_strings(page.pruned_result, renames),
        markdown=rename_strings(page.markdown, renames),
        images={renames[key]: data for key, data in page.images.items()},
    )


def rename_strings(value, renames: dict[str, str]):
    if isinstance(value, str):
        for old, new in renames.items():
            if old in value:
                value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [rename_strings(item, renames) for item in value]
    if isinstance(value, dict):
        return {key: rename_strings(item, renames) for key, item in value.items()}
    return value


def concatenate(page_markdowns: list[str]) -> str:
    return "\n\n".join(text.strip() for text in page_markdowns if text.strip()) + "\n"


def build_zip(markdown: str, images: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("result.md", markdown)
        for key, data in images.items():
            archive.writestr(key, data)
    return out.getvalue()


IMAGE_REF = re.compile(r"imgs/[^\s\"'()<>]+")
PAGE_PREFIX = re.compile(r"^imgs/p\d{5}_")


# restructure-pages regenerates markdown from the pruned layout data, where image names are
# derived from label and bounding box rather than stored, so the merged document refers to
# the un-prefixed names again. Pages appear in order in the merged markdown, so each
# reference is resolved to the earliest page at or after the previous match that produced
# that name; a name no page produced is left as is.
def relink_images(markdown: str, page_image_keys: list[tuple[int, list[str]]]) -> str:
    originals = [
        (page_number, {PAGE_PREFIX.sub("imgs/", key): key for key in keys})
        for page_number, keys in page_image_keys
    ]
    cursor = 0

    def resolve(match: re.Match) -> str:
        nonlocal cursor
        name = match.group(0)
        for index in list(range(cursor, len(originals))) + list(range(0, cursor)):
            stored = originals[index][1].get(name)
            if stored is not None:
                cursor = index
                return stored
        return name

    return IMAGE_REF.sub(resolve, markdown)
