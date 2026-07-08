import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./main.css";

const container = document.getElementById("root");
if (!container) {
  throw new Error("falta #root en el HTML del panel");
}
createRoot(container).render(<App />);
