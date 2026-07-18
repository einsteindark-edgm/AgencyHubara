import { createRoot } from "react-dom/client";
import "./forge.css";
import { ForgeApp } from "./ForgeApp";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<ForgeApp />);
}
