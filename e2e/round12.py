"""Round 12 e2e: snippets from an import, same-title handling and update from an import.
Import the fixture -> split (4 new snippets) -> commit them -> split again (all 'exists', Update existing is the default) ->
selection with a taken title (dialog) -> edit a second import and 'Update snippets from this import…' (only the changed row is
ticked, drafts only) -> 'Go to Commit' -> commit -> the snippet history shows the second version."""
import os
import re
import shutil
import subprocess

from playwright.sync_api import expect, sync_playwright

from common import ART, editor_ready, login, new_folder, open_folder, saved, user

email, password, _ = user("round12")
os.makedirs(ART, exist_ok=True)
sample = os.path.join(ART, "round12-sample.docx")
if os.environ.get("E2E_SAMPLE_DOCX"):
    shutil.copy(os.environ["E2E_SAMPLE_DOCX"], sample)
else:
    with open(sample, "wb") as f:
        f.write(subprocess.run(["docker", "compose", "run", "--rm", "--no-deps", "-T", "api", "python", "-m", "tests.fixtures.sample", "-"],
                               capture_output=True, check=True).stdout)


def upload(page):
    page.get_by_role("navigation", name="Navigation").get_by_role("link", name="Imports").click()
    page.get_by_label("DOCX file").set_input_files(sample)
    expect(page).to_have_url(re.compile("/imports/"))
    return editor_ready(page)


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1700, "height": 1000})
    try:
        login(page, email, password)
        new_folder(page, "Manuals")
        folder_url = page.url
        upload(page)

        # split: nothing exists yet -> all rows new
        page.get_by_role("button", name="Split by headings").click()
        rows = page.get_by_test_id("split-row")
        expect(rows).to_have_count(4)
        expect(page.get_by_test_id("split-summary")).to_contain_text("4 new · 0 updated")
        expect(rows.first).to_contain_text("new")
        page.get_by_role("button", name="Create 4 snippets").click()
        expect(page.get_by_text("Created 4 snippet drafts")).to_be_visible()
        page.goto(folder_url.replace("/folders/", "/commit/"))
        page.get_by_label("Select all").check()
        page.get_by_label("Commit message").fill("First import")
        page.get_by_role("button", name="Commit 4").click()
        expect(page.get_by_test_id("no-changes")).to_be_visible()

        # split again: every row exists, default = Update existing, one row switched to Skip, one to a separate snippet
        page.get_by_role("navigation", name="Navigation").get_by_role("link", name="Imports").click()
        page.locator("a.titlelink").first.click()
        editor_ready(page)
        page.get_by_role("button", name="Split by headings").click()
        rows = page.get_by_test_id("split-row")
        expect(rows).to_have_count(4)
        expect(rows.first).to_contain_text("exists")
        expect(rows.first.get_by_role("combobox")).to_have_value("update_existing")
        rows.nth(1).get_by_role("combobox").select_option("skip")
        rows.nth(2).get_by_role("combobox").select_option("create_new_with_suffix")
        expect(page.get_by_test_id("split-summary")).to_contain_text("1 new · 2 updated (as drafts, not committed) · 1 skipped")
        page.get_by_role("button", name="Apply (1 new, 2 updated)").click()
        expect(page.get_by_text("Created 1 snippet draft, Updated 2 drafts (not committed)")).to_be_visible()
        page.get_by_role("link", name="Go to Commit").click()
        expect(page).to_have_url(re.compile("/commit/"))
        expect(page.get_by_test_id("change-row")).to_have_count(1)  # only the new snippet: the two updates carried no changes

        # selection with a taken title -> the dialog; update it
        page.get_by_role("navigation", name="Navigation").get_by_role("link", name="Imports").click()
        page.locator("a.titlelink").first.click()
        ed = editor_ready(page)
        ed.get_by_text("Callout paragraph with a custom style").click()
        page.get_by_role("button", name="Create snippet from selection").click()
        page.get_by_label("Snippet title").fill("  introduction ")
        page.get_by_role("button", name="Create snippet", exact=True).click()
        expect(page.get_by_test_id("snippet-conflict")).to_contain_text("A snippet titled “Introduction” already exists in Manuals")
        expect(page.get_by_test_id("snippet-conflict")).to_contain_text(re.compile(r"last commit \d\d\.\d\d\.\d{4} by "))
        page.get_by_role("button", name="Choose another title").click()
        page.get_by_label("Snippet title").fill("Callout bit")
        page.get_by_role("button", name="Create snippet", exact=True).click()
        expect(page.get_by_text('Created 1 snippet draft')).to_be_visible()

        # a second upload with an edit: only the changed section is ticked; applied to drafts
        upload(page)
        ed = editor_ready(page)
        ed.get_by_text("The end.").click()
        page.keyboard.press("End")
        page.keyboard.type(" REVISED")
        saved(page)
        page.get_by_role("button", name="Update snippets from this import…").click()
        d = page.get_by_test_id("update-dialog")
        page.get_by_label("Snippets in folder").select_option(label="Manuals")
        urows = d.get_by_test_id("update-row")
        expect(urows).to_have_count(4)  # level 1 headings
        opaque = urows.filter(has=page.locator("span.t", has_text="Opaque things"))
        expect(opaque).to_contain_text("1 changed")
        expect(opaque.get_by_role("checkbox")).to_be_checked()
        expect(urows.filter(has=page.locator("span.t", has_text="Merged cells"))).to_contain_text("no changes")
        expect(urows.filter(has=page.locator("span.t", has_text="Merged cells")).get_by_role("checkbox")).not_to_be_checked()
        expect(d.get_by_test_id("update-summary")).to_contain_text("1 of 4 selected")
        page.get_by_role("button", name="Apply to drafts (1)").click()
        expect(page.get_by_text("Updated 1 draft (not committed)")).to_be_visible()
        page.get_by_role("link", name="Go to Commit").click()
        expect(page).to_have_url(re.compile("/commit/"))
        row = page.get_by_test_id("change-row").filter(has_text="Opaque things")  # the earlier new snippets stay uncommitted
        row.get_by_role("checkbox").check()
        page.get_by_label("Commit message").fill("Manual revised")
        page.get_by_role("button", name="Commit 1").click()
        expect(row).to_have_count(0)
        open_folder(page, "Manuals")
        page.get_by_test_id("item-row").get_by_role("link", name="Opaque things", exact=True).click()
        page.get_by_role("link", name="History").click()
        expect(page.get_by_role("cell").filter(has_text="Manual revised").first).to_be_visible()
        expect(page.get_by_role("cell").filter(has_text="First import").first).to_be_visible()
        print("e2e round12 OK")
    except Exception:
        page.screenshot(path=f"{ART}/round12-failure.png", full_page=True)
        raise
    finally:
        browser.close()
