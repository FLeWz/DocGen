"""Round 16 e2e, two browser contexts (A = owner, B = invited editor): A edits, B sees read-only, B requests, A grants, B edits and
commits, history shows the co-author; deny path with the cooldown message. `python locks.py timeout` (needs a stack started with a
short LOCK_REQUEST_TIMEOUT_S, see `make test-e2e-locks`): nobody answers, the rights move to B and A's text is in the draft."""
import json
import os
import re
import sys
import time
import urllib.request

from playwright.sync_api import expect, sync_playwright

from common import BASE, EDITOR, ART, commit_from_editor, login, preset_prefs, saved, type_into, user

MODE = sys.argv[1] if len(sys.argv) > 1 else "grant"
MAIL = os.environ.get("MAIL_URL", "http://localhost:8025")
owner, pwd, org_name = user("locks" if MODE != "timeout" else "lockstimeout")
tag = owner.split("e2e-")[1].split("@")[0]
guest = f"bob-{tag}@example.com"


def mail_to(addr):
    for _ in range(40):
        msgs = json.load(urllib.request.urlopen(f"{MAIL}/api/v1/messages"))["messages"]
        hit = next((m for m in msgs if addr in [t["Address"] for t in m["To"]]), None)
        if hit:
            return json.load(urllib.request.urlopen(f"{MAIL}/api/v1/message/{hit['ID']}"))["Text"]
        time.sleep(0.5)
    raise AssertionError(f"no mail for {addr}")


def api(page, method, path, body=None, ok=True):
    page.request.get(BASE + "/api/auth/csrf")
    csrf = next(c["value"] for c in page.context.cookies() if c["name"] == "docgen_csrf")
    r = page.request.fetch(BASE + "/api" + path, method=method, data=body, headers={"X-CSRF-Token": csrf})
    assert r.ok or not ok, (path, r.status, r.text())
    return r


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    a, b = (browser.new_context(viewport={"width": 1500, "height": 1000}).new_page() for _ in range(2))
    try:
        login(a, owner, pwd)
        api(a, "POST", "/invites", {"email": guest, "role": "editor"})
        token = re.search(r"token=([\w\-.]+)", mail_to(guest)).group(1)
        preset_prefs(b)
        b.goto(f"{BASE}/accept-invite?token={token}")
        b.get_by_label("Your name").fill("Bob Builder")
        b.get_by_label("Password").fill(pwd)
        b.get_by_role("button", name="Create account & join").click()
        expect(b.get_by_role("heading", name=re.compile("Welcome back"))).to_be_visible()

        fo = api(a, "POST", "/folders", {"name": "Locks"}).json()["id"]
        para = lambda t: {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": t}]}]}
        snippets = []
        for title in ("Shared", "Second"):
            sid = api(a, "POST", f"/folders/{fo}/snippets", {"title": title}).json()["id"]
            api(a, "PUT", f"/snippets/{sid}/draft", {"content": para("start")})
            api(a, "POST", f"/folders/{fo}/commit", {"message": f"init {title}", "snippet_ids": [sid]})
            api(a, "POST", f"/locks/snippets/{sid}/release")
            snippets.append(sid)
        s1, s2 = snippets

        a.goto(f"{BASE}/snippets/{s1}")
        type_into(a, " Alice wrote this")
        saved(a)
        expect(a.get_by_test_id("lock-bar")).to_have_count(0)  # the holder sees no banner

        b.goto(f"{BASE}/snippets/{s1}")  # B: read-only, banner names the holder, request button
        bar = b.get_by_test_id("lock-bar")
        expect(bar).to_have_attribute("data-state", "readonly")
        expect(bar).to_contain_text(re.compile(r"E2E User is editing since \d\d:\d\d"))
        expect(b.locator(EDITOR)).to_have_count(0)
        b.get_by_test_id("request-edit").click()
        expect(bar).to_have_attribute("data-state", "pending")
        expect(b.get_by_test_id("lock-countdown")).to_contain_text(re.compile(r"[0-5]:\d\d"))

        if MODE == "timeout":  # nobody answers: the modal is shown to A and ignored
            expect(a.get_by_test_id("lock-request-modal")).to_be_visible(timeout=15_000)
            expect(b.locator(EDITOR)).to_be_visible(timeout=60_000)  # rights moved after LOCK_REQUEST_TIMEOUT_S
            expect(b.get_by_test_id("preview")).to_contain_text("Alice wrote this")  # her edits were already in the draft
            expect(a.get_by_test_id("lock-bar")).to_have_attribute("data-state", "transferred", timeout=15_000)
            expect(a.get_by_test_id("lock-bar")).to_contain_text("your last changes were saved to the draft")
            expect(a.get_by_test_id("copy-unsaved")).to_be_visible()
            expect(a.get_by_test_id("preview")).to_contain_text("Alice wrote this")  # nothing local was lost
            assert api(b, "POST", f"/locks/snippets/{s1}/request", ok=False).status in (200, 409, 429)
            print("e2e locks timeout OK")
            sys.exit(0)

        modal = a.get_by_test_id("lock-request-modal")  # the holder's open editor is told via the heartbeat poll
        expect(modal).to_contain_text("Bob Builder", timeout=15_000)
        expect(a.get_by_test_id("lock-incoming-countdown")).to_contain_text(re.compile(r"[0-5]:\d\d"))
        expect(a.get_by_test_id("bell-count")).to_have_text("1", timeout=15_000)
        a.get_by_test_id("lock-grant").click()
        expect(a.get_by_test_id("lock-bar")).to_have_attribute("data-state", "granted")
        expect(a.get_by_test_id("lock-bar")).to_contain_text("Edit rights given to Bob Builder")
        expect(a.locator(EDITOR)).to_have_count(0)

        expect(bar).to_have_count(0, timeout=10_000)  # B gets the rights within a poll
        expect(b.get_by_test_id("preview")).to_contain_text("Alice wrote this")
        type_into(b, " Bob adds that")
        saved(b)
        b.get_by_test_id("bell").click()
        expect(b.get_by_test_id("bell-panel")).to_contain_text("gave you edit rights")
        commit_from_editor(b, "Joint edit", "Shared")
        b.goto(f"{BASE}/history/folder/{fo}")
        row = b.get_by_test_id("commit-row").filter(has_text="Joint edit")
        expect(row.get_by_test_id("co-authors")).to_contain_text("E2E User")
        expect(row.get_by_test_id("lock-transfers")).to_contain_text("E2E User -> Bob Builder (granted)")

        # deny path + cooldown message on the second snippet
        a.goto(f"{BASE}/snippets/{s2}")
        type_into(a, " second by Alice")
        b.goto(f"{BASE}/snippets/{s2}")
        b.get_by_test_id("request-edit").click()
        expect(a.get_by_test_id("lock-request-modal")).to_be_visible(timeout=15_000)
        a.get_by_test_id("lock-deny").click()
        bar = b.get_by_test_id("lock-bar")
        expect(bar).to_have_attribute("data-state", "denied", timeout=10_000)
        expect(bar).to_contain_text(re.compile(r"You can request again at \d\d:\d\d \(\d\d\.\d\d\.\d{4}\)"))
        expect(b.get_by_test_id("request-edit")).to_be_disabled()
        r = api(b, "POST", f"/locks/snippets/{s2}/request", ok=False)
        assert r.status == 429 and 0 < r.json()["detail"]["retry_after"] <= 1800, r.text()
        expect(b.locator(EDITOR)).to_have_count(0)
        # lock badge in the folder list while A edits
        b.goto(f"{BASE}/folders/{fo}")
        expect(b.get_by_test_id("item-row").filter(has_text="Second").get_by_test_id("lock-badge")).to_contain_text("E2E User")
        print("e2e locks OK")
    except Exception:
        os.makedirs(ART, exist_ok=True)
        a.screenshot(path=f"{ART}/locks-failure-a.png", full_page=True)
        b.screenshot(path=f"{ART}/locks-failure-b.png", full_page=True)
        raise
    finally:
        browser.close()
