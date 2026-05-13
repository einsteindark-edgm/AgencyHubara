/**
 * Composer del chat con 2 modos:
 *   1) Bot gestionando — banner + botón rojo "Intervenir".
 *   2) Intervenido — textarea activo + barra de herramientas + "Devolver al bot".
 *
 * El estado es local porque sólo afecta a este composer.
 */

import { useState } from "react";
import { Icon } from "@/shared/ui";

export function ChatsComposer() {
  const [intervened, setIntervened] = useState(false);

  if (!intervened) {
    return (
      <div className="composer composer-bot">
        <div className="bot-state">
          <span className="bot-pulse" />
          <div className="bs-body">
            <div className="bs-t">
              Bot <b>remarketing</b> está gestionando esta conversación
            </div>
            <div className="bs-s">
              Si necesitas tomar el control, intervenir pausa al bot y te asigna
              la conversación.
            </div>
          </div>
          <button className="interv-btn" onClick={() => setIntervened(true)}>
            <Icon.user />
            <span>Intervenir</span>
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="composer">
      <div className="composer-tools">
        <span className="interv-on">
          <Icon.user />
          Intervenido por ti · Bot pausado
        </span>
        <span className="tb-sep" />
        <button className="tb-btn" title="Plantillas">
          <Icon.template />
        </button>
        <button className="tb-btn" title="Adjuntar">
          <Icon.attach />
        </button>
        <button className="tb-btn" title="Audio">
          <Icon.mic />
        </button>
        <span className="tb-sep" />
        <button className="tb-btn" title="Asistente IA">
          <Icon.wand />
        </button>
        <button className="tb-btn" title="Emoji">
          <Icon.emoji />
        </button>
        <span className="right">
          <button
            className="interv-off"
            onClick={() => setIntervened(false)}
          >
            Devolver al bot
          </button>
          <kbd>⌘↩</kbd>
        </span>
      </div>
      <div className="composer-row">
        <textarea
          placeholder="Escribe un mensaje al cliente…"
          rows={1}
          autoFocus
        />
        <button className="send-btn">
          <Icon.send />
        </button>
      </div>
    </div>
  );
}
