import { expect, test } from "vitest";
import { ooxmlToHtml } from "./ooxml";

const NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"';
const obj = `<w:p ${NS}><w:r><w:object><v:shape style="width:75pt;height:30pt" o:ole=""><v:imagedata r:id="rId9"/></v:shape><o:OLEObject Type="Embed" ProgID="Word.Document.12" r:id="rId10"/></w:object></w:r></w:p>`;

test("embedded object draws its preview picture at the VML size", () => {
  const h = ooxmlToHtml(obj, { rId9: "data:image/png;base64,AAAA" });
  expect(h).toContain('<img class="oleobj" src="data:image/png;base64,AAAA"');
  expect(h).toContain("width:100px;height:40px");
});

test("embedded object without a raster preview is a labelled box, never a chip or placeholder text", () => {
  const h = ooxmlToHtml(obj, {});
  expect(h).toContain("oleobj-box");
  expect(h).toContain("Embedded document (Word.Document.12)");
  expect(h).not.toContain("removed");
});
