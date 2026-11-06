import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import Root from "./Root";
import "@fontsource/geist-mono/400.css";
import "@fontsource/geist-mono/600.css";
import "./theme.css";
import "./styles.css";

createRoot(document.getElementById("root")!).render(<StrictMode><Root /></StrictMode>);
