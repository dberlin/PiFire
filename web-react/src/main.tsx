import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// Self-hosted Barlow, bundled by rsbuild -- was loaded from fonts.googleapis.com
// (index.html), which an offline Pi cannot reach: the UI silently fell back to
// system-ui and the screenshot fidelity gates saw the wrong glyphs. Weights match
// the old <link>: Barlow 400/500/600/700, Barlow Semi Condensed 500/600/700/800.
import "@fontsource/barlow/400.css";
import "@fontsource/barlow/500.css";
import "@fontsource/barlow/600.css";
import "@fontsource/barlow/700.css";
import "@fontsource/barlow-semi-condensed/500.css";
import "@fontsource/barlow-semi-condensed/600.css";
import "@fontsource/barlow-semi-condensed/700.css";
import "@fontsource/barlow-semi-condensed/800.css";
import App from "./components/App";
import "./theme.css";
import "./components/dashboard/dashboard.css";
import "./components/settings/settings.css";
import "./components/cookfiles/cookfiles.css";
import "./components/recipes/recipes.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
