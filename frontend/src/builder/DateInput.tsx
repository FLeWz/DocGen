import { useEffect, useState } from "react";
import { formatDate, parseDateInput } from "../dates";

/** Text field for a date: shows/accepts DD.MM.YYYY (ISO also accepted), reports the ISO day, inline error when not a real date. */
export function DateInput({ value, onChange, label, placeholder, disabled }: { value: unknown; onChange: (iso: string | null) => void; label: string; placeholder?: string; disabled?: boolean }) {
  const iso = value == null ? null : String(value);
  const [text, setText] = useState(formatDate(iso));
  useEffect(() => { if (parseDateInput(text).iso !== (iso ? parseDateInput(formatDate(iso)).iso : null)) setText(formatDate(iso)); }, [iso]); // eslint-disable-line react-hooks/exhaustive-deps
  const err = parseDateInput(text).error;
  return (
    <span className="dateinput">
      <input aria-label={label} inputMode="numeric" maxLength={10} placeholder={placeholder ?? "DD.MM.YYYY"} disabled={disabled} value={text} aria-invalid={!!err}
        onChange={(e) => { setText(e.target.value); const r = parseDateInput(e.target.value); if (!r.error) onChange(r.iso); }} />
      {err && <small className="err" role="alert">{err}</small>}
    </span>
  );
}
