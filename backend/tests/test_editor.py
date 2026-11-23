"""Styles CRUD/roles, image upload validation and serving, assets at commit."""
import io
import json

import pytest
from PIL import Image

from app.git import repo as git

from .conftest import add_member, signup


def _img(fmt="PNG", size=(4, 3)):
    b = io.BytesIO()
    Image.new("RGB", size, "red").save(b, fmt)
    return b.getvalue()


PNG = _img()


@pytest.fixture
def team(client, outbox):
    signup(client, "owner@x.co", "Acme")
    t = {r: add_member(client, outbox, f"{r}@x.co", r) for r in ("admin", "editor", "viewer")}
    t["owner"] = client
    return t


def test_new_org_gets_builtin_styles(client):
    signup(client, "a@a.co")
    st = client.get("/api/styles").json()
    ids = {s["style_id"] for s in st}
    assert {"Normal", "Title", "Heading1", "Heading2", "Heading3", "Heading4", "Quote", "Code",
            "ListParagraph", "TableGrid"} <= ids
    h1 = next(s for s in st if s["style_id"] == "Heading1")
    assert h1["kind"] == "heading" and h1["level"] == 1 and h1["numbering"] == "decimal" and h1["builtin"]
    # a second org created later is seeded too
    assert client.post("/api/orgs", json={"name": "Second"}).status_code == 201
    assert len(client.get("/api/styles").json()) == len(st)


def test_styles_crud_and_roles(team):
    new = dict(style_id="Callout", name="Callout", kind="paragraph", color="#ff0000", font_size=12, bold=True)
    for r in ("viewer", "editor"):
        assert team[r].get("/api/styles").status_code == 200
        assert team[r].post("/api/styles", json=new).status_code == 403
    a = team["admin"]
    s = a.post("/api/styles", json=new)
    assert s.status_code == 201, s.text
    pk = s.json()["id"]
    assert a.post("/api/styles", json=new).status_code == 409
    for r in ("viewer", "editor"):
        assert team[r].patch(f"/api/styles/{pk}", json={"name": "x"}).status_code == 403
        assert team[r].delete(f"/api/styles/{pk}").status_code == 403
    p = a.patch(f"/api/styles/{pk}", json={"name": "Note", "color": None, "space_after": 4})
    assert p.status_code == 200 and p.json()["name"] == "Note" and p.json()["color"] is None
    assert team["viewer"].get("/api/styles").json()  # everyone reads
    assert a.delete(f"/api/styles/{pk}").status_code == 204
    builtin = next(s["id"] for s in a.get("/api/styles").json() if s["style_id"] == "Normal")
    assert a.delete(f"/api/styles/{builtin}").status_code == 409


@pytest.mark.parametrize("bad", [
    dict(color="red"), dict(color="#12345"), dict(font_family="x;}body{display:none"), dict(font_size=0),
    dict(kind="heading"),  # heading needs a level
    dict(level=2),  # level only for headings
    dict(style_id="bad id"), dict(kind="bogus"),
])
def test_style_validation(team, bad):
    body = {"style_id": "Ok", "name": "Ok", "kind": "paragraph", **bad}
    assert team["admin"].post("/api/styles", json=body).status_code == 422


def test_image_upload_validation(team):
    e = team["editor"]
    up = lambda data, name="a.png", ct="image/png": e.post("/api/images", files={"file": (name, data, ct)})
    r = up(PNG)
    assert r.status_code == 201 and (r.json()["width"], r.json()["height"], r.json()["mime"]) == (4, 3, "image/png")
    assert up(_img("JPEG"), "x.jpg", "image/jpeg").json()["mime"] == "image/jpeg"
    assert up(b"not an image at all", "evil.png").status_code == 415  # extension/content-type lie
    assert up(b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", "a.svg", "image/svg+xml").status_code == 415
    assert up(_img("BMP"), "a.bmp").status_code == 415  # real image but unsupported format
    assert up(PNG[:30]).status_code == 415  # truncated
    assert up(b"\x89PNG" + b"0" * (11 * 1024 * 1024)).status_code == 413
    assert team["viewer"].post("/api/images", files={"file": ("a.png", PNG, "image/png")}).status_code == 403


def test_image_served_authenticated(team, client):
    from .conftest import new_client
    iid = team["editor"].post("/api/images", files={"file": ("a.png", PNG, "image/png")}).json()["id"]
    r = team["viewer"].get(f"/api/images/{iid}")
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert new_client().get(f"/api/images/{iid}").status_code == 401  # anonymous


def test_commit_embeds_assets(team):
    from .conftest import new_client  # noqa: F401
    e = team["editor"]
    org = e.get("/api/orgs/current").json()["id"]
    fo = e.post("/api/folders", json={"name": "F"}).json()["id"]
    sid = e.post(f"/api/folders/{fo}/snippets", json={"title": "Pics"}).json()["id"]
    iid = e.post("/api/images", files={"file": ("a.png", PNG, "image/png")}).json()["id"]
    doc = {"type": "doc", "content": [{"type": "image", "attrs": {"imageId": iid, "alt": "a", "width": None}}]}
    assert e.put(f"/api/snippets/{sid}/draft", json={"content": doc}).status_code == 200
    r = e.post(f"/api/folders/{fo}/commit", json={"message": "add", "snippet_ids": [sid]})
    assert r.status_code == 201, r.text
    repo = git.repo_path(org, fo)
    assert git.show(repo, r.json()["sha"], f"snippets/pics/assets/{iid}.png") == PNG
    assert json.loads(git.show(repo, r.json()["sha"], "snippets/pics/content.json"))["content"][0]["attrs"]["imageId"] == iid
    # unknown image id -> commit refused, nothing written
    bad = {"type": "doc", "content": [{"type": "image", "attrs": {"imageId": "00000000-0000-0000-0000-000000000000"}}]}
    e.put(f"/api/snippets/{sid}/draft", json={"content": bad})
    assert e.post(f"/api/folders/{fo}/commit", json={"message": "x", "snippet_ids": [sid]}).status_code == 422
    # slug rename moves assets along
    e.put(f"/api/snippets/{sid}/draft", json={"content": doc, "slug": "gallery"})
    r2 = e.post(f"/api/folders/{fo}/commit", json={"message": "rename", "snippet_ids": [sid]})
    assert r2.status_code == 201
    assert git.show(repo, r2.json()["sha"], f"snippets/gallery/assets/{iid}.png") == PNG
