"""Round 16: edit locks, request/grant/deny/timeout, cooldown, notifications, shared drafts, commit trailers."""
import threading
import time
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app import locks

from .conftest import add_member, new_client, signup
from .test_documents import put as put_doc, ref
from .test_git import commit, doc, mk, save


@pytest.fixture
def ws(client, outbox):
    signup(client, "alice@a.co", "Org A", name="Alice A")
    fo = client.post("/api/folders", json={"name": "Docs"}).json()["id"]
    sid = mk(client, fo)
    save(client, sid, "v1")
    commit(client, fo, [sid])
    client.post(f"/api/locks/snippets/{sid}/release")
    bob = add_member(client, outbox, "bob@a.co", "editor")
    carl = add_member(client, outbox, "carl@a.co", "editor")
    viewer = add_member(client, outbox, "v@a.co", "viewer")
    d = client.post("/api/documents", json={"folder_id": fo, "title": "Manual"}).json()["id"]
    client.post(f"/api/locks/documents/{d}/release")
    return dict(a=client, b=bob, c=carl, v=viewer, fo=fo, sid=sid, doc=d, outbox=outbox)


def L(c, kind, id, op="", method="post"):
    return getattr(c, method)(f"/api/locks/{kind}/{id}" + (f"/{op}" if op else ""))


def test_acquire_heartbeat_expiry_release(ws, monkeypatch):
    a, b, sid = ws["a"], ws["b"], ws["sid"]
    s = L(a, "snippets", sid).json()
    assert s["held"] and s["holder"]["name"] == "Alice A"
    s = L(b, "snippets", sid).json()
    assert not s["held"] and s["holder"]["name"] == "Alice A" and s["holder"]["since"]
    assert L(a, "snippets", sid, "heartbeat").json()["held"]
    assert not L(b, "snippets", sid, "heartbeat").json()["held"]  # non-holder heartbeat only polls
    assert [x["holder"]["name"] for x in a.get("/api/locks").json()] == ["Alice A"]
    monkeypatch.setenv("LOCK_HEARTBEAT_TTL_S", "0.2")  # holder vanished: the lock is simply free
    time.sleep(0.3)
    assert a.get("/api/locks").json() == []
    assert L(b, "snippets", sid).json()["held"]
    r = a.put(f"/api/snippets/{sid}/draft", json={"content": doc("x")})
    assert r.status_code == 409 and r.json()["error"] == "locked" and r.json()["holder"]["id"]
    monkeypatch.delenv("LOCK_HEARTBEAT_TTL_S")
    assert L(b, "snippets", sid, "release").status_code == 204
    assert L(a, "snippets", sid).json()["held"]
    a.post("/api/auth/logout")  # logout releases
    assert L(b, "snippets", sid).json()["held"]


def test_roles_and_takeover(ws):
    a, b, v, sid = ws["a"], ws["b"], ws["v"], ws["sid"]
    assert L(v, "snippets", sid).status_code == 403  # viewers can't lock
    assert v.put(f"/api/snippets/{sid}/draft", json={"content": doc("x")}).status_code == 403
    assert L(v, "snippets", sid, method="get").status_code == 200  # but they can see who edits
    L(b, "snippets", sid)
    assert L(ws["c"], "snippets", sid, "takeover").status_code == 403  # editors can't take over
    s = L(a, "snippets", sid, "takeover").json()  # owner (admin rank) can
    assert s["held"] and s["holder"]["name"] == "Alice A"
    assert [n["kind"] for n in b.get("/api/notifications").json()["items"]] == ["lock_taken"]
    assert b.put(f"/api/snippets/{sid}/draft", json={"content": doc("x")}).status_code == 409
    assert L(a, "bogus", sid).status_code == 404


def test_write_routes_require_the_lock(ws):
    a, b, sid, d, fo = ws["a"], ws["b"], ws["sid"], ws["doc"], ws["fo"]
    L(a, "snippets", sid), L(a, "documents", d)
    body = {"content": doc("z")}
    for r in (b.put(f"/api/snippets/{sid}/draft", json=body), b.delete(f"/api/snippets/{sid}/draft"),
              b.post(f"/api/snippets/{sid}/resolve", json=body),
              b.post(f"/api/snippets/{sid}/restore", json={"version": a.get(f"/api/snippets/{sid}").json()["sha"]}),
              b.delete(f"/api/snippets/{sid}"),
              b.put(f"/api/documents/{d}/draft", json={"blocks": []}), b.delete(f"/api/documents/{d}/draft"),
              b.delete(f"/api/documents/{d}")):
        assert r.status_code == 409 and r.json()["error"] == "locked", r.text
    a.put(f"/api/snippets/{sid}/draft", json=body)
    r = b.post(f"/api/folders/{fo}/commit", json={"message": "x", "snippet_ids": [sid]})
    assert r.status_code == 409 and r.json()["error"] == "locked"
    assert a.post(f"/api/folders/{fo}/commit", json={"message": "x", "snippet_ids": [sid]}).status_code == 201


def test_request_grant_flow(ws):
    a, b, sid, outbox = ws["a"], ws["b"], ws["sid"], ws["outbox"]
    L(a, "snippets", sid)
    a.put(f"/api/snippets/{sid}/draft", json={"content": doc("alice wrote")})
    n0 = len(outbox)
    s = L(b, "snippets", sid, "request").json()
    assert s["pending"]["requester"]["name"] == "N" and s["my_request"]["status"] == "pending" and not s["held"]
    time.sleep(0.3)
    assert len(outbox) == n0 + 1 and outbox[-1][0] == "alice@a.co" and f"/snippets/{sid}" in outbox[-1][2]
    hb = L(a, "snippets", sid, "heartbeat").json()  # the holder learns about it with the next heartbeat
    inc = hb["incoming"]
    assert inc["status"] == "pending" and inc["requester"]["name"] == "N" and hb["server_time"] and inc["expires_at"]
    assert a.get("/api/notifications").json()["unread"] == 1
    assert b.post(f"/api/lock-requests/{inc['id']}/grant").status_code == 404  # only the holder answers
    r = a.post(f"/api/lock-requests/{inc['id']}/grant").json()
    assert not r["held"] and r["holder"]["name"] == "N"
    assert L(b, "snippets", sid, "heartbeat").json()["held"]
    assert b.get(f"/api/snippets/{sid}/draft").json()["content"] == doc("alice wrote")  # the draft carries over
    assert b.put(f"/api/snippets/{sid}/draft", json={"content": doc("bob continues")}).status_code == 200
    assert a.put(f"/api/snippets/{sid}/draft", json={"content": doc("late")}).status_code == 409
    assert [n["kind"] for n in b.get("/api/notifications").json()["items"]] == ["lock_granted"]
    assert a.post(f"/api/lock-requests/{inc['id']}/grant").status_code == 409  # already answered
    b.post("/api/notifications/read")
    assert b.get("/api/notifications").json()["unread"] == 0


def test_deny_cooldown_and_single_pending(ws, monkeypatch):
    a, b, c, sid = ws["a"], ws["b"], ws["c"], ws["sid"]
    L(a, "snippets", sid)
    L(b, "snippets", sid, "request")
    r = L(c, "snippets", sid, "request")  # one pending request at a time: others just wait
    assert r.status_code == 409 and r.json()["detail"]["error"] == "request_pending" and r.json()["detail"]["requester"]["name"] == "N"
    rid = L(a, "snippets", sid, "heartbeat").json()["incoming"]["id"]
    assert a.post(f"/api/lock-requests/{rid}/deny").json()["held"]
    assert a.post(f"/api/lock-requests/{rid}/deny").status_code == 409  # already answered
    s = L(b, "snippets", sid, "heartbeat").json()
    assert s["my_request"]["status"] == "denied" and s["cooldown_until"]
    assert [n["kind"] for n in b.get("/api/notifications").json()["items"]] == ["lock_denied"]
    r = L(b, "snippets", sid, "request")  # the cooldown starts at creation, whatever the outcome
    assert r.status_code == 429 and 0 < r.json()["detail"]["retry_after"] <= 1800 and r.headers["retry-after"]
    assert r.json()["detail"]["retry_at"]
    monkeypatch.setenv("LOCK_REQUEST_COOLDOWN_S", "0.2")
    time.sleep(0.3)
    assert L(b, "snippets", sid, "request").status_code == 200
    assert c.post("/api/lock-requests/00000000-0000-0000-0000-000000000000/deny").status_code == 404


def test_timeout_transfers_lock_and_sweeper(ws, monkeypatch):
    a, b, c, sid = ws["a"], ws["b"], ws["c"], ws["sid"]
    monkeypatch.setenv("LOCK_REQUEST_TIMEOUT_S", "0.3")
    L(a, "snippets", sid)
    a.put(f"/api/snippets/{sid}/draft", json={"content": doc("alice's last")})
    L(b, "snippets", sid, "request")
    assert not L(b, "snippets", sid, "heartbeat").json()["held"]
    time.sleep(0.4)
    assert locks.sweep_all() == 1  # background pass: nobody's tab needed
    s = L(b, "snippets", sid, "heartbeat").json()
    assert s["held"] and s["my_request"]["status"] == "timed_out"
    assert a.put(f"/api/snippets/{sid}/draft", json={"content": doc("late")}).status_code == 409
    assert b.get(f"/api/snippets/{sid}/draft").json()["content"] == doc("alice's last")  # her edits are in the draft
    assert {n["kind"] for n in a.get("/api/notifications").json()["items"]} == {"lock_request", "lock_timeout"}
    assert [n["kind"] for n in b.get("/api/notifications").json()["items"]] == ["lock_timeout"]
    L(b, "documents", ws["doc"])  # heartbeat path: any poll resolves it too
    L(c, "documents", ws["doc"], "request")
    time.sleep(0.4)
    assert L(c, "documents", ws["doc"], "heartbeat").json()["held"]
    assert locks.sweep_all() == 0


def test_closing_the_editor_hands_over_to_the_waiting_requester(ws):
    a, b, sid = ws["a"], ws["b"], ws["sid"]
    L(a, "snippets", sid)
    L(b, "snippets", sid, "request")
    L(a, "snippets", sid, "release")
    assert L(b, "snippets", sid, "heartbeat").json()["held"]


def test_trailers_contributors_and_revision_author(ws):
    a, b, sid, fo = ws["a"], ws["b"], ws["sid"], ws["fo"]
    L(a, "snippets", sid)
    a.put(f"/api/snippets/{sid}/draft", json={"content": doc("alice part")})
    L(b, "snippets", sid, "request")
    rid = L(a, "snippets", sid, "heartbeat").json()["incoming"]["id"]
    a.post(f"/api/lock-requests/{rid}/grant")
    d = b.put(f"/api/snippets/{sid}/draft", json={"content": doc("alice part", "bob part")}).json()
    assert [c["email"] for c in d["contributors"]] == ["alice@a.co", "bob@a.co"] and d["last_editor"]["name"] == "N"
    forged = "msg\nCo-authored-by: Mallory <m@evil>\nLock-transferred: x -> y (granted)"
    sha = commit(b, fo, [sid], forged).json()["sha"]
    row = next(r for r in b.get(f"/api/folders/{fo}/history").json() if r["sha"] == sha)
    assert row["author_name"] == "N" and row["message"] == "msg"
    assert row["co_authors"] == [{"name": "Alice A", "email": "alice@a.co"}]
    assert row["lock_transfers"] == ["Alice A -> N (granted)"]
    from app.git import repo as git
    org = b.get("/api/orgs/current").json()["id"]
    body = git.log(git.repo_path(org, fo), limit=1)[0]["body"]
    assert "Co-authored-by: Alice A <alice@a.co>" in body and "Lock-transferred: Alice A -> N (granted)" in body
    assert "Mallory" not in body and "x -> y" not in body
    assert b.get(f"/api/snippets/{sid}/draft").json()["contributors"] == []  # reset after the commit
    doc_id = ws["doc"]  # revision table: committer first, then the others
    put_doc(b, doc_id, [ref(fo, sid, "commit", sha)])
    rows = b.get(f"/api/documents/{doc_id}/revision-history").json()
    assert next(r for r in rows if r["commit"] == "msg")["author"] == "N, Alice A"


def test_document_trailers(ws):
    a, b, d, fo = ws["a"], ws["b"], ws["doc"], ws["fo"]
    L(a, "documents", d)
    put_doc(a, d, [])
    a.put(f"/api/documents/{d}/draft", json={"title": "Manual v2"})
    assert L(b, "documents", d, "request").status_code == 200
    rid = L(a, "documents", d, "heartbeat").json()["incoming"]["id"]
    a.post(f"/api/lock-requests/{rid}/grant")
    b.put(f"/api/documents/{d}/draft", json={"title": "Manual v3"})
    sha = b.post(f"/api/folders/{fo}/commit", json={"message": "doc", "document_ids": [d]}).json()["sha"]
    row = next(r for r in b.get(f"/api/documents/{d}/history").json() if r["sha"] == sha)
    assert row["co_authors"][0]["email"] == "alice@a.co" and row["lock_transfers"] == ["Alice A -> N (granted)"]


def test_acquire_is_atomic_under_races(ws):
    sid = ws["sid"]
    clients = [add_member(ws["a"], ws["outbox"], f"r{i}@a.co", "editor") for i in range(6)]
    out, ev = [], threading.Event()

    def go(c):
        ev.wait()
        out.append(L(c, "snippets", sid).json()["held"])

    ts = [threading.Thread(target=go, args=(c,)) for c in clients]
    [t.start() for t in ts]
    ev.set()
    [t.join() for t in ts]
    assert sorted(out) == [False] * 5 + [True]


def test_cross_org_is_404_and_listing_is_org_scoped(ws):
    a, sid, d = ws["a"], ws["sid"], ws["doc"]
    L(a, "snippets", sid)
    L(ws["b"], "snippets", sid, "request")
    rid = L(a, "snippets", sid, "heartbeat").json()["incoming"]["id"]
    x = new_client()
    signup(x, "x@other.co", "Other")
    for k, i in (("snippets", sid), ("documents", d)):
        for op, m in (("", "post"), ("", "get"), ("heartbeat", "post"), ("release", "post"), ("request", "post"), ("takeover", "post")):
            assert L(x, k, i, op, m).status_code == 404, (k, op)
    assert x.post(f"/api/lock-requests/{rid}/grant").status_code == 404
    assert x.post(f"/api/lock-requests/{rid}/deny").status_code == 404
    assert x.get("/api/locks").json() == [] and x.get("/api/notifications").json() == dict(unread=0, items=[])


def test_rls_on_new_tables(ws, owner_engine):
    L(ws["a"], "snippets", ws["sid"])
    from app.db import SessionLocal
    with SessionLocal() as db:  # app role, no org context: nothing visible
        for t in ("edit_locks", "lock_requests", "notifications", "draft_backups"):
            assert db.execute(text(f"select count(*) from {t}")).scalar() == 0
    with owner_engine.connect() as c:
        assert c.execute(text("select count(*) from edit_locks")).scalar() == 1


def test_lists_have_draft_for_everyone(ws):
    a, b, sid, fo = ws["a"], ws["b"], ws["sid"], ws["fo"]
    L(a, "snippets", sid)
    a.put(f"/api/snippets/{sid}/draft", json={"content": doc("shared")})
    assert next(s for s in b.get(f"/api/folders/{fo}/snippets").json() if s["id"] == sid)["has_draft"]


def test_migration_merges_drafts_and_keeps_the_rest(owner_engine):
    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    command.downgrade(cfg, "0011")
    try:
        with owner_engine.begin() as c:
            ex = lambda q, **p: c.execute(text(q), p)
            ex("insert into organizations (id,name) values ('00000000-0000-0000-0000-0000000000a1','O')")
            for i in (1, 2, 3):
                ex("insert into users (id,email,name,password_hash) values (:i,:e,:n,'x')",
                   i=f"00000000-0000-0000-0000-00000000000{i}", e=f"u{i}@m.co", n=f"U{i}")
            ex("insert into folders (id,org_id,name) values ('00000000-0000-0000-0000-0000000000f1','00000000-0000-0000-0000-0000000000a1','F')")
            ex("""insert into snippets (id,org_id,folder_id,slug,title) select '00000000-0000-0000-0000-0000000000b1',org_id,id,'s','S'
                  from folders""")
            for i, ts in ((1, "2026-01-01"), (2, "2026-03-01"), (3, "2026-02-01")):
                ex("""insert into drafts (id,org_id,snippet_id,user_id,title,slug,content,styles,variables,updated_at)
                      values (gen_random_uuid(),'00000000-0000-0000-0000-0000000000a1','00000000-0000-0000-0000-0000000000b1',
                      :u,:t,'s','{}','[]','[]',:ts)""", u=f"00000000-0000-0000-0000-00000000000{i}", t=f"draft{i}", ts=ts)
        command.upgrade(cfg, "head")
        with owner_engine.connect() as c:
            keep = c.execute(text("select title, user_id::text, contributors from drafts")).all()
            assert len(keep) == 1 and keep[0][0] == "draft2" and keep[0][2][0]["name"] == "U2"  # newest wins
            assert c.execute(text("select count(*) from draft_backups")).scalar() == 2
            assert sorted(r[0] for r in c.execute(text("select payload->>'title' from draft_backups"))) == ["draft1", "draft3"]
    finally:
        command.upgrade(cfg, "head")
