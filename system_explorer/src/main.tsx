import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "./styles/index.css";
import "@xyflow/react/dist/style.css";
import { App } from "./App";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // El grafo cambia solo cuando alguien edita un manifest — refresh ocasional
      // es suficiente. Si querés tiempo real, agregar un poller manual.
      staleTime: 30 * 1000,
      refetchOnWindowFocus: false,
      retry: 2,
    },
  },
});

const root = document.getElementById("root");
if (!root) throw new Error("root element missing");

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
