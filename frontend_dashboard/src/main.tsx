import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { initWebTracing } from "@/app/observability/otel";
import { Dashboard } from "@/pages/Dashboard";
import { MobileChatsApp } from "@/pages/MobileChats";
import { AppProviders } from "@/app/providers";
import { IS_MOBILE } from "@/shared/lib";

// OTel web (Tier 2): instrumenta fetch/XHR → traceparent al backend. Antes del render.
initWebTracing();

// En teléfono (app Android Tauri o viewport angosto) arrancamos SOLO la sección
// de chats — sin el shell macOS de escritorio. Mismo árbol de providers (auth,
// SSE, query) para ambos.
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppProviders>
      {IS_MOBILE ? <MobileChatsApp /> : <Dashboard />}
    </AppProviders>
  </StrictMode>,
);
