"""Shared helpers for the e2e scripts. Every step waits for a real UI condition (no sleeps, no retry loops)."""
import os
import re

from playwright.sync_api import Page, expect

BASE = os.environ.get("E2E_BASE_URL", "http://localhost:8080")
ART = os.environ.get("E2E_ARTIFACTS", "e2e-artifacts")
EDITOR = ".ProseMirror[contenteditable=true]"  # only editable once the draft has loaded: typing earlier would be lost


def user(script: str):
    """(email, password, org name) of the throwaway user scripts/e2e-users.sh made for `script`."""
    import secrets
    tag = (os.environ.get("E2E_TAG") or secrets.token_hex(4)) + "-" + script  # sign-up is closed
    return f"e2e-{tag}@example.com", os.environ["E2E_PASSWORD"], f"E2E {tag}"


def preset_prefs(page: Page):
    """The app defaults to no preview at 100%; scripts that read/assert the live preview pick it like a user would (remembered per browser)."""
    page.add_init_script("localStorage.setItem('docgen.editor.v2.preview','live');localStorage.setItem('docgen.editor.v2.zoom','0.45')")


def login(page: Page, email: str, password: str, prefs=True):
    if prefs:
        preset_prefs(page)
    page.goto(BASE + "/login")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Log in").click()
    expect(page.get_by_role("heading", name=re.compile("Welcome back"))).to_be_visible()


def new_folder(page: Page, name: str):
    page.get_by_role("button", name="New folder").click()
    page.get_by_label("Folder name").fill(name)
    page.get_by_role("button", name="Create", exact=True).click()
    expect(page.get_by_role("heading", name=name, level=1)).to_be_visible()  # navigated into the new folder


def open_folder(page: Page, name: str):
    page.get_by_test_id("tree-folder").filter(has_text=name).click()
    expect(page.get_by_role("heading", name=name, level=1)).to_be_visible()


def new_item(page: Page, kind: str, title: str):
    """On a folder page: New snippet / New document -> dialog -> the new item's screen."""
    page.get_by_role("button", name=f"New {kind}").click()
    page.get_by_label("Title").fill(title)
    page.get_by_role("button", name=f"Create {kind}").click()
    expect(page).to_have_url(re.compile(f"/{kind}s/"))


def open_item(page: Page, title: str):
    page.get_by_test_id("item-row").get_by_role("link", name=title, exact=True).click()


def editor_ready(page: Page):
    ed = page.locator(EDITOR)
    expect(ed).to_be_visible()
    return ed


def type_into(page: Page, text: str, end=True):
    ed = editor_ready(page)
    ed.click()
    if end:
        page.keyboard.press("Control+End")
    page.keyboard.type(text)
    expect(page.get_by_test_id("preview")).to_contain_text(text.strip())


def saved(page: Page, testid="save-status"):
    expect(page.get_by_test_id(testid)).to_have_attribute("data-status", "saved")


def commit(page: Page, message: str, title: str | None = None):
    """On the commit screen: tick the change row (by title), message, commit; the list ends up empty."""
    rows = page.get_by_test_id("change-row")
    row = rows.filter(has_text=title).first if title else rows.first
    expect(row).to_be_visible()  # the list has loaded: this is what the old global checkbox lookup did not wait for
    row.get_by_role("checkbox").set_checked(True)
    page.get_by_label("Commit message").fill(message)
    page.get_by_role("button", name="Commit 1").click()
    expect(page.get_by_test_id("no-changes")).to_be_visible()


def commit_from_editor(page: Page, message: str, title: str | None = None):
    page.get_by_role("button", name="Commit…").click()
    expect(page).to_have_url(re.compile("/commit/"))
    commit(page, message, title)
