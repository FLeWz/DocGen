import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, VarCandidate } from "../api";
import { Banner, Skeleton } from "../ui";
import { Pick, validatePicks } from "./picks";

const LOC = { cover: "Cover / front matter", header: "Header", footer: "Footer", body: "Body" } as Record<string, string>;

/** The candidates found by the server (`GET url`): include toggle, location, detected value and an editable variable name. */
export function Candidates({ url, onPicks, dataKey = "candidates" }: { url: string; onPicks: (p: { picks: Pick[]; error: string }) => void; dataKey?: string }) {
  const q = useQuery({ queryKey: ["find-vars", url], gcTime: 0, queryFn: () => api<VarCandidate[] | Record<string, VarCandidate[]>>(url) });
  const [rows, setRows] = useState<(VarCandidate & { include: boolean })[]>([]);
  useEffect(() => { const d = q.data; if (d) setRows(((Array.isArray(d) ? d : d[dataKey]) ?? []).map((c) => ({ ...c }))); }, [q.data, dataKey]);
  useEffect(() => { onPicks(validatePicks(rows)); }, [rows]); // eslint-disable-line react-hooks/exhaustive-deps
  if (q.isLoading) return <Skeleton rows={3} />;
  if (!rows.length) return <Banner kind="info">No per-document values found (cover text, header/footer fields, dates, version numbers).</Banner>;
  const set = (id: string, p: Partial<VarCandidate & { include: boolean }>) => setRows(rows.map((r) => (r.id === id ? { ...r, ...p } : r)));
  return (
    <table className="tbl compact" data-testid="var-candidates">
      <thead><tr><th>Use</th><th>Where</th><th>Detected value</th><th>Variable name</th></tr></thead>
      <tbody>{rows.map((r) => (
        <tr key={r.id}>
          <td><input type="checkbox" aria-label={`Use ${r.value}`} checked={r.include} onChange={(e) => set(r.id, { include: e.target.checked })} /></td>
          <td className="nowrap">{Object.entries(r.locs).map(([k, n]) => `${LOC[k] ?? k}${n > 1 ? ` ×${n}` : ""}`).join(", ")}</td>
          <td><code>{r.value}</code>{r.display && r.display !== r.value && <small className="muted"> → {r.display}</small>}</td>
          <td><input aria-label={`Name for ${r.value}`} value={r.name} disabled={!r.include} onChange={(e) => set(r.id, { name: e.target.value })} /></td>
        </tr>))}</tbody>
    </table>
  );
}
