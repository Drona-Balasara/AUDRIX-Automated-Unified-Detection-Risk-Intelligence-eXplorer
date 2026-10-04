import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";

import "./AppShell.css";

const NAV_ITEMS = [
  { to: "/", label: "Command Center", end: true },
  { to: "/entities", label: "Entities", end: false },
  { to: "/findings", label: "Findings", end: false },
  { to: "/queue", label: "Review Queue", end: false },
];

interface AppShellProps {
  children: ReactNode;
}

/**
 * Application shell: masthead with product identity, tab-style navigation,
 * main content area, and footer. The design is restrained and information-
 * first — no marketing language, no decorative elements.
 */
export function AppShell({ children }: AppShellProps) {
  return (
    <div className="app-shell">
      <header className="app-header" role="banner">
        <div className="app-header__brand">
          <span className="app-header__name">SAT-SA</span>
          <span className="app-header__descriptor">
            Security Assessment &amp; Supervisory Analytics
          </span>
        </div>
      </header>

      <nav className="app-nav" aria-label="Primary navigation">
        <ul className="app-nav__list" role="list">
          {NAV_ITEMS.map(({ to, label, end }) => (
            <li key={to}>
              <NavLink
                to={to}
                end={end}
                className={({ isActive }) =>
                  `app-nav__link${isActive ? " app-nav__link--active" : ""}`
                }
              >
                {label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>

      <main className="app-shell__main">{children}</main>

      <footer className="app-shell__footer">
        SAT-SA — Security Assessment &amp; Supervisory Analytics
      </footer>
    </div>
  );
}
