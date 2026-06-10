/**
 * PluginHost — el contrato shell↔plugin (F7; mata el anti-pattern AP-7/F9).
 *
 * Pre-F7 el shell pasaba un "bandejón" hardcodeado de 12 props
 * (`selectedChatId/setSelectedChatId/...` × 5 plugins) a TODOS los Pages:
 * agregar un plugin con estado de selección nuevo = editar `Dashboard.tsx`
 * (violación INV-1). Ahora el shell publica este contexto GENÉRICO:
 *
 *   - `showSidebar` / `showInspector` — chrome global del shell.
 *   - `selection` — mapa `clave → id` que sobrevive cambios de sección.
 *     Cada plugin elige su clave (convención: su propio plugin id) vía
 *     `useSelection("<plugin-id>")`. El shell NO conoce las claves.
 *
 * Un plugin nuevo con selección propia NO toca ningún archivo central:
 * `const [id, setId] = useSelection("mi_plugin")` y listo.
 *
 * Vive en `shared/lib` (capa piso del FSD): importable por plugins y por el
 * shell sin violar ninguna regla de dirección.
 */
import {
  createContext,
  useCallback,
  useContext,
  type ReactNode,
} from "react";

export interface PluginHostState {
  showSidebar: boolean;
  showInspector: boolean;
  /** Selección cross-sección por clave (convención: el plugin id). */
  selection: Readonly<Record<string, string | null>>;
  setSelection: (key: string, id: string | null) => void;
}

const PluginHostContext = createContext<PluginHostState | null>(null);

export function PluginHostProvider({
  value,
  children,
}: {
  value: PluginHostState;
  children: ReactNode;
}) {
  return (
    <PluginHostContext.Provider value={value}>
      {children}
    </PluginHostContext.Provider>
  );
}

export function usePluginHost(): PluginHostState {
  const ctx = useContext(PluginHostContext);
  if (ctx === null) {
    throw new Error(
      "usePluginHost() fuera de <PluginHostProvider> — los Pages de plugin " +
        "solo se montan dentro del shell (Dashboard).",
    );
  }
  return ctx;
}

/**
 * Selección persistente del plugin: `const [id, setId] = useSelection("eta")`.
 * `fallback` se devuelve cuando la clave aún no tiene valor.
 */
export function useSelection(
  key: string,
  fallback: string | null = null,
): [string | null, (id: string | null) => void] {
  const { selection, setSelection } = usePluginHost();
  const set = useCallback(
    (id: string | null) => setSelection(key, id),
    [key, setSelection],
  );
  return [selection[key] ?? fallback, set];
}
