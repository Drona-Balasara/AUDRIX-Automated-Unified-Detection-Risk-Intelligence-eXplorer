import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import "./styles/global.css";

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("Root element #root was not found in the document.");
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
