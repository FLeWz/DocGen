"""Header/footer parts as editable content. `content["hf"]` = {part path: TipTap doc} next to the body blocks of an import:
read (`part_doc`: header XML -> TipTap, PAGE/NUMPAGES as `field` nodes, TITLE/AUTHOR/DATE as built-in variable chips),
validated on save (`check`), written back on export (`write`: the part is re-serialised with the exporter's Builder; relationships
and images of the part stay) and restored byte for byte when a part was not edited (`restore`)."""
import io
import posixpath
import zipfile

from docx.blkcntnr import BlockItemContainer
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from lxml import etree

from . import docx_import as D
from .docx_export import Builder
from .ooxml import stable_zip

MAX_PARTS = 40
HF_RE = ("word/header", "word/footer")


def part_paths(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return sorted(n for n in z.namelist() if n.startswith(HF_RE) and n.endswith(".xml"))


def _rels(z: zipfile.ZipFile, path: str) -> dict:
    rp = posixpath.join(posixpath.dirname(path), "_rels", posixpath.basename(path) + ".rels")
    return {r.get("Id"): r for r in etree.fromstring(z.read(rp))} if rp in z.namelist() else {}


def rel_ids_of(data: bytes, path: str) -> set:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return set(_rels(z, path))


def part_doc(data: bytes, path: str, store_image) -> tuple[dict, list[str]]:
    """TipTap doc of one header/footer part of the package (`data` already validated)."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if path not in part_paths(data):
            raise KeyError(path)
        ps = D._Parser(z, store_image, data)
        ps.rels, ps.hf = _rels(z, path), True
        ps.rel_ids = set(ps.rels)
        root = etree.fromstring(z.read(path), etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))
        blocks = ps.blocks(root)
    for b in blocks:
        for n in D.walk(b):
            (n.get("attrs") or {}).pop("orig", None)
    warn = [f"{n}× {k}: kept as-is (not editable)" for k, n in ps.opaque.items()]
    return {"type": "doc", "content": blocks or [{"type": "paragraph", "attrs": {"styleId": None}}]}, warn


def check(hf, data: bytes) -> None:
    """Client-supplied `hf`: only parts of this package, every opaque fragment/table property valid for that part's relationships."""
    if hf is None:
        return
    if not isinstance(hf, dict) or len(hf) > MAX_PARTS:
        raise ValueError("hf must be an object of at most %d parts" % MAX_PARTS)
    known = set(part_paths(data))
    for path, doc in hf.items():
        if path not in known:
            raise ValueError(f"unknown header/footer part {str(path)[:60]!r}")
        if not isinstance(doc, dict) or doc.get("type") != "doc" or not isinstance(doc.get("content"), list):
            raise ValueError("invalid header/footer content")
        D.check_content(doc, rel_ids_of(data, path))


class PartContainer(BlockItemContainer):
    def __init__(self, el, part):
        super().__init__(el, part)
        self._p = part

    @property
    def part(self):
        return self._p


def write(doc, hf: dict, load_asset, warnings: list) -> set:
    """Replace the content of the edited parts of the open python-docx document `doc`; returns the paths written."""
    parts = {str(r.target_part.partname).lstrip("/"): r.target_part for r in doc.part.rels.values() if r.reltype in (RT.HEADER, RT.FOOTER)}
    done = set()
    for path, content in (hf or {}).items():
        part = parts.get(path)
        if part is None:
            warnings.append(f"header/footer part {path} not in the package: skipped")
            continue
        el = part.element
        for ch in list(el):
            el.remove(ch)
        cont = PartContainer(el, part)
        b = Builder(doc, load_asset, warnings, rels=set(part.rels))
        b.top = [cont]
        b.nodes(content.get("content"), cont, {})
        if el.find(qn("w:p")) is None:  # a header needs a block-level element
            el.append(OxmlElement("w:p"))
        done.add(path)
    return done


def _c14n(b: bytes) -> bytes:
    return etree.tostring(etree.fromstring(b), method="c14n")


def restore(orig: bytes, saved: bytes, edited: set) -> bytes:
    """python-docx re-serialises every XML part it opened; header/footer parts that were not edited (and did not change in
    content) go back to the original bytes."""
    keep = {}
    with zipfile.ZipFile(io.BytesIO(orig)) as zo, zipfile.ZipFile(io.BytesIO(saved)) as zs:
        for n in part_paths(orig):
            if n not in edited and n in zs.namelist():
                try:
                    if _c14n(zo.read(n)) == _c14n(zs.read(n)):
                        keep[n] = zo.read(n)
                except etree.XMLSyntaxError:
                    pass
        if not keep:
            return saved
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for i in zs.infolist():
                zout.writestr(i.filename, keep.get(i.filename, zs.read(i)))
    return out.getvalue()
