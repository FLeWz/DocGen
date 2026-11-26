"""Round 20 e2e: editing modes (Word | Markdown) and equations. Snippet editor: default Word, switch to Markdown, edit the text, live preview,
back to Word with unchanged blocks untouched, a syntax error keeps the user in Markdown (list, jump, nothing saved), reload = Word,
?focus=1. Import editor: Insert > Equation, the exported DOCX holds a native m:oMath, Markdown mode there too.
Logs in as the throwaway user `modes`."""
import io
import os
import re
import secrets
import shutil
import zipfile

from playwright.sync_api import expect, sync_playwright

from common import ART, BASE, EDITOR, login, new_folder, new_item, saved, user

email, password, _ = user("modes")
os.makedirs(ART, exist_ok=True)
sample = os.path.join(ART, "modes-sample.docx")
if os.environ.get("E2E_SAMPLE_DOCX"):
    shutil.copy(os.environ["E2E_SAMPLE_DOCX"], sample)
else:
    import subprocess
    with open(sample, "wb") as f:
        f.write(subprocess.run(["docker", "compose", "run", "--rm", "--no-deps", "-T", "api", "python", "-c",
                                "import sys; from tests.fixtures.sample import build; sys.stdout.buffer.write(build())"], capture_output=True, check=True).stdout)

CM = ".cm-content"

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1700, "height": 1000})
    try:
        login(page, email, password)
        new_folder(page, "Modes" + secrets.token_hex(2))
        new_item(page, "snippet", "Mode test")
        ed = page.locator(EDITOR)
        ed.wait_for()
        word, md = page.get_by_test_id("mode-word"), page.get_by_test_id("mode-markdown")
        expect(word).to_have_attribute("aria-pressed", "true")
        expect(md).to_have_attribute("aria-pressed", "false")
        expect(page.locator(CM)).to_have_count(0)

        # type: a bold word, a second paragraph centred by the ribbon
        ed.click()
        page.keyboard.type("Intro ")
        page.keyboard.press("Control+b")
        page.keyboard.type("bold")
        page.keyboard.press("Control+b")
        page.keyboard.press("Enter")
        page.keyboard.type("Centred para")
        page.get_by_role("button", name=re.compile("^Align center")).first.click()
        saved(page)
        draft_url = f"{BASE}/api/snippets/{re.search(r'/snippets/([0-9a-f-]+)', page.url).group(1)}/draft"
        before = page.request.get(draft_url).json()["content"]["content"]
        assert before[1]["attrs"]["pfmt"]["jc"] == "center", before[1]["attrs"]

        # Word -> Markdown: the text shows
        md.click()
        expect(md).to_have_attribute("aria-pressed", "true")
        cm = page.locator(CM)
        expect(cm).to_contain_text("Intro **bold**")
        expect(cm).to_contain_text("Centred para")
        expect(page.get_by_role("tab", name="Insert")).to_have_count(0)  # the Word ribbon is hidden
        # edit the text; the live preview follows (debounced parse)
        cm.click()
        page.keyboard.press("Control+End")
        page.keyboard.type("\n\nThird paragraph from markdown")
        expect(page.get_by_test_id("preview")).to_contain_text("Third paragraph from markdown", timeout=15_000)
        saved(page)

        # back to Word: new text is there, the untouched blocks keep their formatting (same JSON)
        word.click()
        expect(word).to_have_attribute("aria-pressed", "true")
        ed = page.locator(EDITOR)
        expect(ed).to_contain_text("Third paragraph from markdown")
        expect(ed.locator("strong")).to_have_text("bold")
        after = page.request.get(draft_url).json()["content"]["content"]
        assert [b for b in after if b["type"] == "paragraph" and b.get("content")][-1]["content"][0]["text"] == "Third paragraph from markdown"
        assert after[0] == before[0] and after[1] == before[1], (after[:2], before[:2])  # not touched by the round trip

        # syntax error: stays in Markdown, error list, jump, nothing saved
        md.click()
        cm = page.locator(CM)
        cm.click()
        page.keyboard.press("Control+End")
        page.keyboard.type("\n\nbroken {{ variable")
        word.click()
        expect(md).to_have_attribute("aria-pressed", "true")
        errs = page.get_by_test_id("text-errors")
        expect(errs).to_contain_text("Line")
        errs.get_by_role("button").first.click()
        assert "broken" not in str(page.request.get(draft_url).json()["content"])
        cm.click()
        page.keyboard.press("Control+End")
        for _ in range(len("broken {{ variable")):
            page.keyboard.press("Backspace")
        page.keyboard.type("fixed {{ name }}")
        word.click()
        expect(word).to_have_attribute("aria-pressed", "true", timeout=15_000)
        expect(page.locator(EDITOR)).to_contain_text("fixed")
        expect(page.locator(EDITOR).locator(".var-chip")).to_have_text("{{ name }}")

        # default after reload = Word; focus mode still works
        page.reload()
        expect(page.get_by_test_id("mode-word")).to_have_attribute("aria-pressed", "true")
        page.locator(EDITOR).wait_for()
        page.goto(page.url.split("?")[0] + "?focus=1")
        expect(page.get_by_test_id("mode-word")).to_have_attribute("aria-pressed", "true")
        page.get_by_test_id("mode-markdown").click()
        expect(page.locator(CM)).to_contain_text("Intro")
        assert page.evaluate("document.documentElement.classList.contains('ed-focus')")
        page.goto(page.url.split("?")[0])

        # import editor: Insert > Equation, native OMML in the exported DOCX; Markdown mode there too
        page.get_by_role("link", name="Imports").click()
        page.get_by_label("DOCX file").set_input_files(sample)
        expect(page).to_have_url(re.compile("/imports/"))
        iid = re.search(r"/imports/([0-9a-f-]+)", page.url).group(1)
        ed = page.locator(EDITOR)
        ed.wait_for()
        ed.click()
        page.keyboard.press("Control+End")
        page.get_by_role("tab", name="Insert").click()
        page.get_by_role("button", name=re.compile("Insert equation")).click()
        dlg = page.get_by_role("dialog")
        dlg.get_by_test_id("math-latex").fill("\\frac{a}{b} + \\sum_{i=1}^{n} x_i")
        expect(dlg.get_by_test_id("math-preview").locator(".katex")).to_be_visible()
        dlg.get_by_test_id("math-latex").fill("\\frac{")
        expect(dlg.get_by_test_id("math-ok")).to_be_disabled()
        dlg.get_by_test_id("math-latex").fill("\\frac{a}{b} + \\sum_{i=1}^{n} x_i")
        dlg.get_by_test_id("math-ok").click()
        expect(ed.locator(".katex").first).to_be_visible()
        saved(page)
        data = page.request.get(f"{BASE}/api/imports/{iid}/export").body()
        xml = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode()
        assert "<m:oMath" in xml and "<m:f>" in xml and "<m:nary>" in xml, "equation missing as OMML"
        page.get_by_test_id("mode-markdown").click()
        page.locator(CM).click()
        page.keyboard.press("Control+End")  # CodeMirror only renders the visible lines
        expect(page.locator(CM)).to_contain_text("$\\frac{a}{b}")
        page.get_by_test_id("mode-word").click()
        expect(page.locator(EDITOR).locator(".katex").first).to_be_visible()
        saved(page)
        data2 = page.request.get(f"{BASE}/api/imports/{iid}/export").body()
        assert data2 == data, "Word -> Markdown -> Word changed the export"
        print("modes e2e ok")
    except Exception:
        page.screenshot(path=os.path.join(ART, "modes-fail.png"))
        raise
    finally:
        browser.close()
