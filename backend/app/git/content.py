"""Snippet <-> files in git, plus text and block-level diffs."""
import difflib
import json

import yaml

from . import repo as git

EMPTY_DOC = {"type": "doc", "content": []}


def files(snippet_id, v: dict) -> dict[str, str]:
    """Canonical file bodies (sorted keys) so equal content is byte-equal."""
    meta = {"id": str(snippet_id), "title": v["title"], "styles": v["styles"], "variables": v["variables"]}
    return {"content.json": json.dumps(v["content"], indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            "meta.yaml": yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)}


def version_at(repo, sha: str, snippet_id) -> dict | None:
    slug = git.slug_at(repo, sha, snippet_id)
    if not slug:
        return None
    c, m = (git.show(repo, sha, f"snippets/{slug}/{f}") for f in ("content.json", "meta.yaml"))
    if c is None or m is None:
        return None
    meta = yaml.safe_load(m)
    return dict(sha=sha, slug=slug, title=meta["title"], styles=meta.get("styles") or [],
                variables=meta.get("variables") or [], content=json.loads(c))


def same(snippet_id, a: dict | None, b: dict | None) -> bool:
    return bool(a and b and a["slug"] == b["slug"] and files(snippet_id, a) == files(snippet_id, b))


def _text(node) -> str:
    if isinstance(node, dict):
        return node.get("text", "") + " ".join(_text(c) for c in node.get("content", []))
    return ""


def _blocks(doc: dict) -> list[str]:
    return [json.dumps(b, sort_keys=True) for b in (doc or EMPTY_DOC).get("content", [])]


def block_summary(old: dict | None, new: dict | None) -> dict:
    a, b = _blocks(old and old["content"]), _blocks(new and new["content"])
    items, n = [], dict(added=0, removed=0, changed=0, unchanged=0)

    def add(op, blk):
        n[op] += 1
        j = json.loads(blk)
        items.append(dict(op=op, type=j.get("type"), text=_text(j)[:80]))

    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            n["unchanged"] += i2 - i1
            continue
        k = min(i2 - i1, j2 - j1) if tag == "replace" else 0
        for x in range(k):
            n["changed"] += 1
            j = json.loads(b[j1 + x])
            items.append(dict(op="changed", type=j.get("type"), text=_text(j)[:80]))
        for blk in a[i1 + k:i2]:
            add("removed", blk)
        for blk in b[j1 + k:j2]:
            add("added", blk)
    return dict(**n, blocks=items)


def diff(snippet_id, old: dict | None, new: dict | None) -> dict:
    fo = files(snippet_id, old) if old else {"content.json": "", "meta.yaml": ""}
    fn = files(snippet_id, new) if new else {"content.json": "", "meta.yaml": ""}
    text = "".join("".join(difflib.unified_diff(fo[f].splitlines(True), fn[f].splitlines(True),
                                                f"a/{f}", f"b/{f}")) for f in fo)
    return dict(text=text, summary=block_summary(old, new))
