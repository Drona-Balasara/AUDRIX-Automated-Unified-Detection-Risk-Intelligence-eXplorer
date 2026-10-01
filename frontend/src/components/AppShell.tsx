import type { ReactNode } from "react";

import "./AppShell.css";
import { Header } from "./Header";

interface AppShellProps {
  environment?: string;
  children: ReactNode;
}

/** Page scaffold: masthead, a constrained main content column, and a footer. */
export function AppShell({ environment, children }: AppShellProps) {
  return (
    <div className="app-shell">
      <Header environment={environment} />
      <main className="app-shell__main">{children}</main>
      <footer className="app-shell__footer">
        SAT-SA — Phase 1: Project Foundation
      </footer>
    </div>
  );
}
