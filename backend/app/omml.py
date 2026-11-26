"""LaTeX math -> native Word equations (OMML). One pandoc run converts every equation of an export that is not cached yet
(docx writer, then the `m:oMath` / `m:oMathPara` elements are taken out of its document.xml). Cached by sha256 of the LaTeX."""
import copy
import hashlib
import io
import zipfile

from lxml import etree

from . import pandoc_bridge as B

M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_cache: dict[str, bytes | None] = {}  # key -> serialized element (None = pandoc could not typeset it)
MAX_CACHE = 5000
MAX_LATEX = 20_000


def key(latex: str, display: bool) -> str:
    return hashlib.sha256((("D" if display else "I") + latex).encode()).hexdigest()


def prepare(items) -> None:
    """items: iterable of (latex, display); converts the uncached ones in a single pandoc run"""
    todo = {key(l, d): (l, d) for l, d in items if l and len(l) <= MAX_LATEX and key(l, d) not in _cache}
    if not todo:
        return
    if len(_cache) > MAX_CACHE:
        _cache.clear()
    paras = [B.E("Para", [B.E("Math", [B.E("DisplayMath" if d else "InlineMath"), l])]) for l, d in todo.values()]
    try:
        data = B.run(["-f", "json", "-t", "docx", "-o", "-"], B._ast(paras))
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            body = etree.fromstring(z.read("word/document.xml"), etree.XMLParser(resolve_entities=False, no_network=True)).find(f"{{{W}}}body")
        ps = body.findall(f"{{{W}}}p")
        assert len(ps) == len(todo)
    except (B.BridgeError, zipfile.BadZipFile, etree.XMLSyntaxError, AssertionError):
        for k in todo:
            _cache[k] = None
        return
    for (k, (_, d)), p in zip(todo.items(), ps):
        el = p.find(f"{{{M}}}oMathPara" if d else f"{{{M}}}oMath")
        _cache[k] = etree.tostring(el) if el is not None else None


def get(latex: str, display: bool):
    """a fresh OMML element for the equation, or None when it could not be typeset"""
    prepare([(latex, display)])
    blob = _cache.get(key(latex, display))
    return etree.fromstring(blob) if blob else None


def collect(nodes) -> list:
    out = []
    for n in nodes or []:
        if n.get("type") == "math":
            a = n.get("attrs") or {}
            out.append((str(a.get("latex") or ""), bool(a.get("display"))))
        out += collect(n.get("content"))
    return out
