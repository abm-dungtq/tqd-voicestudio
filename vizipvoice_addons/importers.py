"""Text import: .txt .md .docx .pdf files and web pages. Optional deps give 501-able errors."""

from __future__ import annotations

import io
import re
import urllib.request
from typing import Dict, Tuple
from urllib.parse import urlparse

INSTALL_FIX = (
    'uv tool install --reinstall --python 3.12 --torch-backend=auto '
    '"tqd-voicestudio[import] @ git+https://github.com/abm-dungtq/tqd-voicestudio@v0.3.0"'
)
MAX_URL_BYTES = 5 * 1024 * 1024


class MissingDependency(RuntimeError):
    def __init__(self, package: str):
        super().__init__(f"Thiếu thư viện '{package}'. Cài lại với: {INSTALL_FIX}")
        self.package = package


class ImportFailure(ValueError):
    pass


def word_count(text: str) -> int:
    return len(text.split())


def clean_markdown(md: str) -> str:
    text = re.sub(r"(?s)^---\n.*?\n---\n", "", md)
    text = re.sub(r"(?s)```.*?```", "", text)
    text = re.sub(r"(?s)~~~.*?~~~", "", text)
    text = re.sub(r"(?s)<!--.*?-->", "", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"!\[[^\]]*\]\[[^\]]*\]", "", text)
    text = re.sub(r"(?m)^\s*\|.*\|\s*$", "", text)
    text = re.sub(r"(?m)^\s*[:\-| ]{3,}\s*$", "", text)
    text = re.sub(r"(?s)<(script|style)\b.*?</\1>", "", text, flags=re.I)
    text = re.sub(r"</?[A-Za-z][^>]*>", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\[[^\]]*\]", r"\1", text)
    text = re.sub(r"(?m)^\s*\[[^\]]+\]:\s*\S+.*$", "", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^\s*>\s?", "", text)
    text = re.sub(r"(?m)^\s*([-*+]|\d+\.)\s+", "", text)
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
    text = re.sub(r"(?<!\w)([*_])(.+?)\1(?!\w)", r"\2", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"(?m)^\s*([-*_]\s*){3,}$", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _title_from_text(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:120]
    return fallback


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("cp1258", errors="replace")


def from_docx(data: bytes) -> str:
    try:
        import docx
    except ImportError as exc:
        raise MissingDependency("python-docx") from exc
    try:
        doc = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise ImportFailure(f"không đọc được file docx: {exc}") from exc
    return "\n\n".join(p.text.strip() for p in doc.paragraphs if p.text.strip())


def from_pdf(data: bytes) -> str:
    try:
        import pypdf
    except ImportError as exc:
        raise MissingDependency("pypdf") from exc
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        pages = [(p.extract_text() or "").strip() for p in reader.pages]
    except Exception as exc:
        raise ImportFailure(f"không đọc được file pdf: {exc}") from exc
    return "\n\n".join(p for p in pages if p)


def import_file(filename: str, data: bytes) -> Dict[str, object]:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext == "txt":
        text = _decode(data).strip()
    elif ext in ("md", "markdown"):
        text = clean_markdown(_decode(data))
    elif ext == "docx":
        text = from_docx(data)
    elif ext == "pdf":
        text = from_pdf(data)
    else:
        raise ImportFailure("chỉ hỗ trợ .txt .md .docx .pdf")
    if not text.strip():
        raise ImportFailure("không tìm thấy nội dung văn bản")
    return {"title": _title_from_text(text, filename), "text": text, "source": filename, "words": word_count(text)}


def fetch_html(url: str, timeout: float = 20.0) -> str:
    scheme = urlparse(url).scheme
    if scheme not in ("http", "https"):
        raise ImportFailure("chỉ hỗ trợ URL http/https")
    req = urllib.request.Request(url, headers={"User-Agent": "tqd-voicestudio/0.3 (+import)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(MAX_URL_BYTES + 1)
        charset = resp.headers.get_content_charset() or "utf-8"
    if len(data) > MAX_URL_BYTES:
        raise ImportFailure("trang web quá lớn (>5 MB)")
    return data.decode(charset, errors="replace")


def from_html(html: str) -> Tuple[str, str]:
    try:
        from readability import Document
    except ImportError as exc:
        raise MissingDependency("readability-lxml") from exc
    import lxml.html

    doc = Document(html)
    body = lxml.html.fromstring(doc.summary(html_partial=True))
    for bad in body.xpath("//script|//style|//nav|//aside|//footer|//form"):
        bad.getparent().remove(bad)
    blocks = []
    for el in body.iter("p", "h1", "h2", "h3", "h4", "li", "blockquote"):
        t = " ".join(el.text_content().split())
        if t:
            blocks.append(t)
    text = "\n\n".join(blocks) if blocks else " ".join(body.text_content().split())
    return doc.short_title().strip(), text


def import_url(url: str) -> Dict[str, object]:
    try:
        html = fetch_html(url)
    except ImportFailure:
        raise
    except Exception as exc:
        raise ImportFailure(f"không tải được trang: {exc}") from exc
    title, text = from_html(html)
    if not text.strip():
        raise ImportFailure("không tìm thấy nội dung bài viết")
    return {"title": title or _title_from_text(text, url), "text": text, "source": url, "words": word_count(text)}
