"""Git-backed folders/snippets: commit, history, diff, restore, tags, conflicts, repo hygiene."""
import subprocess

import pytest

from app.git import repo as git

from .conftest import add_member, signup


def doc(*texts):
    return {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": t}]} for t in texts]}


@pytest.fixture
def ws(client, outbox):
    signup(client, "alice@a.co", "Org A", name="Alice A")
    org = client.get("/api/orgs/current").json()["id"]
    f = client.post("/api/folders", json={"name": "Docs"}).json()
    return dict(c=client, org=org, folder=f["id"], outbox=outbox)


def mk(c, folder, title="Intro"):
    return c.post(f"/api/folders/{folder}/snippets", json={"title": title}).json()["id"]


def save(c, sid, *texts, **kw):
    r = c.put(f"/api/snippets/{sid}/draft", json={"content": doc(*texts), **kw})
    assert r.status_code == 200, r.text
    return r.json()


def commit(c, folder, ids, msg="m"):
    return c.post(f"/api/folders/{folder}/commit", json={"message": msg, "snippet_ids": ids})


def test_folder_tree_move_rename_delete(ws):
    c = ws["c"]
    a = c.post("/api/folders", json={"name": "A", "parent_id": ws["folder"]}).json()
    b = c.post("/api/folders", json={"name": "B", "parent_id": a["id"]}).json()
    assert c.post("/api/folders", json={"name": "A", "parent_id": ws["folder"]}).status_code == 409
    assert c.patch(f"/api/folders/{ws['folder']}", json={"parent_id": b["id"]}).status_code == 400  # cycle
    assert c.patch(f"/api/folders/{b['id']}", json={"name": "B2", "parent_id": None}).json()["parent_id"] is None
    repos = [git.repo_path(ws["org"], f) for f in (ws["folder"], a["id"], b["id"])]
    assert all(p.is_dir() and p.is_relative_to(git.ROOT) for p in repos)
    assert c.delete(f"/api/folders/{ws['folder']}").status_code == 204  # also removes child A
    assert [p.exists() for p in repos] == [False, False, True]
    assert [f["name"] for f in c.get("/api/folders").json()] == ["B2"]


def test_commit_edit_commit_history_diff_restore_tag(ws):
    c, fo = ws["c"], ws["folder"]
    sid = mk(c, fo)
    save(c, sid, "one")
    sha1 = commit(c, fo, [sid], "first").json()["sha"]
    repo = git.repo_path(ws["org"], fo)
    assert git.head(repo) == sha1
    assert git._git(repo, "ls-tree", "-r", "--name-only", sha1).decode().split() == \
        ["snippets/intro/content.json", "snippets/intro/meta.yaml"]
    # author is the user's name/email; exactly one commit
    out = git._git(repo, "log", "--format=%an <%ae>|%s").decode().strip().splitlines()
    assert out == ["Alice A <alice@a.co>|first"]
    assert c.get(f"/api/snippets/{sid}/draft").json()["exists"] is False
    save(c, sid, "one", "two")
    ch = c.get(f"/api/folders/{fo}/changes").json()
    assert len(ch) == 1 and ch[0]["status"] == "modified" and "+          \"text\": \"two\"," in ch[0]["text"]
    assert ch[0]["summary"]["added"] == 1 and ch[0]["summary"]["unchanged"] == 1
    sha2 = commit(c, fo, [sid], "second").json()["sha"]
    assert c.get(f"/api/folders/{fo}/changes").json() == []
    hist = c.get(f"/api/snippets/{sid}/history").json()
    assert [h["sha"] for h in hist] == [sha2, sha1] and hist[0]["message"] == "second" and hist[0]["snippet_ids"] == [sid]
    assert len(c.get(f"/api/folders/{fo}/history").json()) == 2
    assert c.get(f"/api/snippets/{sid}?version={sha1}").json()["content"] == doc("one")
    assert c.get(f"/api/snippets/{sid}").json()["content"] == doc("one", "two")
    d = c.get(f"/api/snippets/{sid}/diff", params={"from": sha1, "to": sha2}).json()
    assert d["summary"]["added"] == 1 and d["summary"]["blocks"][0]["text"] == "two"
    # tags
    assert c.post(f"/api/folders/{fo}/tags", json={"name": "v1.0", "sha": sha1}).status_code == 201
    assert c.post(f"/api/folders/{fo}/tags", json={"name": "v1.0"}).status_code == 409
    assert c.get(f"/api/folders/{fo}/tags").json() == [{"name": "v1.0", "sha": sha1}]
    assert c.get(f"/api/snippets/{sid}?version=v1.0").json()["sha"] == sha1
    assert c.get(f"/api/snippets/{sid}/history").json()[1]["tags"] == ["v1.0"]
    # restore into a draft, then commit it
    r = c.post(f"/api/snippets/{sid}/restore", json={"version": "v1.0"}).json()
    assert r["content"] == doc("one") and r["dirty"] and not r["conflict"]
    sha3 = commit(c, fo, [sid], "revert").json()["sha"]
    assert c.get(f"/api/snippets/{sid}").json()["content"] == doc("one")
    assert len(c.get(f"/api/snippets/{sid}/history").json()) == 3 and sha3 != sha2
    assert c.delete(f"/api/folders/{fo}/tags/v1.0").status_code == 204
    assert c.delete(f"/api/folders/{fo}/tags/v1.0").status_code == 404


def test_rename_is_git_move_and_uuid_stable(ws):
    c, fo = ws["c"], ws["folder"]
    sid = mk(c, fo)
    save(c, sid, "x")
    sha1 = commit(c, fo, [sid]).json()["sha"]
    save(c, sid, "x", slug="welcome", title="Welcome")
    sha2 = commit(c, fo, [sid]).json()["sha"]
    repo = git.repo_path(ws["org"], fo)
    assert git._git(repo, "ls-tree", "-r", "--name-only", sha2).decode().split() == \
        ["snippets/welcome/content.json", "snippets/welcome/meta.yaml"]
    assert c.get(f"/api/snippets/{sid}?version={sha1}").json()["slug"] == "intro"  # old path still resolvable
    assert [h["sha"] for h in c.get(f"/api/snippets/{sid}/history").json()] == [sha2, sha1]
    assert c.get(f"/api/folders/{fo}/snippets").json()[0]["slug"] == "welcome"


def test_slug_collision_and_validation(ws):
    c, fo = ws["c"], ws["folder"]
    a, b = mk(c, fo, "Intro"), mk(c, fo, "Intro")
    assert {s["slug"] for s in c.get(f"/api/folders/{fo}/snippets").json()} == {"intro", "intro-2"}
    assert c.post(f"/api/folders/{fo}/snippets", json={"title": "x", "slug": "../etc"}).status_code == 422
    save(c, b, "b", slug="intro")
    assert commit(c, fo, [b]).status_code == 409
    assert commit(c, fo, [a, b]).status_code == 409  # nothing was committed
    assert git.head(git.repo_path(ws["org"], fo)) is None
    assert c.get(f"/api/folders/{fo}/snippets?q=INTRO-2").json()[0]["id"] == b


def test_commit_only_picked_and_nothing_to_commit(ws):
    c, fo = ws["c"], ws["folder"]
    a, b = mk(c, fo, "A"), mk(c, fo, "B")
    save(c, a, "a"); save(c, b, "b")
    commit(c, fo, [a])
    assert [x["snippet_id"] for x in c.get(f"/api/folders/{fo}/changes").json()] == [b]
    assert commit(c, fo, [a]).status_code == 400
    c.delete(f"/api/snippets/{b}/draft")
    assert c.get(f"/api/snippets/{b}/draft").json()["exists"] is False


def rewind(owner_engine, sid, sha):
    """Round 16: drafts are shared, so a stale draft can only come from old data: put its base back by hand."""
    with owner_engine.begin() as c:
        c.execute(__import__("sqlalchemy").text("update drafts set base_sha=:s where snippet_id=:i"), {"s": sha, "i": sid})


def test_conflict_three_way_and_resolve(ws, owner_engine):
    alice, fo = ws["c"], ws["folder"]
    bob = add_member(alice, ws["outbox"], "bob@a.co", "editor")
    sid = mk(alice, fo)
    save(alice, sid, "base")
    sha0 = commit(alice, fo, [sid]).json()["sha"]
    save(alice, sid, "alice edit")
    sha_a = commit(alice, fo, [sid], "alice").json()["sha"]
    alice.post(f"/api/locks/snippets/{sid}/release")
    save(bob, sid, "bob edit")                      # a draft that started at base (rewound by hand)
    rewind(owner_engine, sid, sha0)
    r = commit(bob, fo, [sid], "bob")
    assert r.status_code == 409
    cf = r.json()["detail"]["conflicts"][0]
    assert cf["base"]["content"] == doc("base") and cf["theirs"]["content"] == doc("alice edit") \
        and cf["mine"]["content"] == doc("bob edit")
    ch = bob.get(f"/api/folders/{fo}/changes").json()
    assert ch[0]["conflict"] and ch[0]["three_way"]["head_sha"] == sha_a
    assert bob.get(f"/api/snippets/{sid}/draft").json()["conflict"] is True
    res = bob.post(f"/api/snippets/{sid}/resolve", json={"content": doc("merged")})
    assert res.status_code == 200 and not res.json()["conflict"]
    assert commit(bob, fo, [sid], "bob merged").status_code == 201
    assert alice.get(f"/api/snippets/{sid}").json()["content"] == doc("merged")


def test_untouched_stale_draft_follows_head(ws, owner_engine):
    alice, fo = ws["c"], ws["folder"]
    bob = add_member(alice, ws["outbox"], "bob@a.co", "editor")
    sid = mk(alice, fo)
    save(alice, sid, "v1"); sha0 = commit(alice, fo, [sid]).json()["sha"]
    save(alice, sid, "v2"); commit(alice, fo, [sid])
    alice.post(f"/api/locks/snippets/{sid}/release")
    save(bob, sid, "v1")  # same as its (old, rewound by hand) base -> not dirty
    rewind(owner_engine, sid, sha0)
    d = bob.get(f"/api/snippets/{sid}/draft").json()
    assert d["content"] == doc("v2") and not d["conflict"]
    assert bob.get(f"/api/folders/{fo}/changes").json() == []


def test_bad_revs_and_message_trailer_forgery(ws):
    c, fo = ws["c"], ws["folder"]
    sid = mk(c, fo)
    save(c, sid, "x")
    forged = "\n".join(["hi", "Snippet-Id: 00000000-0000-0000-0000-000000000000"])
    sha = commit(c, fo, [sid], forged).json()["sha"]
    assert c.get(f"/api/folders/{fo}/history").json()[0]["snippet_ids"] == [sid]
    for rev in ("--all", "HEAD", "zz", "../x", "0" * 40):
        assert c.get(f"/api/snippets/{sid}?version={rev}").status_code == 404, rev
    assert c.post(f"/api/folders/{fo}/tags", json={"name": "-x"}).status_code == 422
    assert c.get(f"/api/snippets/{sid}?version={sha[:10]}").status_code == 200


def test_delete_org_removes_repos(ws):
    c = ws["c"]
    assert git.repo_path(ws["org"], ws["folder"]).exists()
    assert c.delete("/api/orgs/current").status_code == 204
    assert not (git.ROOT / ws["org"]).exists()


def test_repo_path_confined():
    with pytest.raises(ValueError):
        git.repo_path("../..", "x")
    with pytest.raises(AssertionError):
        git.remove(git.ROOT)
    assert subprocess.run(["git", "--version"]).returncode == 0
