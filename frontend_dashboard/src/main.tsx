import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { initWebTracing } from "@/app/observability/otel";
import { Dashboard } from "@/pages/Dashboard";
import { MobileChatsApp } from "@/pages/MobileChats";
import { AppProviders } from "@/app/providers";
import { IS_MOBILE_APP } from "@/shared/lib";

// OTel web (Tier 2): instrumenta fetch/XHR → traceparent al backend. Antes del render.
initWebTracing();

// En el build de la app móvil (VITE_MOBILE_APP=1) arrancamos SOLO la sección de
// chats — sin el shell de escritorio. NO depende del ancho de pantalla: el web
// de escritorio siempre monta el Dashboard aunque la ventana esté angosta.
// Mismo árbol de providers (auth, SSE, query) para ambos.
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppProviders>
      {IS_MOBILE_APP ? <MobileChatsApp /> : <Dashboard />}
    </AppProviders>
  </StrictMode>,
);
