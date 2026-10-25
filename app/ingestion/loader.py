import csv
import io
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from app.config import settings
from app.utils.helpers import human_size

PDF_TEXT_FLAGS = pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_PRESERVE_LIGATURES
TEXT_EXTENSIONS = {".txt", ".md"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
SIGNATURES = {
    ".pdf": (b"%PDF-",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
}
ROW_TOLERANCE = 3.0
REPEATED_LINE_MAX_LENGTH = 120


class FileRejectedError(ValueError):
    pass


@dataclass(frozen=True)
class PageText:
    page: int | None
    text: str


@dataclass(frozen=True)
class LoadedDocument:
    path: Path
    document: str
    file_type: str
    pages: tuple[PageText, ...]
    needs_ocr: bool = False

    @property
    def has_text(self):
        return any(page.text for page in self.pages)


def reject(path, reason):
    raise FileRejectedError(f"{path.name}: {reason}")


def validate_file(path: Path, max_bytes=None, allowed_extensions=None):
    max_bytes = settings.max_file_bytes if max_bytes is None else max_bytes
    allowed_extensions = allowed_extensions or settings.allowed_extensions
    if path.is_symlink():
        reject(path, "symbolic links are not accepted.")
    if not path.is_file():
        reject(path, "not found or not a regular file.")
    extension = path.suffix.lower()
    if extension not in allowed_extensions:
        accepted = ", ".join(allowed_extensions)
        reject(path, f"file type '{extension or 'none'}' is not supported. Accepted types: {accepted}.")
    size = path.stat().st_size
    if size == 0:
        reject(path, "the file is empty.")
    if size > max_bytes:
        reject(path, f"the file is {human_size(size)}, above the {human_size(max_bytes)} limit.")
    expected = SIGNATURES.get(extension)
    if expected:
        with path.open("rb") as handle:
            head = handle.read(16)
        if not head.startswith(expected):
            reject(path, f"the content does not match the '{extension}' file type.")
    return extension


def clean_line(text):
    text = unicodedata.normalize("NFKC", text).replace(" ", " ")
    return re.sub(r"[ \t]+", " ", text).strip()


def clean_multiline(text):
    lines = [clean_line(line) for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def join_wrapped_lines(block_text):
    joined = ""
    for line in (clean_line(part) for part in block_text.splitlines()):
        if not line:
            continue
        if not joined:
            joined = line
        elif joined.endswith("-") and not joined.endswith(" -"):
            joined += line
        else:
            joined += " " + line
    return joined


def page_lines(page):
    rows = []
    for x0, y0, _, _, text, _, block_type in page.get_text("blocks", flags=PDF_TEXT_FLAGS, sort=True):
        if block_type != 0:
            continue
        cleaned = join_wrapped_lines(text)
        if not cleaned:
            continue
        if rows and abs(rows[-1][0] - y0) <= ROW_TOLERANCE:
            rows[-1][1].append((x0, cleaned))
        else:
            rows.append([y0, [(x0, cleaned)]])
    return [" ".join(text for _, text in sorted(cells)) for _, cells in rows]


def line_signature(line):
    return re.sub(r"\d+", "#", line.lower())


def remove_repeated_lines(pages):
    if len(pages) < 2:
        return pages
    counts = Counter(signature for lines in pages for signature in {line_signature(line) for line in lines})
    repeated = {
        signature
        for signature, count in counts.items()
        if count == len(pages) and len(signature) <= REPEATED_LINE_MAX_LENGTH
    }
    return [[line for line in lines if line_signature(line) not in repeated] for lines in pages]


def load_pdf(path):
    try:
        doc = pymupdf.open(path)
    except (pymupdf.FileDataError, RuntimeError) as error:
        reject(path, f"could not be read as a PDF ({error}).")
    with doc:
        if doc.needs_pass or doc.is_encrypted:
            reject(path, "password-protected PDFs are not accepted.")
        if doc.page_count > settings.max_pdf_pages:
            reject(path, f"the PDF has {doc.page_count} pages, above the {settings.max_pdf_pages} page limit.")
        lines_per_page = remove_repeated_lines([page_lines(page) for page in doc])
        has_images = any(page.get_images() for page in doc)
    pages = tuple(PageText(number, "\n".join(lines)) for number, lines in enumerate(lines_per_page, start=1))
    needs_ocr = has_images and not any(page.text for page in pages)
    return pages, needs_ocr


def decode_text(path):
    raw = path.read_bytes()
    if b"\x00" in raw:
        reject(path, "the file contains binary data and is not a valid text file.")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        reject(path, "the file is not valid UTF-8 text.")


def load_text(path):
    return (PageText(None, clean_multiline(decode_text(path))),), False


def load_csv(path):
    reader = csv.reader(io.StringIO(decode_text(path)))
    header = [clean_line(name) for name in next(reader, [])]
    if not header:
        reject(path, "the CSV file has no header row.")
    lines = []
    for number, row in enumerate(reader, start=1):
        cells = [f"{name}: {clean_line(value)}" for name, value in zip(header, row) if value.strip()]
        if cells:
            lines.append(f"Row {number} | " + " | ".join(cells))
    return (PageText(None, "\n".join(lines)),), False


def load_image(path):
    return (), True


LOADERS = {".pdf": load_pdf, ".csv": load_csv, **{ext: load_text for ext in TEXT_EXTENSIONS}, **{ext: load_image for ext in IMAGE_EXTENSIONS}}


def load_document(path: Path, max_bytes=None):
    extension = validate_file(path, max_bytes=max_bytes)
    pages, needs_ocr = LOADERS[extension](path)
    return LoadedDocument(path=path, document=path.name, file_type=extension.lstrip("."), pages=pages, needs_ocr=needs_ocr)
