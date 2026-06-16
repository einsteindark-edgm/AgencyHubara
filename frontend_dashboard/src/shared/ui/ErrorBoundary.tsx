/**
 * ErrorBoundary genérico del shell (auditoría 2026-06-10, F2.1).
 *
 * Antes NO había ningún boundary: un throw en el render de cualquier plugin
 * (p.ej. un Zod parse propagado) tumbaba la app entera a pantalla blanca.
 * Usos:
 *   - `Dashboard` envuelve la Page activa con `scope={section}` — el crash de
 *     un plugin deja el toolbar/shell vivos y ofrece reintentar.
 *   - `AppProviders` envuelve todo con `scope="app"` — última red.
 *
 * Class component a propósito: React (19 incluido) solo expone
 * getDerivedStateFromError/componentDidCatch en clases.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";
import { MacButton } from "./Button";

interface Props {
  /** Identidad del scope protegido (plugin id o "app") — visible en el fallback. */
  scope: string;
  /** Cuando cambia (ej. cambio de sección), un boundary roto se auto-resetea. */
  resetKey?: string;
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // El span/log estructurado llega con F1 (connectionState + telemetría);
    // por ahora console.error es la señal mínima para el dev.
    console.error(
      `[error-boundary:${this.props.scope}] render crash`,
      error,
      info.componentStack,
    );
  }

  componentDidUpdate(prev: Props) {
    if (prev.resetKey !== this.props.resetKey && this.state.error !== null) {
      this.setState({ error: null });
    }
  }

  render() {
    if (this.state.error !== null) {
      return (
        <div
          role="alert"
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: 10,
            padding: 32,
            textAlign: "center",
          }}
        >
          <div style={{ fontSize: 13, fontWeight: 600 }}>
            La sección «{this.props.scope}» falló al renderizar
          </div>
          <div
            style={{
              fontSize: 11,
              color: "var(--fg-muted)",
              maxWidth: 480,
              fontFamily: "var(--font-mono)",
            }}
          >
            {this.state.error.message}
          </div>
          <MacButton sm onClick={() => this.setState({ error: null })}>
            Reintentar
          </MacButton>
        </div>
      );
    }
    return this.props.children;
  }
}
