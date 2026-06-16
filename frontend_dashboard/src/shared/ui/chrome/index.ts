// TitleBar fake (semáforos macOS decorativos) se eliminó en F2.4: en desktop
// conviven las decoraciones NATIVAS de la ventana — el bar interno duplicaba
// chrome y sus controles no funcionaban. Si algún día se quiere chrome custom,
// va con `decorations: false` + `data-tauri-drag-region` + window controls
// reales (@tauri-apps/api/window).
export { Toolbar, type SectionKey } from "./Toolbar";
export { StatusBar } from "./StatusBar";
