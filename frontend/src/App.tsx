import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AcceptInvite, Login } from "./auth";
import { useMe } from "./data";
import { CommitPage } from "./pages/Commit";
import { Dashboard } from "./pages/Dashboard";
import { FolderPage } from "./pages/Folder";
import { HistoryPage } from "./pages/History";
import { ImportPage, ImportsPage } from "./pages/Imports";
import { Settings } from "./pages/Settings";
import { SnippetPage } from "./pages/Snippet";
import { DocumentPage } from "./pages/Document";
import { Shell } from "./Shell";
import { Skeleton } from "./ui";

export function App() {
  const me = useMe(), loc = useLocation();
  if (me.isLoading) return <div className="authwrap"><div className="authcard"><Skeleton rows={4} /></div></div>;
  const m = me.data;
  return (
    <Routes>
      <Route path="/accept-invite" element={<AcceptInvite me={m} />} />
      <Route path="/login" element={<Login me={m} />} />
      {!m ? <Route path="*" element={<Navigate to="/login" replace state={{ from: loc.pathname + loc.search }} />} /> : (
        <Route element={<Shell me={m} />}>
          <Route index element={<Dashboard me={m} />} />
          <Route path="folders/:id" element={<FolderPage me={m} />} />
          <Route path="snippets/:id" element={<SnippetPage me={m} />} />
          <Route path="documents/:id" element={<DocumentPage me={m} />} />
          <Route path="imports" element={<ImportsPage me={m} />} />
          <Route path="imports/:id" element={<ImportPage me={m} />} />
          <Route path="commit/:folderId" element={<CommitPage me={m} />} />
          <Route path="history/:kind/:id" element={<HistoryPage me={m} />} />
          <Route path="settings/*" element={<Settings me={m} />} />
          <Route path="*" element={<div className="page"><h1>Not found</h1><p>This page does not exist.</p></div>} />
        </Route>)}
    </Routes>
  );
}
