from io import BytesIO

from pypdf import PdfReader, PdfWriter


def count_pages(data: bytes) -> int:
    return len(open_pdf(data).pages)


def extract_pages(data: bytes, first_page: int, last_page: int) -> bytes:
    reader = open_pdf(data)
    writer = PdfWriter()
    for index in range(first_page - 1, last_page):
        writer.add_page(reader.pages[index])
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


# "Secured" PDFs are often AES-encrypted with an empty user password; they open once
# decrypted with it, and PaddleOCR renders the extracted pages without any password.
def open_pdf(data: bytes) -> PdfReader:
    reader = PdfReader(BytesIO(data))
    if reader.is_encrypted and reader.decrypt("") == 0:
        raise ValueError("PDF is password-protected")
    return reader
