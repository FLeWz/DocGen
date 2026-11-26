// Editor view preferences. First-time default (and anything unreadable) is: no preview, 100% zoom.
export type PvMode = "live" | "word" | "off";
export const ZOOMS = [0.4, 0.45, 0.5, 0.6, 0.75, 1, 1.5];
export const PV_KEY = "docgen.editor.v2.preview", ZOOM_KEY = "docgen.editor.v2.zoom"; // v2: pre-round-18 values are ignored;
export function loadPrefs(s: Pick<Storage, "getItem">): { pv: PvMode; zoom: number } {
  const pv = s.getItem(PV_KEY), zoom = Number(s.getItem(ZOOM_KEY));
  return { pv: pv === "live" || pv === "word" ? pv : "off", zoom: ZOOMS.includes(zoom) ? zoom : 1 };
}
