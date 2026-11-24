import { KeyboardEvent, PointerEvent, useRef } from "react";

/** Vertical drag handle. dir = +1 when moving right grows the pane (library), -1 for the right panel. Pointer capture keeps the drag over iframes. */
export function Resizer({ label, value, min, max, dir, onChange, onReset, testid }: { label: string; value: number; min: number; max: number; dir: 1 | -1; onChange: (v: number) => void; onReset: () => void; testid: string }) {
  const start = useRef<{ x: number; v: number } | null>(null);
  const set = (v: number) => onChange(Math.max(min, Math.min(max, Math.round(v))));
  const key = (e: KeyboardEvent) => {
    if (e.key === "Home") onReset();
    else if (e.key === "ArrowLeft" || e.key === "ArrowRight") set(value + (e.key === "ArrowRight" ? 16 : -16) * dir);
    else return;
    e.preventDefault();
  };
  return <div className="resizer" role="separator" aria-orientation="vertical" aria-label={label} aria-valuenow={Math.round(value)} aria-valuemin={min} aria-valuemax={max} tabIndex={0} data-testid={testid}
    onPointerDown={(e: PointerEvent) => { if (e.button) return; e.currentTarget.setPointerCapture(e.pointerId); start.current = { x: e.clientX, v: value }; e.preventDefault(); }}
    onPointerMove={(e) => start.current && set(start.current.v + (e.clientX - start.current.x) * dir)}
    onPointerUp={() => { start.current = null; }} onPointerCancel={() => { start.current = null; }}
    onDoubleClick={onReset} onKeyDown={key} />;
}
