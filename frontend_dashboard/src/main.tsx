import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { initWebTracing } from "@/app/observability/otel";
import { Dashboard } from "@/pages/Dashboard";
import { AppProviders } from "@/app/providers";

// OTel web (Tier 2): instrumenta fetch/XHR → traceparent al backend. Antes del render.
initWebTracing();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppProviders>
      <Dashboard />
    </AppProviders>
  </StrictMode>,
);
