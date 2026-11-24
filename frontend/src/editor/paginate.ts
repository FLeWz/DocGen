// Pagination estimate: block heights + page body height + Word's keep rules -> which blocks/rows go on which page.
// Pure (no DOM). Breaks fall at block boundaries; tables split between rows (header rows repeat); a block taller than a page
// gets a page of its own and overflows. Not Word's algorithm (no line splitting, no widow/orphan control, no floating objects).
export type Block = {
  h: number;              // border-box height (any unit, the same as bodyH)
  mt?: number; mb?: number; // top/bottom margin: adjacent margins collapse (the larger wins, as in Word/LibreOffice); mt is not drawn at the top of a page after the first
  keepNext?: boolean;     // stays on the page of the next block
  breakBefore?: boolean;  // page-break-before, new section (false: a new section that continues on the same page)
  breakAfter?: boolean;   // explicit page break inside/at the end of the block: the next block starts a page
  rows?: number[];        // table: height of each row (their sum <= h; the rest is the table's own border/spacing)
  headerRows?: number;    // leading rows repeated on each following page
  cantSplit?: boolean;    // never split a table between rows
  section?: number;       // section index, handed to bodyH
};
export type Cut = { block: number; row: number };
export type PageSpan = { from: Cut; to: Cut; used: number; section: number; repeat: number };

const MAX_KEEP_CHAIN = 4; // Word keeps chains of keep-with-next paragraphs together; a runaway chain is not honoured past this

/** bodyH(pageIndex, section, pages so far) = usable height of that page (it may depend on titlePg / odd-even headers).
 *  Returns one span per page: [from, to) with `to` exclusive (to.row > 0: the block is split after that many rows);
 *  `used` = height placed, incl. repeated header rows. */
export function paginate(blocks: Block[], bodyH: (page: number, section: number, before: readonly PageSpan[]) => number): PageSpan[] {
  const pages: PageSpan[] = [];
  let cur!: PageSpan, y = 0, has = false, lastMb = 0; // has: something was placed on the current page
  const open = (block: number, row: number, section: number) => { cur = { from: { block, row }, to: { block, row }, used: 0, section, repeat: 0 }; pages.push(cur); y = 0; has = false; lastMb = 0; };
  const close = (block: number, row: number) => { cur.to = { block, row }; cur.used = y; };
  const room = () => bodyH(pages.length - 1, cur.section, pages) - y;
  const next = (block: number, row: number, section: number) => { close(block, row); open(block, row, section); };
  const gap = (b: Block) => (has ? Math.max(lastMb, b.mt ?? 0) : pages.length > 1 ? 0 : b.mt ?? 0);

  for (let i = 0; i < blocks.length; i++) {
    const b = blocks[i], sec = b.section ?? 0;
    if (!pages.length) open(i, 0, sec);
    else if (has && (b.breakBefore || (sec !== cur.section && b.breakBefore !== false))) next(i, 0, sec);
    else if (!has) { cur.section = sec; cur.from = cur.to = { block: i, row: 0 }; }
    // keep-with-next chain starting here moves as a unit when it fits on a fresh page
    let k = i, chain = b.h;
    while (blocks[k].keepNext && !blocks[k].breakAfter && k + 1 < blocks.length && k - i < MAX_KEEP_CHAIN && !blocks[k + 1].breakBefore) { chain += Math.max(blocks[k].mb ?? 0, blocks[k + 1].mt ?? 0) + blocks[k + 1].h; k++; }
    if (k > i && has && gap(b) + chain > room() && chain <= bodyH(pages.length - 1, cur.section, pages)) next(i, 0, sec);

    const rows = b.rows;
    if (rows && !b.cantSplit && gap(b) + b.h > room()) {
      const hdr = Math.min(b.headerRows ?? 0, rows.length - 1), hdrH = rows.slice(0, hdr).reduce((a, c) => a + c, 0);
      const over = b.h - rows.reduce((a, c) => a + c, 0);
      // at least the header rows and one body row must fit, else the whole table starts on a fresh page
      if (has && room() < gap(b) + over + hdrH + rows[hdr]) next(i, 0, sec);
      y += gap(b) + over; has = true;
      let r = 0, onPage = 0;
      while (r < rows.length) {
        if (onPage === 0 || y + rows[r] <= bodyH(pages.length - 1, cur.section, pages)) { y += rows[r++]; onPage++; continue; }
        next(i, r, sec);
        if (r >= hdr) { y = hdrH; cur.repeat = hdr; } // continuation pages repeat the header rows
        has = true; onPage = 0;
      }
    } else {
      if (has && gap(b) + b.h > room()) next(i, 0, sec);
      y += gap(b) + b.h; has = true;
    }
    lastMb = b.mb ?? 0;
    if (b.breakAfter && i + 1 < blocks.length) next(i + 1, 0, blocks[i + 1].section ?? sec);
  }
  if (pages.length) close(blocks.length, 0);
  return pages;
}
