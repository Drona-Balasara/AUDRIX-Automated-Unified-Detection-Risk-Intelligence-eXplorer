import "./Header.css";

interface HeaderProps {
  environment?: string;
}

/**
 * Application masthead: product name, descriptor, and (when known) the backend
 * environment. Uses semantic <header> and a single H1 for the product name.
 */
export function Header({ environment }: HeaderProps) {
  return (
    <header className="app-header">
      <div className="app-header__brand">
        <h1 className="app-header__name">AUDRIX</h1>
        <p className="app-header__descriptor">
          Automated Unified Detection &amp; Risk Intelligence eXplorer
        </p>
      </div>
      {environment ? (
        <span className="app-header__env" title="Backend environment">
          {environment}
        </span>
      ) : null}
    </header>
  );
}
