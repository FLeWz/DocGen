"""Round 11: every date is DD.MM.YYYY: parsing, doc.date, date variables, date filter default, revision rows, import detection."""
import datetime as dt
import io

import pytest
from docx import Document
from pydantic import ValidationError

from app import docx_export, docx_vars
from app.dates import dmy, parse_date
from app.schemas import Variables

from .test_base_docs import make_docx


@pytest.mark.parametrize("src,want", [
    ("24.11.2026", "24.11.2026"), ("1.2.2026", "01.02.2026"), ("2026-11-24", "24.11.2026"), ("2026-11-24T10:00:00+01:00", "24.11.2026"),
    ("13/01/2026", "13.01.2026"), ("01/13/2026", "13.01.2026"), ("05/03/2026", "05.03.2026"),  # ambiguous slash = DD/MM
    ("5 March 2026", "05.03.2026"), ("March 5, 2026", "05.03.2026"), ("24. novembra 2026", "24.11.2026"), ("3 maj 2026", "03.05.2026"),
    ("29.02.2024", "29.02.2024")])
def test_parse_formats(src, want):
    assert dmy(src) == want


@pytest.mark.parametrize("bad", ["31.02.2026", "29.02.2025", "2026-13-01", "x", "", None])
def test_parse_rejects(bad):
    assert parse_date(bad) is None


def build(paras, variables=None, rows=(), date="2026-11-24"):
    def mod(d):
        for p in paras:
            d.add_paragraph(p)
    spec = dict(title="T", blocks=[dict(type="snippet_ref", slot="CONTENT", pin=dict(snippet_id="s", sha="a" * 40, folder_id="f"))],
                variables=variables or dict(declarations=[], values={}), links=[], revision_history=list(rows),
                built_ins={"doc.title": "T", "doc.version": "1", "doc.date": date, "org.name": "O", "author": "A"})
    out, _ = docx_export.build(spec, make_docx(mod), lambda pin: ({"type": "doc", "content": []}, "s"), lambda *a: None)
    return [p.text for p in Document(io.BytesIO(out)).paragraphs]


def test_export_renders_dates_as_dmy():
    vars_ = dict(declarations=[dict(name="due", type="date", default=None)], values={"due": "2026-03-05"})
    t = build(["{{ doc.date }}|{{ due }}|{{ due|datefmt('') }}|{{ doc.date|datefmt('YYYY-MM-DD') }}|{{ doc.date|datefmt('%d/%m/%y') }}"], vars_)
    assert "24.11.2026|05.03.2026|05.03.2026|2026-11-24|24/11/26" in t


def test_stored_dmy_values_still_render():
    vars_ = dict(declarations=[dict(name="due", type="date", default="2026-01-02")], values={"due": "5.3.2026"})
    assert "05.03.2026" in build(["{{ due }}"], vars_)


def test_revision_rows_are_dmy_even_if_stored_iso():
    t = build(["{{ doc.version }}"], rows=[dict(version="v1", date="2026-01-31", author="a", message="m")])
    ctx = docx_export.context(dict(variables=dict(declarations=[], values={}), links=[], built_ins={
        "doc.title": "T", "doc.version": "1", "doc.date": "2026-11-24", "org.name": "O", "author": "A"},
        revision_history=[dict(version="v1", date="2026-01-31")]))
    assert ctx["revision_history"][0]["date"] == "31.01.2026" and t


def test_variables_api_accepts_both_stores_iso():
    v = Variables(declarations=[dict(name="d", type="date", default="1.2.2026")], values={"d": "24.11.2026"})
    assert v.values["d"] == "2026-11-24" and v.declarations[0].default == "2026-02-01"
    with pytest.raises(ValidationError):
        Variables(declarations=[dict(name="d", type="date")], values={"d": "31.02.2026"})


def test_import_detects_dates_and_declares_them_typed():
    def mod(d):
        d.add_paragraph("5 March 2026")
        d.add_paragraph("24. novembra 2026")
    root = Document(io.BytesIO(make_docx(mod))).element.body
    cands = {c["value"]: c for c in docx_vars.scan([("body", root, True)])}
    assert cands["5 March 2026"]["kind"] == "date" and cands["5 March 2026"]["display"] == "05.03.2026"
    assert cands["24. novembra 2026"]["display"] == "24.11.2026"
    from types import SimpleNamespace as N
    d = docx_vars.picks_decls([N(name="d1", value="5 March 2026")])[0]
    assert (d["type"], d["default"]) == ("date", "2026-03-05")
