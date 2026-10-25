import io
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageFilter

PAGE_WIDTH, PAGE_HEIGHT = pymupdf.paper_size("a4")
MARGIN = 54
FOOTER_HEIGHT = 22

BASE_CSS = """
* { font-family: sans-serif; }
body { font-size: 10.5pt; line-height: 1.4; color: #1a1a1a; }
h1 { font-size: 17pt; margin: 0 0 4pt 0; }
h2 { font-size: 12.5pt; margin: 10pt 0 4pt 0; }
p { margin: 0 0 6pt 0; }
table { border-collapse: collapse; width: 100%; margin: 4pt 0 8pt 0; }
th, td { border: 0.6pt solid #888888; padding: 3pt 5pt; text-align: left; font-size: 9.5pt; }
th { background-color: #eeeeee; }
.muted { color: #555555; font-size: 9pt; }
.footer { color: #666666; font-size: 8pt; text-align: center; }
.code { white-space: nowrap; }
"""


def body_rect():
    return pymupdf.Rect(MARGIN, MARGIN, PAGE_WIDTH - MARGIN, PAGE_HEIGHT - MARGIN - FOOTER_HEIGHT)


def footer_rect():
    return pymupdf.Rect(MARGIN, PAGE_HEIGHT - MARGIN - FOOTER_HEIGHT + 6, PAGE_WIDTH - MARGIN, PAGE_HEIGHT - MARGIN + 10)


def place_html(page, rect, html, css, label):
    spare_height, _ = page.insert_htmlbox(rect, html, css=css, scale_low=1)
    if spare_height < 0:
        raise ValueError(f"Content does not fit on the page: {label}")


def save(doc, path: Path, title, author):
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.set_metadata({"title": title, "author": author, "creator": "PrivateDesk synthetic data"})
    doc.save(path, garbage=3, deflate=True)
    doc.close()


def render_pdf(path: Path, pages, title, author, footer=None, css=BASE_CSS):
    doc = pymupdf.open()
    for number, html in enumerate(pages, start=1):
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        place_html(page, body_rect(), html, css, f"{path.name} page {number}")
        if footer:
            footer_html = f"<p class='footer'>{footer} | Page {number} of {len(pages)}</p>"
            place_html(page, footer_rect(), footer_html, css, f"{path.name} footer")
    save(doc, path, title, author)


def degrade(image, seed):
    rng = np.random.default_rng(seed)
    image = image.rotate(0.8, resample=Image.Resampling.BICUBIC, fillcolor=255)
    image = image.filter(ImageFilter.GaussianBlur(radius=0.7))
    pixels = np.asarray(image, dtype=np.float32) * 0.9 + 18
    pixels -= np.linspace(0, 14, pixels.shape[1], dtype=np.float32)[np.newaxis, :]
    pixels += rng.normal(0, 10, pixels.shape)
    specks = rng.random(pixels.shape) < 0.0012
    pixels[specks] = rng.uniform(30, 90, int(specks.sum()))
    return Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))


def render_scanned_pdf(path: Path, html, title, author, seed, css=BASE_CSS, dpi=150):
    source = pymupdf.open()
    page = source.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    place_html(page, body_rect(), html, css, path.name)
    pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    source.close()
    image = degrade(Image.frombytes("L", (pixmap.width, pixmap.height), pixmap.samples), seed)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=58)
    scan = pymupdf.open()
    scan_page = scan.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    scan_page.insert_image(scan_page.rect, stream=buffer.getvalue())
    save(scan, path, title, author)
