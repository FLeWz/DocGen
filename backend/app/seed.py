"""Demo data (dev only): `python -m app.seed`. Idempotent; runs on api start when SEED_DEMO=1.
Goes through the real API (in-process TestClient), so RLS, git and validation behave exactly as for users.
Uses tests.fixtures.sample for the imported DOCX (python-docx + Pillow, both runtime deps)."""
import sys

from fastapi import HTTPException
from fastapi.testclient import TestClient

from . import mail
from .db import SessionLocal
from .routers.auth import register
from .main import app

ORG, PW = "Demo Org", "demo-password-123"
USERS = {"owner": "owner@example.com", "admin": "admin@example.com", "editor": "editor@example.com",
         "viewer": "viewer@example.com"}


def client() -> TestClient:
    c = TestClient(app)
    c.headers["X-CSRF-Token"] = c.get("/api/auth/csrf").json()["csrf_token"]
    return c


def ok(r, *codes):
    assert r.status_code in (codes or (200, 201, 204)), f"{r.request.method} {r.request.url.path}: {r.status_code} {r.text}"
    return r.json() if r.content else None


def p(*parts):
    return {"type": "paragraph", "content": [{"type": "text", "text": t} if isinstance(t, str) else t for t in parts]}


def h(text, level=1):
    return {"type": "heading", "attrs": {"level": level}, "content": [{"type": "text", "text": text}]}


def var(name, default=None, filter=None):
    return {"type": "variable", "attrs": {"name": name, "default": default, "filter": filter}}


def doc(*blocks):
    return {"type": "doc", "content": list(blocks)}


def cell(*c, header=False):
    return {"type": "tableHeader" if header else "tableCell", "content": [p(*c)]}


def seeded(c) -> bool:
    if c.post("/api/auth/login", json={"email": USERS["owner"], "password": PW}).status_code != 200:
        return False
    org = next((o for o in ok(c.get("/api/orgs")) if o["name"] == ORG), None)
    if not org:
        return False
    ok(c.post("/api/orgs/switch", json={"org_id": org["id"]}))
    if ok(c.get("/api/imports")):  # the import is the last step: its presence = complete
        return True
    ok(c.delete("/api/orgs/current"))  # partial leftovers of a failed run: start over (users are reused)
    return False


def join(owner, role, outbox):
    email = USERS[role]
    ok(owner.post("/api/invites", json={"email": email, "role": role}))
    token = outbox[-1][2].split("token=")[1].split()[0]
    c = client()
    if c.post("/api/invites/lookup", json={"token": token}).json()["user_exists"]:
        ok(c.post("/api/auth/login", json={"email": email, "password": PW}))
        ok(c.post("/api/invites/accept", json={"token": token}))
    else:
        ok(c.post("/api/invites/accept", json={"token": token, "name": role.title(), "password": PW}))
    return c


def snippet(c, folder, title, versions, tags=()):
    """versions: list of (commit message, content, variables); tags[i] tags commit i. Returns (id, [shas])."""
    sid = ok(c.post(f"/api/folders/{folder}/snippets", json={"title": title}))["id"]
    shas = []
    for n, (msg, content, variables) in enumerate(versions):
        ok(c.put(f"/api/snippets/{sid}/draft", json={"content": content, "variables": variables,
                                                      "styles": ["Normal", "Heading1"]}))
        shas.append(ok(c.post(f"/api/folders/{folder}/commit", json={"message": msg, "snippet_ids": [sid]}))["sha"])
        if n < len(tags) and tags[n]:
            ok(c.post(f"/api/folders/{folder}/tags", json={"name": tags[n], "sha": shas[-1]}))
    return sid, shas


def seed() -> str:
    outbox: list = []
    real, mail.send_mail = mail.send_mail, lambda to, subject, body: outbox.append((to, subject, body)) or True
    try:
        owner = client()
        if seeded(owner):
            return "demo data already present"
        try:  # public sign-up is closed: create the owner directly
            with SessionLocal() as db:
                register(db, USERS["owner"], "Olivia Owner", PW, ORG)
            ok(owner.post("/api/auth/login", json={"email": USERS["owner"], "password": PW}))
        except HTTPException:  # user left over from a failed run: login already done in seeded(); add a fresh org
            ok(owner.post("/api/orgs", json={"name": ORG}))
        admin, editor = join(owner, "admin", outbox), join(owner, "editor", outbox)
        join(owner, "viewer", outbox)

        sec = ok(editor.post("/api/folders", json={"name": "Security"}))["id"]
        ops = ok(editor.post("/api/folders", json={"name": "Operations"}))["id"]
        ok(editor.post("/api/folders", json={"name": "Runbooks", "parent_id": ops}))

        intro, (i1, i2, i3) = snippet(editor, sec, "Introduction", [
            ("Add introduction", doc(h("Introduction"), p("This document describes the security controls of ", var("project_name", "Acme Rollout"), ".")), ["project_name"]),
            ("Add scope paragraph", doc(h("Introduction"), p("This document describes the security controls of ", var("project_name", "Acme Rollout"), "."), p("Scope: all production systems.")), ["project_name"]),
            ("Clarify scope", doc(h("Introduction"), p("This document describes the security controls of ", var("project_name", "Acme Rollout"), "."), p("Scope: all production systems and their backups.")), ["project_name"]),
        ], tags=["v1.0", "v1.1"])
        snippet(editor, sec, "Access Policy", [
            ("Add access policy", doc(h("Access Policy", 2), p("Access is granted on a least-privilege basis.")), []),
            ("Require MFA", doc(h("Access Policy", 2), p("Access is granted on a least-privilege basis. MFA is mandatory for admins.")), []),
        ], tags=["policy-1.0"])
        contacts = doc(h("Escalation Contacts", 2), p("Client: ", var("client", "Acme Corp"), " (", var("client", "Acme Corp", "upper"), ")"), {
            "type": "table", "attrs": {"dynamicList": "contacts"}, "content": [
                {"type": "tableRow", "content": [cell("Name", header=True), cell("Role", header=True)]},
                {"type": "tableRow", "content": [cell(var("row.name")), cell(var("row.role"))]}]})
        esc, _ = snippet(editor, ops, "Escalation Contacts", [("Add escalation contacts", contacts, ["client", "contacts"])], tags=["v1.0"])
        ok(editor.get(f"/api/folders/{ops}/changes"))

        base = ok(admin.post("/api/base-documents", data={"name": "Default"}))
        d = ok(editor.post("/api/documents", json={"folder_id": ops, "title": "Operations Manual"}))["id"]
        body = {"base_document": {"id": base["id"]},
                "blocks": [{"type": "toc"},
                           {"type": "snippet_ref", "folder_id": sec, "snippet_id": intro, "version": {"type": "tag", "value": "v1.0"}},  # older than HEAD: "update available"
                           {"type": "page_break"},
                           {"type": "snippet_ref", "folder_id": ops, "snippet_id": esc, "version": {"type": "commit", "value": None}},
                           {"type": "heading", "text": "Appendix", "level": 1, "numbered": True}],
                "variables": {"declarations": [{"name": "project_name", "type": "text", "default": "Acme Rollout"},
                                               {"name": "client", "type": "text", "default": "Acme Corp"},
                                               {"name": "contacts", "type": "list", "columns": ["name", "role"]}],
                              "values": {"contacts": [{"name": "Dana Duty", "role": "On-call lead"}, {"name": "Sam Support", "role": "Service desk"}]}},
                "links": [{"title": "Security Policy", "doc_id": "SEC-001", "version": "2.1", "url": "https://example.com/security-policy"},
                          {"title": "Backup Procedure", "doc_id": "OPS-014", "version": "1.0"}]}
        body["blocks"][3]["version"]["value"] = ok(editor.get(f"/api/snippets/{esc}"))["sha"]
        ok(editor.put(f"/api/documents/{d}/draft", json=body))
        ok(editor.post(f"/api/folders/{ops}/commit", json={"message": "Create operations manual", "document_ids": [d]}))
        body["title"] = "Operations Manual"
        body["variables"]["values"]["project_name"] = "Acme Rollout 2"
        body["links"].append({"title": "Incident Policy", "doc_id": "OPS-020", "version": "0.3"})
        ok(editor.put(f"/api/documents/{d}/draft", json=body))
        ok(editor.post(f"/api/folders/{ops}/commit", json={"message": "Add incident policy link", "document_ids": [d]}))

        from tests.fixtures.sample import build
        ok(editor.post("/api/import/docx", files={"file": ("sample.docx", build(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                       data={"title": "Imported sample"}))
        return "demo data created"
    finally:
        mail.send_mail = real


if __name__ == "__main__":
    from . import config
    if config.AUTH_MODE == "oidc":
        sys.exit("seed: skipped, demo users have passwords and AUTH_MODE=oidc has none")
    try:
        print("seed:", seed())
    except Exception as e:  # never keep the app from starting
        print("seed failed:", e, file=sys.stderr)
