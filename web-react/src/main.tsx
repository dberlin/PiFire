import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./components/App";
import "./theme.css";
import "./components/dashboard/dashboard.css";
import "./components/settings/settings.css";
import "./components/cookfiles/cookfiles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
