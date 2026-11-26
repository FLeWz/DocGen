"""Round 21 e2e: the LaTeX editing mode. Snippet editor: Word content (written through Markdown) shows as plain LaTeX (headings, lists, table, equation),
edit it, live preview follows, back to Word with unchanged blocks untouched, a syntax error keeps the user in LaTeX, a pasted full \\documentclass
document parses, Markdown <-> LaTeX switch, completions, reload = Word. Logs in as the throwaway user `latex`."""
import os
import re
import secrets

from playwright.sync_api import expect, sync_playwright

from common import ART, BASE, EDITOR, login, new_folder, new_item, saved, user

email, password, _ = user("latex")
os.makedirs(ART, exist_ok=True)
CM = ".cm-content"
MD = "# Overview\n\n- first item\n- second item\n\n| Name | Qty |\n|------|-----|\n| apple | 3 |\n\nEnergy $E=mc^2$ is famous.\n"
FULL = ("\\documentclass{article}\n\\usepackage{amsmath}\n\\title{Pasted}\n\\begin{document}\n\\section{From a full document}\n"
        "Plain text with \\textbf{bold} and a footnote\\footnote{Note text}.\n\n\\begin{enumerate}\n\\item one\n\\item two\n\\end{enumerate}\n\\end{document}\n")

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1700, "height": 1000})
    try:
        login(page, email, password)
        new_folder(page, "Latex" + secrets.token_hex(2))
        new_item(page, "snippet", "LaTeX test")
        ed = page.locator(EDITOR)
        ed.wait_for()
        word, md, tex = page.get_by_test_id("mode-word"), page.get_by_test_id("mode-markdown"), page.get_by_test_id("mode-latex")
        expect(page.get_by_test_id("mode-word").locator("xpath=..").locator("button")).to_have_text(["Word", "Markdown", "LaTeX"])
        expect(word).to_have_attribute("aria-pressed", "true")
        expect(page.locator(CM)).to_have_count(0)

        # content: a centred paragraph by the ribbon, the rest through Markdown
        ed.click()
        page.keyboard.type("Centred para")
        page.get_by_role("button", name=re.compile("^Align center")).first.click()
        saved(page)
        md.click()
        page.locator(CM).click()
        page.keyboard.press("Control+End")
        page.keyboard.insert_text("\n\n" + MD)
        saved(page)
        draft_url = f"{BASE}/api/snippets/{re.search(r'/snippets/([0-9a-f-]+)', page.url).group(1)}/draft"
        word.click()
        expect(word).to_have_attribute("aria-pressed", "true", timeout=15_000)
        expect(page.locator(EDITOR).locator("h1")).to_contain_text("Overview")
        saved(page)
        before = page.request.get(draft_url).json()["content"]["content"]
        assert before[0]["attrs"]["pfmt"]["jc"] == "center", before[0]

        # Word -> LaTeX: valid-looking LaTeX, body only
        tex.click()
        expect(tex).to_have_attribute("aria-pressed", "true")
        cm = page.locator(CM)
        for frag in ["\\begin{center}", "\\section{Overview}", "\\begin{itemize}", "\\begin{tabular}", "apple & 3"]:
            expect(cm).to_contain_text(frag)
        cm.click()
        page.keyboard.press("Control+End")
        expect(cm).to_contain_text("E=mc^2")
        expect(page.get_by_role("tab", name="Insert")).to_have_count(0)
        assert "\\documentclass" not in cm.inner_text()

        # completions come from the static list
        page.keyboard.insert_text("\n\n")
        page.keyboard.type("\\sec")
        expect(page.locator(".cm-tooltip-autocomplete")).to_contain_text("\\section")
        page.keyboard.press("Escape")
        for _ in range(4):
            page.keyboard.press("Backspace")

        # edit: a new paragraph; the preview follows the debounced parse
        page.keyboard.insert_text("Third paragraph from LaTeX with \\emph{style}.")
        expect(page.get_by_test_id("preview")).to_contain_text("Third paragraph from LaTeX", timeout=15_000)
        saved(page)

        # back to Word: new text there, equation rendered, untouched blocks keep their formatting
        word.click()
        expect(word).to_have_attribute("aria-pressed", "true", timeout=15_000)
        ed = page.locator(EDITOR)
        expect(ed).to_contain_text("Third paragraph from LaTeX")
        expect(ed.locator("em").last).to_have_text("style")
        expect(ed.locator(".katex").first).to_be_visible()
        expect(ed.locator("table")).to_have_count(1)
        saved(page)
        after = page.request.get(draft_url).json()["content"]["content"]
        assert after[:len(before)] == before, "untouched blocks changed in the LaTeX round trip"
        assert "Third paragraph" in str(after[-1])

        # syntax error: stays in LaTeX, list with line numbers, jump, nothing saved
        tex.click()
        cm = page.locator(CM)
        cm.click()
        page.keyboard.press("Control+End")
        page.keyboard.insert_text("\n\n\\begin{itemize}\n\\item broken\n")
        word.click()
        expect(tex).to_have_attribute("aria-pressed", "true")
        errs = page.get_by_test_id("text-errors")
        expect(errs).to_contain_text("never closed", timeout=15_000)
        expect(errs).to_contain_text("Line")
        errs.get_by_role("button").first.click()
        assert "broken" not in str(page.request.get(draft_url).json()["content"])
        page.keyboard.press("Control+End")
        page.keyboard.insert_text("\\end{itemize}\n")
        word.click()
        expect(word).to_have_attribute("aria-pressed", "true", timeout=15_000)
        expect(page.locator(EDITOR)).to_contain_text("broken")

        # Markdown <-> LaTeX directly (through Word, same rules)
        md.click()
        expect(md).to_have_attribute("aria-pressed", "true")
        expect(page.locator(CM)).to_contain_text("Centred para")
        tex.click()
        expect(tex).to_have_attribute("aria-pressed", "true", timeout=15_000)

        # a pasted full document (preamble, \title, \begin{document}) parses; the info warning asks for confirmation
        cm = page.locator(CM)
        cm.click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text(FULL)
        expect(page.get_by_test_id("preview")).to_contain_text("From a full document", timeout=15_000)
        word.click()
        errs = page.get_by_test_id("text-errors")
        expect(errs).to_contain_text("usepackage")  # ignored package: a warning, never an error
        page.get_by_test_id("switch-lossy").click()
        expect(word).to_have_attribute("aria-pressed", "true", timeout=15_000)
        ed = page.locator(EDITOR)
        expect(ed.locator("h1").last).to_contain_text("From a full document")
        expect(ed.locator("strong").last).to_have_text("bold")
        expect(ed.locator("ol li")).to_have_count(2)
        saved(page)

        # default after reload = Word
        page.reload()
        expect(page.get_by_test_id("mode-word")).to_have_attribute("aria-pressed", "true")
        expect(page.locator(CM)).to_have_count(0)
        print("latex e2e ok")
    except Exception:
        page.screenshot(path=os.path.join(ART, "latex-fail.png"))
        raise
    finally:
        browser.close()
