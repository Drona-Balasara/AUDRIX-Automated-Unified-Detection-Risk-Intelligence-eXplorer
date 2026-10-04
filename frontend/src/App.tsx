import { BrowserRouter, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/AppShell";
import { CommandCenter } from "./pages/CommandCenter";
import { EntityListPage, EntityDetailPage } from "./pages/EntityAssessment";
import { FindingExplorer } from "./pages/FindingExplorer";
import { FindingDetail } from "./pages/FindingDetail";
import { ReviewQueue } from "./pages/ReviewQueue";

/**
 * Application root. Wires React Router to the AppShell and maps URL paths
 * to the five primary dashboard areas. Authentication is out of scope for V1.
 */
export default function App() {
  return (
    <BrowserRouter>
      <AppShell>
        <Routes>
          <Route path="/" element={<CommandCenter />} />
          <Route path="/entities" element={<EntityListPage />} />
          <Route path="/entities/:entityId" element={<EntityDetailPage />} />
          <Route path="/findings" element={<FindingExplorer />} />
          {/* {finding_key} may contain slashes — use wildcard route */}
          <Route path="/findings/*" element={<FindingDetail />} />
          <Route path="/queue" element={<ReviewQueue />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  );
}

function NotFound() {
  return (
    <div style={{ padding: "var(--space-6)" }}>
      <h2>Page not found</h2>
      <p>The requested page does not exist.</p>
    </div>
  );
}
