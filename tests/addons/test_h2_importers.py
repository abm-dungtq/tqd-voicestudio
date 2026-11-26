import io
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from vizipvoice_addons import importers


def make_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)
    return out


def test_txt_import():
    r = importers.import_file("bai.txt", "Tiêu đề\n\nNội dung một hai ba.".encode("utf-8"))
    assert r["title"] == "Tiêu đề" and r["words"] == 7 and r["source"] == "bai.txt"


def test_markdown_cleaning():
    md = (
        "---\ntitle: x\n---\n# Tiêu đề\n\nĐoạn [liên kết](http://a.b/c) và **đậm**.\n\n"
        "![ảnh](a.png)\n\n```python\nprint('code')\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n"
        "<div>html</div>\n\n- mục một\n- mục hai\n"
    )
    text = importers.import_file("a.md", md.encode("utf-8"))["text"]
    assert "http" not in text and "print(" not in text and "|" not in text and "<div>" not in text
    assert "ảnh" not in text and "liên kết" in text and "đậm" in text and "mục một" in text
    assert "title: x" not in text


def test_docx_import():
    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.add_paragraph("Đoạn đầu tiên của tài liệu.")
    d.add_paragraph("Đoạn thứ hai.")
    buf = io.BytesIO()
    d.save(buf)
    r = importers.import_file("t.docx", buf.getvalue())
    assert r["text"] == "Đoạn đầu tiên của tài liệu.\n\nĐoạn thứ hai." and r["words"] == 9


def test_pdf_import():
    pytest.importorskip("pypdf")
    r = importers.import_file("t.pdf", make_pdf("Hello PDF world"))
    assert "Hello PDF world" in r["text"]


def test_unsupported_and_empty():
    with pytest.raises(importers.ImportFailure):
        importers.import_file("x.exe", b"abc")
    with pytest.raises(importers.ImportFailure):
        importers.import_file("x.txt", b"   ")


class _Page(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (
            "<html><head><title>Bài báo thử</title></head><body><div class='nav'>Trang chủ Liên hệ</div>"
            "<article><h1>Bài báo thử</h1><p>" + "Đây là đoạn văn rất dài về một chủ đề quan trọng. " * 8 +
            "</p><p>" + "Đoạn thứ hai bổ sung nhiều thông tin hữu ích khác. " * 8 +
            "</p></article><div class='ads'>Quảng cáo mua ngay</div></body></html>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_url_import_local_server():
    pytest.importorskip("readability")
    srv = HTTPServer(("127.0.0.1", 0), _Page)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = importers.import_url(f"http://127.0.0.1:{srv.server_address[1]}/a")
    finally:
        srv.shutdown()
    assert r["title"].startswith("Bài báo")
    assert "đoạn văn rất dài" in r["text"] and r["words"] > 50
    assert "Quảng cáo" not in r["text"] and "Trang chủ" not in r["text"]


def test_url_scheme_rejected():
    with pytest.raises(importers.ImportFailure):
        importers.import_url("file:///etc/passwd")


def test_missing_dependency_message(monkeypatch):
    import builtins

    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "pypdf":
            raise ImportError("no")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    with pytest.raises(importers.MissingDependency) as exc:
        importers.from_pdf(b"x")
    assert "uv tool install" in str(exc.value) and "[import]" in str(exc.value)
