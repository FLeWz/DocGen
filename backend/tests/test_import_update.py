"""Round 12: same-title detection when making snippets from an import, 'update existing' = draft on top of HEAD (never a
commit), draft protection, and bulk update from a (newer) import by remembered source / title / fuzzy title."""
import pytest

from .test_git import commit
from .test_import import folder, imp, imported, team  # noqa: F401  (fixtures)


def blocks(c, iid):
    return c.get(f"/api/imports/{iid}").json()["content"]["content"]


def sel(c, iid, fo, title, **kw):
    return c.post(f"/api/imports/{iid}/selection-snippet", json={"folder_id": fo, "title": title, "start": 0, "end": 2, **kw})


def head(c, sid):
    return c.get(f"/api/snippets/{sid}").json()["sha"]


def newer(c, iid, edit_title=None):
    """A second upload of the same manual: one body paragraph changed (and optionally a heading renamed)."""
    j = c.get(f"/api/imports/{iid}").json()
    bs = j["content"]["content"]
    k = next(k for k, b in enumerate(bs) if b["type"] == "heading")
    p = next(b for b in bs[k + 1:] if b["type"] == "paragraph" and b.get("content"))
    p["content"][0]["text"] += " CHANGED"
    p["attrs"].pop("orig", None)
    r = imp(c)
    n = r.json()["id"]
    assert c.put(f"/api/imports/{n}", json={"content": j["content"]}).status_code == 200
    if edit_title:
        old, new = edit_title
        j2 = c.get(f"/api/imports/{n}").json()["content"]
        h = next(b for b in j2["content"] if b["type"] == "heading" and b["content"][0]["text"] == old)
        h["content"][0]["text"] = new
        h["attrs"].pop("orig", None)
        assert c.put(f"/api/imports/{n}", json={"content": j2}).status_code == 200
    return n


def test_selection_same_title_conflict_payload_and_actions(imported):
    team, iid = imported
    ed = team["editor"]
    fo = folder(ed, "Manuals")
    sid = sel(ed, iid, fo, "Intro bit").json()["id"]
    assert commit(ed, fo, [sid]).status_code == 201
    h0 = head(ed, sid)
    r = sel(ed, iid, fo, "  intro BIT ")  # case-insensitive, trimmed
    assert r.status_code == 409
    d = r.json()["detail"]
    c = d["conflicts"][0]
    assert d["error"] == "name_exists" and c["snippet_id"] == sid and c["slug"] == "intro-bit" and c["title"] == "Intro bit"
    assert c["head_sha"] == h0 and c["last_commit_at"] and c["last_commit_author"] and c["folder_name"] == "Manuals" and c["has_draft"] is False
    assert sel(ed, iid, fo, "Intro-Bit").status_code == 409  # same slug
    # suffix: a separate snippet, title and slug numbered
    r = sel(ed, iid, fo, "Intro bit", on_conflict="create_new_with_suffix").json()
    assert (r["title"], r["slug"], r["action"]) == ("Intro bit (2)", "intro-bit-2", "created") and r["id"] != sid
    # skip: nothing written
    assert sel(ed, iid, fo, "Intro bit", on_conflict="skip").json()["action"] == "skipped"
    assert len(ed.get(f"/api/folders/{fo}/snippets").json()) == 2
    assert sel(ed, iid, fo, "x", on_conflict="nope").status_code == 422


def test_update_existing_writes_draft_on_head_and_never_commits(imported):
    team, iid = imported
    ed = team["editor"]
    fo = folder(ed)
    sid = sel(ed, iid, fo, "Intro bit").json()["id"]
    commit(ed, fo, [sid])
    h0, n0 = head(ed, sid), len(ed.get(f"/api/folders/{fo}/log").json()) if ed.get(f"/api/folders/{fo}/log").status_code == 200 else None
    r = ed.post(f"/api/imports/{iid}/selection-snippet", json={"folder_id": fo, "title": "intro bit", "start": 2, "end": 5, "on_conflict": "update_existing"})
    assert r.status_code == 201, r.text
    o = r.json()
    assert (o["id"], o["slug"], o["title"], o["action"], o["base_sha"], o["head_sha"]) == (sid, "intro-bit", "Intro bit", "updated", h0, h0)
    d = ed.get(f"/api/snippets/{sid}/draft").json()
    assert d["base_sha"] == h0 and d["head_sha"] == h0 and not d["conflict"] and d["dirty"]
    assert [b["type"] for b in d["content"]["content"]] == ["paragraph", "paragraph", "image"]
    assert "Callout" in d["styles"] and d["title"] == "Intro bit" and head(ed, sid) == h0  # not committed
    assert n0 is None or len(ed.get(f"/api/folders/{fo}/log").json()) == n0
    # the user's unsaved draft is protected; overwrite_draft replaces it
    body = {"folder_id": fo, "title": "Intro bit", "start": 0, "end": 3, "on_conflict": "update_existing"}
    assert ed.get(f"/api/folders/{fo}/snippets").json()[0]["has_draft"]
    c = sel(ed, iid, fo, "Intro bit").json()["detail"]["conflicts"][0]
    assert c["has_draft"] is True
    r = ed.post(f"/api/imports/{iid}/selection-snippet", json=body)
    assert r.status_code == 409 and r.json()["detail"]["error"] == "draft_exists" and r.json()["detail"]["snippets"][0]["snippet_id"] == sid
    assert len(ed.get(f"/api/snippets/{sid}/draft").json()["content"]["content"]) == 3  # untouched
    assert ed.post(f"/api/imports/{iid}/selection-snippet", json={**body, "overwrite_draft": True}).status_code == 201
    assert len(ed.get(f"/api/snippets/{sid}/draft").json()["content"]["content"]) == 3
    # committing it is the version bump
    assert commit(ed, fo, [sid]).status_code == 201 and head(ed, sid) != h0


def test_split_conflicts_per_row(imported):
    team, iid = imported
    ed = team["editor"]
    fo = folder(ed)
    first = ed.post(f"/api/imports/{iid}/split", json={"folder_id": fo, "level": 1}).json()
    ids = {s["title"]: s["id"] for s in first}
    commit(ed, fo, list(ids.values()))
    again = {"folder_id": fo, "level": 1}
    r = ed.post(f"/api/imports/{iid}/split", json=again)
    assert r.status_code == 409 and r.json()["detail"]["error"] == "name_exists"
    cs = r.json()["detail"]["conflicts"]
    assert [c["title"] for c in cs] == list(ids) and all("key" in c and c["new_title"] == c["title"] for c in cs)
    keys = [c["key"] for c in cs]
    pick = dict(zip(keys, ["update_existing", "create_new_with_suffix", "skip", "update_existing"]))
    r = ed.post(f"/api/imports/{iid}/split", json={**again, "on_conflict": pick})
    assert r.status_code == 201, r.text
    out = r.json()
    assert [(o["action"], o["title"]) for o in out] == [("updated", "Introduction"), ("created", "Revision history (2)"), ("updated", "Opaque things")]
    assert {o["id"] for o in out if o["action"] == "updated"} == {ids["Introduction"], ids["Opaque things"]}
    assert len(ed.get(f"/api/folders/{fo}/snippets").json()) == 5
    # the updated snippets hold an edited draft now: same request without overwrite_draft is refused, nothing else written
    for sid in (ids["Introduction"], ids["Opaque things"]):
        d = ed.get(f"/api/snippets/{sid}/draft").json()
        d["content"]["content"][0]["content"][0]["text"] += " mine"
        assert ed.put(f"/api/snippets/{sid}/draft", json={"content": d["content"]}).status_code == 200
    r = ed.post(f"/api/imports/{iid}/split", json={**again, "on_conflict": pick})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "draft_exists" and len(r.json()["detail"]["snippets"]) == 2
    pick2 = {k: "update_existing" for k in keys}
    r = ed.post(f"/api/imports/{iid}/split", json={**again, "on_conflict": pick2})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "draft_exists" and len(r.json()["detail"]["snippets"]) == 2
    assert len(ed.get(f"/api/folders/{fo}/snippets").json()) == 5


def test_conflicts_preview(imported):
    team, iid = imported
    ed = team["editor"]
    fo = folder(ed)
    sid = sel(ed, iid, fo, "Alpha").json()["id"]
    r = ed.post(f"/api/imports/{iid}/conflicts", json={"folder_id": fo, "titles": ["alpha ", "Beta"]})
    assert r.status_code == 200 and r.json()[0]["snippet_id"] == sid and r.json()[1] is None
    assert team["viewer"].post(f"/api/imports/{iid}/conflicts", json={"folder_id": fo, "titles": ["x"]}).status_code == 200


def heads(c, iid):
    return {h["title"]: h["index"] for h in c.get(f"/api/imports/{iid}/headings").json()}


def test_bulk_update_matches_by_source_title_and_fuzzy(imported):
    team, iid = imported
    ed = team["editor"]
    fo = folder(ed)
    h0 = heads(ed, iid)  # "Merged cells" is made under another title: only the remembered source ties it to the heading
    first = ed.post(f"/api/imports/{iid}/split", json={"folder_id": fo, "headings": [h0[t] for t in ("Introduction", "Revision history", "Merged cells", "Opaque things")],
                                                     "titles": {h0["Merged cells"]: "Tables with spans"}}).json()
    ids = {s["title"]: s["id"] for s in first}
    ids["Merged cells"] = ids.pop("Tables with spans")
    assert commit(ed, fo, list(ids.values())).status_code == 201
    heads0 = {k: head(ed, v) for k, v in ids.items()}
    n = newer(ed, iid, edit_title=("Revision history", "Revision histories"))
    hs = heads(ed, n)
    hs = {t: hs[t] for t in ("Introduction", "Revision histories", "Merged cells", "Opaque things")}
    body = {"folder_id": fo, "headings": sorted(hs.values())}
    plan = {r["title"]: r for r in ed.post(f"/api/imports/{n}/update-plan", json=body).json()}
    assert plan["Introduction"]["match"]["how"] == "title" and plan["Introduction"]["target"] == ids["Introduction"]
    assert plan["Introduction"]["changes"]["changed"] == 1 and not plan["Introduction"]["changes"]["same"]
    assert plan["Merged cells"]["match"]["how"] == "source" and plan["Merged cells"]["target"] == ids["Merged cells"]
    f = plan["Revision histories"]["match"]
    assert f["how"] == "fuzzy" and 0.7 <= f["score"] < 1 and f["snippet_id"] == ids["Revision history"]
    assert plan["Opaque things"]["changes"]["same"] is True  # unchanged section: marked, to be skipped by default
    # user override: heading -> new / skip / other snippet
    over = ed.post(f"/api/imports/{n}/update-plan", json={**body, "targets": {hs["Introduction"]: "new", hs["Merged cells"]: "skip"}}).json()
    assert {r["title"]: (r["target"], r["changes"]) for r in over}["Introduction"] == ("new", None)
    assert ed.post(f"/api/imports/{n}/update-plan", json={**body, "targets": {hs["Introduction"]: ids["Merged cells"]}}).json()[0]["target"] == ids["Merged cells"]
    # apply: Introduction + Revision histories updated, Merged cells skipped (so its blocks merge into the previous snippet's run)
    items = [{"key": hs["Introduction"], "action": "update", "snippet_id": ids["Introduction"]},
             {"key": hs["Revision histories"], "action": "update", "snippet_id": ids["Revision history"]},
             {"key": hs["Opaque things"], "action": "create"}]
    r = ed.post(f"/api/imports/{n}/update-apply", json={"folder_id": fo, "items": items})
    assert r.status_code == 200, r.text
    out = r.json()
    assert [o["action"] for o in out] == ["updated", "updated", "created"] and out[2]["title"] == "Opaque things (2)"
    for t in ("Introduction", "Revision history"):
        dd = ed.get(f"/api/snippets/{ids[t]}/draft").json()
        assert dd["base_sha"] == heads0[t] == head(ed, ids[t]) and dd["dirty"] and dd["title"] == t
    assert head(ed, ids["Merged cells"]) == heads0["Merged cells"]
    # committing the changed drafts bumps the versions
    assert commit(ed, fo, [ids["Introduction"], ids["Revision history"]]).status_code == 201
    assert head(ed, ids["Introduction"]) != heads0["Introduction"]
    # drafts are protected on a second apply; sanity errors
    items2 = items[:1] + [{"key": hs["Merged cells"], "action": "update", "snippet_id": ids["Introduction"]}]
    assert ed.post(f"/api/imports/{n}/update-apply", json={"folder_id": fo, "items": items2}).status_code == 422  # one snippet, two headings
    assert ed.post(f"/api/imports/{n}/update-apply", json={"folder_id": fo, "items": [{"key": hs["Introduction"], "action": "skip"}]}).status_code == 422
    assert ed.post(f"/api/imports/{n}/update-plan", json={"folder_id": fo, "headings": [1]}).status_code == 422  # block 1 is no heading
    # "Update from import…" on one snippet: an explicit end keeps the run to that heading instead of the end of the document
    one = ed.post(f"/api/imports/{n}/update-apply", json={"folder_id": fo, "items": [{"key": hs["Introduction"], "action": "update", "snippet_id": ids["Introduction"], "end": hs["Revision histories"]}], "overwrite_draft": True}).json()
    assert [o["action"] for o in one] == ["updated"]
    assert len(ed.get(f"/api/snippets/{ids['Introduction']}/draft").json()["content"]["content"]) == hs["Revision histories"] - hs["Introduction"]


def test_bulk_update_draft_protection_and_roles(imported):
    team, iid = imported
    ed = team["editor"]
    fo = folder(ed)
    ids = {s["title"]: s["id"] for s in ed.post(f"/api/imports/{iid}/split", json={"folder_id": fo, "level": 1}).json()}
    commit(ed, fo, list(ids.values()))
    hs = heads(ed, iid)
    items = [{"key": hs[t], "action": "update", "snippet_id": sid} for t, sid in ids.items()]
    sid = ids["Introduction"]
    # same content as HEAD: the draft is not dirty, so a repeated apply loses nothing
    for _ in range(2):
        r = ed.post(f"/api/imports/{iid}/update-apply", json={"folder_id": fo, "items": items})
        assert r.status_code == 200 and [o["action"] for o in r.json()] == ["updated"] * 4
    d = ed.get(f"/api/snippets/{sid}/draft").json()
    assert not d["dirty"]
    d["content"]["content"][0]["content"][0]["text"] = "Mine"
    ed.put(f"/api/snippets/{sid}/draft", json={"content": d["content"]})
    r = ed.post(f"/api/imports/{iid}/update-apply", json={"folder_id": fo, "items": items})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "draft_exists"
    assert ed.get(f"/api/snippets/{sid}/draft").json()["content"]["content"][0]["content"][0]["text"] == "Mine"
    assert ed.post(f"/api/imports/{iid}/update-apply", json={"folder_id": fo, "items": items, "overwrite_draft": True}).status_code == 200
    assert ed.get(f"/api/snippets/{sid}/draft").json()["content"]["content"][0]["content"][0]["text"] == "Introduction"
    v = team["viewer"]
    assert v.post(f"/api/imports/{iid}/update-apply", json={"folder_id": fo, "items": items}).status_code == 403
    assert v.post(f"/api/imports/{iid}/update-plan", json={"folder_id": fo, "headings": [hs["Introduction"]]}).status_code == 200


def test_update_from_other_org_is_404(imported, client):
    team, iid = imported
    ed = team["editor"]
    fo = folder(ed)
    sid = ed.post(f"/api/imports/{iid}/split", json={"folder_id": fo, "level": 1}).json()[0]["id"]
    from .conftest import new_client, signup
    other = new_client()
    signup(other, "b@y.co", "Other")
    bf = folder(other)
    item = {"key": heads(ed, iid)["Introduction"], "action": "update", "snippet_id": sid}
    assert other.post(f"/api/imports/{iid}/update-apply", json={"folder_id": bf, "items": [item]}).status_code == 404
    assert other.post(f"/api/imports/{iid}/update-plan", json={"folder_id": bf, "headings": [0]}).status_code == 404
    assert other.post(f"/api/imports/{iid}/conflicts", json={"folder_id": bf, "titles": ["x"]}).status_code == 404
    b_imp = imp(other).json()["id"]
    assert other.post(f"/api/imports/{b_imp}/update-apply", json={"folder_id": bf, "items": [item]}).status_code == 404  # A's snippet
    assert other.post(f"/api/imports/{b_imp}/update-apply", json={"folder_id": fo, "items": [item]}).status_code == 404  # A's folder

