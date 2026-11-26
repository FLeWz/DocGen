"""LaTeX text helpers of the pandoc bridge (round 21): pre-processing before pandoc reads, static scan for errors/warnings, tabular
post-processing after pandoc writes. Pure text in, text out; nothing here starts a process or touches the filesystem.
LaTeX is only an authoring syntax: no engine runs, `\\usepackage` and friends are ignored, nothing can include a file."""
import json
import re

ANN_RE = re.compile(r"^[ \t]*%[ \t]*docgen:(\{.*\})[ \t]*$", re.M)  # `% docgen:{json}`: Word-only attributes of the block that follows
MAX_DEPTH = 100

# Constructs pandoc's reader maps. Anything else is reported (line number) and ignored by pandoc.
KNOWN_ENVS = set("""document itemize enumerate description quote quotation verse center flushleft flushright verbatim verbatim* tabular tabular* table table*
figure figure* equation equation* align align* alignat alignat* gather gather* multline multline* eqnarray eqnarray* split cases matrix pmatrix bmatrix
Bmatrix vmatrix Vmatrix smallmatrix array aligned alignedat gathered flalign flalign* subequations thebibliography abstract math displaymath minipage
longtable tabularx""".split())
KNOWN_CMDS = set("""documentclass usepackage title author date maketitle thanks tableofcontents listoffigures listoftables newpage clearpage pagebreak
part chapter section subsection subsubsection paragraph subparagraph par newline linebreak item textbf textit emph underline texttt textsc sout
textsuperscript textsubscript textrm textsf textnormal textup textsl textmd mbox hbox fbox makebox textcolor color colorbox small footnotesize scriptsize tiny large
Large LARGE huge Huge normalsize bf it rm sf tt sc bfseries itshape ttfamily scshape upshape slshape mdseries rmfamily sffamily normalfont em
hline cline multicolumn multirow caption label ref pageref eqref autoref nameref cref footnote footnotemark footnotetext cite citep citet
bibitem bibliography bibliographystyle href url hyperref hypertarget hypersetup includegraphics begin end verb newcommand renewcommand providecommand
def let setcounter addtocounter newenvironment frac sqrt sum int prod lim infty left right text mathbf mathrm mathit mathcal mathbb mathfrak mathsf mathtt
boldsymbol operatorname hat bar vec dot ddot tilde overline underbrace overbrace binom dfrac tfrac partial nabla alpha beta gamma delta epsilon varepsilon
zeta eta theta vartheta iota kappa lambda mu nu xi pi varpi rho sigma tau upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega
cdot cdots ldots dots vdots ddots times div pm mp leq geq le ge neq ne approx equiv sim simeq propto in notin subset subseteq supset supseteq cup cap setminus
emptyset forall exists neg land lor lnot rightarrow leftarrow leftrightarrow Rightarrow Leftarrow Leftrightarrow to gets mapsto longrightarrow implies iff
sin cos tan log ln exp min max sup inf lim det dim ker arg gcd mod bmod pmod quad qquad hspace vspace hfill vfill noindent indent centering raggedright raggedleft
textbackslash textbar textasciitilde textasciicircum textless textgreater textquotedbl textquoteleft textquoteright textendash textemdash textbullet
ldots dots S P copyright pounds dag ddag today LaTeX TeX tag nonumber notag displaystyle textstyle scriptstyle limits nolimits big Big bigg Bigg
langle rangle lfloor rfloor lceil rceil lbrace rbrace vert Vert mid colon prime star ast circ bullet oplus otimes angle perp parallel degree
tightlist texorpdfstring nolinkurl hypertarget phantomsection textquotesingle ensuremath protect labelenumi labelenumii labelenumiii labelenumiv alph Alph roman Roman arabic rule hrule vline toprule midrule bottomrule noalign tabcolsep arraystretch endnote selectfont fontsize""".split())
IGNORED = {"documentclass", "usepackage", "hypersetup", "bibliographystyle", "bibliography", "newcommand", "renewcommand", "providecommand", "def", "let",
           "newenvironment", "setcounter", "addtocounter", "tabcolsep", "arraystretch", "selectfont", "fontsize", "noindent", "indent", "centering",
           "raggedright", "raggedleft", "hspace", "vspace", "hfill", "vfill", "thanks", "limits", "nolimits", "tightlist"}


def mask(text: str) -> str:
    """comments and verbatim material blanked (length and newlines kept) so that scans see only markup"""
    def blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))
    text = re.sub(r"\\begin\{(verbatim\*?|lstlisting|minted)\}.*?\\end\{\1\}", blank, text, flags=re.S)
    text = re.sub(r"\\verb\*?(.).*?\1", blank, text)
    return re.sub(r"(?<!\\)%[^\n]*", blank, text)


def scan(text: str, store: dict) -> tuple[list[dict], list[str]]:
    """(errors, warnings) pandoc does not report: unbalanced braces/environments, runaway nesting, unknown opaque ids,
    unsupported commands/environments (kept out of the DOCX, listed with their line)"""
    errs, warns, stack, depth = [], [], [], 0
    m = mask(text)
    for i, line in enumerate(text.split("\n"), 1):
        for mm in re.finditer(r'"opaque":\s*"([^"]*)"', line) if ANN_RE.match(line) else ():
            if mm.group(1) not in store:
                errs.append({"line": i, "column": mm.start() + 1, "message": f"unknown opaque block {mm.group(1)!r}: placeholders cannot be invented, copy an existing one"})
    seen, line_of = set(), lambda pos: m.count("\n", 0, pos) + 1
    for mm in re.finditer(r"\\begin\{([^}\n]*)\}|\\end\{([^}\n]*)\}", m):
        pos = mm.start()
        col = pos - (m.rfind("\n", 0, pos) + 1) + 1
        if mm.group(1) is not None:
            stack.append((mm.group(1), line_of(pos), col))
            if len(stack) > MAX_DEPTH:
                errs.append({"line": line_of(pos), "column": col, "message": f"environments nested deeper than {MAX_DEPTH}"})
                return errs, warns
            if mm.group(1) not in KNOWN_ENVS and ("env", mm.group(1)) not in seen:
                seen.add(("env", mm.group(1)))
                warns.append(f"line {line_of(pos)}: environment {mm.group(1)!r} is not supported, its content is kept as plain text")
        elif not stack:
            errs.append({"line": line_of(pos), "column": col, "message": f"\\end{{{mm.group(2)}}} without \\begin"})
        elif stack[-1][0] != mm.group(2):
            errs.append({"line": line_of(pos), "column": col, "message": f"\\end{{{mm.group(2)}}} does not match \\begin{{{stack[-1][0]}}} on line {stack[-1][1]}"})
            stack.pop()
        else:
            stack.pop()
    errs += [{"line": ln, "column": col, "message": f"\\begin{{{name}}} is never closed"} for name, ln, col in stack]
    bal = []  # braces
    for mm in re.finditer(r"(?<!\\)(?:\\\\)*([{}])", m):
        pos = mm.start(1)
        if mm.group(1) == "{":
            bal.append(pos)
        elif bal:
            bal.pop()
        else:
            errs.append({"line": line_of(pos), "column": pos - (m.rfind("\n", 0, pos) + 1) + 1, "message": "unmatched }"})
    errs += [{"line": line_of(p), "column": p - (m.rfind("\n", 0, p) + 1) + 1, "message": "unmatched { (brace is never closed)"} for p in bal]
    for mm in re.finditer(r"\\begin\{tabular\*?\}(?:\{[^{}]*\})?\{((?:[^{}]|\{[^{}]*\})*)\}(.*?)\\end\{tabular\*?\}", m, flags=re.S):  # pandoc turns a table with more cells than columns into loose paragraphs
        spec = re.sub(r"[@!<>]\{[^{}]*\}", "", mm.group(1))
        spec = re.sub(r"\*\{(\d+)\}\{([^{}]*)\}", lambda x: x.group(2) * int(x.group(1)), spec)
        ncol, pos = len(re.findall(r"[lcrXpmb]", re.sub(r"[pmb]\{[^{}]*\}", "p", spec))), mm.start(2)
        for row in re.split(r"\\\\(?:\[[^\]]*\])?", mm.group(2)):
            cells = [c.strip() for c in re.split(r"(?<!\\)&", row.replace("\\hline", ""))]
            span = sum(int(x.group(1)) if (x := re.match(r"\\multicolumn\{(\d+)\}", c)) else 1 for c in cells)
            if any(cells) and span > ncol:
                errs.append({"line": line_of(pos + len(row) - len(row.lstrip())), "column": 1, "message": f"row has {span} cells but the column specification has {ncol}: add a column letter to \\begin{{tabular}}"})
            pos += len(row) + 2
    for mm in re.finditer(r"(?<!\\)(?:\\\\)*\\([A-Za-z]+)\*?", m):  # an odd number of backslashes: `\\c` is a line break and a letter
        name = mm.group(1)
        if name == "def" and m.startswith("\\label", mm.end()):
            continue  # pandoc's own `\def\labelenumi{..}` for list styles
        if name in KNOWN_CMDS and name not in IGNORED or (name, "c") in seen:
            continue
        seen.add((name, "c"))
        ln = line_of(mm.start())
        if name in ("usepackage", "documentclass"):
            warns.append(f"line {ln}: \\{name} is ignored (no packages are loaded: LaTeX here is only an authoring syntax)")
        elif name in ("newcommand", "renewcommand", "providecommand", "def", "let", "newenvironment"):
            warns.append(f"line {ln}: macro definitions are expanded while reading and not kept after switching back")
        elif name in IGNORED:
            continue
        elif name == "appendix":
            warns.append(f"line {ln}: \\appendix is ignored (following sections stay normal headings)")
        else:
            warns.append(f"line {ln}: unsupported command \\{name} is ignored")
    return errs, warns


def pre(text: str) -> tuple[str, list[dict]]:
    """Text pandoc should read, with the same number of lines: `% docgen:{}` lines become marker paragraphs, constructs pandoc drops or
    has no node for become tokens (DOCGENPB page break, DOCGENTOC.. generated lists, DOCGENTITLE, DOCGENBIB:key bibliography entries, DOCGENEND endnote)."""
    anns: list[dict] = []

    def ann(m):
        try:
            anns.append(json.loads(m.group(1)))
        except ValueError:
            return m.group(0)  # not ours: an ordinary comment
        return f"DOCGENMARK{len(anns) - 1}"
    text = ANN_RE.sub(ann, text)
    for pat, rep in (
        (r"\\(?:newpage|clearpage|pagebreak)(?![A-Za-z])(?:\[\d\])?", r" DOCGENPB "),
        (r"\\tableofcontents(?![A-Za-z])", r"\\par DOCGENTOC \\par "), (r"\\listoffigures(?![A-Za-z])", r"\\par DOCGENTOF \\par "),
        (r"\\listoftables(?![A-Za-z])", r"\\par DOCGENTOT \\par "), (r"\\maketitle(?![A-Za-z])", r"\\par DOCGENTITLE \\par "),
        (r"\\pageref\{", r"\\ref{PG:"), (r"\\endnote\{", r"\\footnote{DOCGENEND "), (r"\\cline\{[^}]*\}", r"\\hline "), (r"\\textbar(?:\{\})?", "|"), (r"\\(?:fbox|mbox|framebox)\{([^{}]*)\}", r"\1"),  # pandoc drops these with their content
        (r"\\begin\{thebibliography\}\{[^}\n]*\}|\\end\{thebibliography\}", ""), (r"\\bibitem(?:\[[^\]\n]*\])?\{([^}\n]*)\}", r"\\par DOCGENBIB:\1 "),
        (r"\{\{([^{}\n]*)\}\}", r"\\{\\{\1\\}\\}"),  # `{{ var }}` would be read as two TeX groups
    ):
        text = re.sub(pat, rep, text)
    return text, anns


def parse_error(stderr: str) -> dict:
    m = re.search(r"line (\d+), column (\d+)", stderr) or re.search(r"line (\d+)", stderr)
    msg = re.sub(r"\s+", " ", re.sub(r"^.*?\(line \d+, column \d+\):?", "", stderr.strip(), flags=re.S)) or stderr.strip()
    return {"line": int(m.group(1)) if m else 1, "column": int(m.group(2)) if m and (m.lastindex or 0) > 1 else 1, "message": msg[:200] or "LaTeX could not be read"}


def stderr_warnings(stderr: str) -> list[str]:
    out = []
    for ln in stderr.splitlines():
        if ln.startswith("[WARNING]"):
            w = re.sub(r"^\[WARNING\]\s*", "", ln).strip()
            m = re.search(r"at line (\d+)", w)
            w = f"line {m.group(1)}: " + re.sub(r"\s*at line \d+ column \d+", "", w) if m else w
            if w not in out:
                out.append(w)
    return out


TB, TE, SC, SR, SH = "%%DGTB", "%%DGTE", "%%DGC", "%%DGR", "%%DGH"  # table region / cell / row sentinels written as raw blocks, resolved by `fix_tables`


def fix_tables(text: str, specs: list[tuple[str, bool]]) -> str:
    """Resolve the sentinel regions of `\\begin{tabular}`: cells of the writer come as blocks separated by blank lines, this puts them on one
    row line each (`a & b \\\\`). `specs` = per table (column spec, hlines)."""
    it = iter(specs)

    def one(m):
        spec, lines = next(it)
        hl = "\\hline\n"
        rows, cur = [], []
        parts = re.split(r"\n*(%%DG[CRH])\n*", m.group(1).strip("\n"))  # [cell, sentinel, cell, sentinel, ..., ""]
        for k in range(0, len(parts) - 1, 2):
            cur.append(re.sub(r"\s*\n\s*", " ", re.sub(r"\n\n+", " \\\\par ", parts[k].strip())))
            if parts[k + 1] in (SR, SH):  # SH: the row is followed by \\hline (header separator, bottom rule)
                rows.append(" & ".join(cur) + " \\\\\n" + (hl if parts[k + 1] == SH and lines else ""))
                cur = []
        return f"\\begin{{tabular}}{{{spec}}}\n{hl if lines else ''}" + "".join(rows) + "\\end{tabular}"
    return re.sub(rf"{TB}\n(.*?)\n*{TE}", one, text, flags=re.S)
