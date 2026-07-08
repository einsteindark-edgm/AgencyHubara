import { createRoot } from "react-dom/client";
import "../main.css";
import { ExecApp } from "./ExecApp";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<ExecApp />);
}
