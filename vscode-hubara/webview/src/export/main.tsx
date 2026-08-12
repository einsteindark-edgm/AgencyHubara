import { createRoot } from "react-dom/client";
import "./export.css";
import { ExportApp } from "./ExportApp";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<ExportApp />);
}
