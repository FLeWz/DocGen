"""Milestone 6: PDF export through Gotenberg. Mocked server for the error mapping, the real container for content."""
import io
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest
from pypdf import PdfReader

from app import config

from .test_documents import put
from .test_dynamic import CCELL, CHIP, ROW, TEAM, ref
from .test_export import CELL, fetch, run_export, ws  # noqa: F401  (ws is a fixture)
from .test_git import commit, mk


class Fake(BaseHTTPRequestHandler):
    reply = (200, b"%PDF-1.7 fake", 0.0)
    seen: list = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Fake.seen.append((self.path, body))
        status, data, delay = Fake.reply
        time.sleep(delay)
        try:
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:  # client gave up (timeout test)
            pass

    def log_message(self, *a):
        pass


@pytest.fixture
def gotenberg(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(config, "PDF_URL", f"http://127.0.0.1:{srv.server_port}")
    Fake.seen, Fake.reply = [], (200, b"%PDF-1.7 fake", 0.0)
    yield Fake
    srv.shutdown()


def test_pdf_export_sends_docx_and_serves_pdf(ws, gotenberg):
    c = ws["c"]
    eid, st = run_export(c, ws["doc"], "pdf")
    assert st["status"] == "done", st
    r = c.get(f"/api/exports/{eid}/download")
    assert r.headers["content-type"] == "application/pdf" and r.content == b"%PDF-1.7 fake"
    assert r.headers["content-disposition"].endswith('.pdf"')
    path, body = gotenberg.seen[0]
    assert path == "/forms/libreoffice/convert" and b"updateIndexes" in body
    assert b"PK\x03\x04" in body  # the DOCX is the multipart file part


@pytest.mark.parametrize("reply,delay,limit,msg", [
    ((500, b"boom from gotenberg", 0), None, None, "HTTP 500"),
    ((200, b"<html>nope</html>", 0), None, None, "not a PDF"),
    ((200, b"%PDF" + b"x" * 5000, 0), None, 1000, "limit"),
    ((200, b"%PDF-1", 2.0), 0.3, None, "timed out"),
])
def test_pdf_errors_become_export_errors(ws, gotenberg, monkeypatch, reply, delay, limit, msg):
    gotenberg.reply = reply
    if delay:
        monkeypatch.setattr(config, "PDF_TIMEOUT_S", delay)
    if limit:
        monkeypatch.setattr(config, "PDF_MAX_BYTES", limit)
    eid, st = run_export(ws["c"], ws["doc"], "pdf")
    assert st["status"] == "error" and msg in st["error"], st
    assert ws["c"].get(f"/api/exports/{eid}/download").status_code == 409


def test_pdf_service_down_is_a_clean_error(ws, monkeypatch):
    monkeypatch.setattr(config, "PDF_URL", "http://127.0.0.1:9")
    eid, st = run_export(ws["c"], ws["doc"], "pdf")
    assert st["status"] == "error" and "unavailable" in st["error"], st


def test_pdf_missing_variables_422_same_as_docx(ws, gotenberg):
    c = ws["c"]
    put(c, ws["doc"], [], variables={"declarations": [{"name": "needed"}], "values": {}})
    r = c.get(f"/api/documents/{ws['doc']}/export", params={"format": "pdf"})
    assert r.status_code == 422 and r.json()["detail"]["variables"] == [{"name": "needed", "type": "text"}]


def live() -> bool:
    try:
        return httpx.get(config.PDF_URL + "/health", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.mark.skipif(not live(), reason="Gotenberg container not reachable")
def test_live_pdf_has_content_variables_tables_and_refreshed_toc(ws):
    c = ws["c"]
    sid = mk(c, ws["fa"], "Team")
    content = {"type": "doc", "content": [{"type": "table", "attrs": {"dynamicList": "team"}, "content": [
        ROW(CELL("Name"), CELL("Joined")),
        ROW(CCELL(CHIP("row.name")), CCELL(CHIP("row.joined", "", "date:DD/MM/YYYY")))]}]}
    c.put(f"/api/snippets/{sid}/draft", json={"content": content})
    sha = commit(c, ws["fa"], [sid]).json()["sha"]
    put(c, ws["doc"], [
        {"type": "heading", "text": "Unique chapter heading", "level": 1, "numbered": False}, {"type": "toc"},
        ref(ws["fa"], ws["sid"], ws["v1"]), {"type": "page_break"},
        {"type": "heading", "text": "Second chapter", "level": 1, "numbered": False}, ref(ws["fa"], sid, sha)],
        base_document=None,
        variables={"declarations": TEAM["declarations"] + [{"name": "client", "default": "Acme"}],
                   "values": {**TEAM["values"], "client": "Globex"}})
    eid, st = run_export(c, ws["doc"], "pdf")
    assert st["status"] == "done", st
    data = fetch(c, eid)
    assert data.startswith(b"%PDF")
    pdf = PdfReader(io.BytesIO(data))
    assert len(pdf.pages) >= 2  # the page break
    text = "\n".join(p.extract_text() for p in pdf.pages)
    for needle in ("GLOBEX", "Overview", "ann", "04/03/2026", "bob <b>", "Alice A", "Org A", "Second chapter"):
        assert needle in text, (needle, text[:800])
    assert text.count("Unique chapter heading") >= 2, "TOC was not refreshed by LibreOffice"
    assert "Right-click" not in text and "{{" not in text and "{%" not in text
