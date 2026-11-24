import { useState } from "react";
import { Link } from "react-router-dom";
import { Me } from "../api";
import { canEdit, folderLabel, LAST_FOLDER, useFolders, useImports, useOverview } from "../data";
import { PageHead } from "../Shell";
import { ago, Empty, Icon, Skeleton } from "../ui";
import { NewItem } from "./NewItem";

export function Dashboard({ me }: { me: Me }) {
  const ov = useOverview(me), folders = useFolders(me), imps = useImports(me);
  const [mk, setMk] = useState<"snippet" | "document" | null>(null);
  const last = localStorage.getItem(LAST_FOLDER(me.org?.id)), lastF = folders.data?.find((f) => f.id === last);
  const edit = canEdit(me);
  const snippets = ov.data?.items.filter((i) => i.kind === "snippet").slice(0, 6) ?? [], docs = ov.data?.items.filter((i) => i.kind === "document").slice(0, 6) ?? [];
  const Row = ({ to, icon, title, sub, when }: { to: string; icon: string; title: string; sub: string; when: string }) =>
    <li><Link to={to} className="row"><Icon n={icon} /><span className="t">{title}</span><small>{sub}</small><small className="when">{when}</small></Link></li>;
  const Card = ({ title, loading, empty, children }: { title: string; loading: boolean; empty: boolean; children: React.ReactNode }) =>
    <section className="card"><h2>{title}</h2>{loading ? <Skeleton rows={3} /> : empty ? <p className="muted">Nothing here yet.</p> : <ul className="list">{children}</ul>}</section>;
  return (
    <div className="page">
      <PageHead crumbs={[{ label: me.org!.name }, { label: "Dashboard" }]} title={`Welcome back, ${me.user.name.split(" ")[0]}`} />
      <div className="quick" aria-label="Quick actions">
        {edit && <button className="btn primary" onClick={() => setMk("snippet")}><Icon n="plus" />New snippet</button>}
        {edit && <button className="btn" onClick={() => setMk("document")}><Icon n="doc" />New document</button>}
        {edit && <Link className="btn" to="/imports"><Icon n="upload" />Import DOCX</Link>}
        {lastF && <Link className="btn" to={`/folders/${lastF.id}`}><Icon n="folder" />Continue in {lastF.name}</Link>}
      </div>
      {ov.data && folders.data?.length === 0 && <Empty icon="folder" title="No folders yet">Create a folder with the “+” above the folder tree, then add snippets and documents to it.</Empty>}
      <div className="grid3">
        <Card title="Recent snippets" loading={ov.isLoading} empty={!snippets.length}>{snippets.map((i) => <Row key={i.id} to={`/snippets/${i.id}`} icon="file" title={i.title} sub={folderLabel(folders.data, i.folder_id)} when={ago(i.created_at)} />)}</Card>
        <Card title="Recent documents" loading={ov.isLoading} empty={!docs.length}>{docs.map((i) => <Row key={i.id} to={`/documents/${i.id}`} icon="doc" title={i.title} sub={folderLabel(folders.data, i.folder_id)} when={ago(i.created_at)} />)}</Card>
        <Card title="Recent imports" loading={imps.isLoading} empty={!imps.data?.length}>{imps.data?.slice(0, 6).map((i) => <Row key={i.id} to={`/imports/${i.id}`} icon="upload" title={i.title} sub={i.filename} when={ago(i.updated_at)} />)}</Card>
      </div>
      {mk && <NewItem me={me} kind={mk} onClose={() => setMk(null)} />}
    </div>
  );
}
