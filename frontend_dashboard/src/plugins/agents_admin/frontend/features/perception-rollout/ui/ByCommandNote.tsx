/**
 * El bot nuevo se cambia SOLO por comando (decisión del operador, 2026-10-06:
 * «para evitar que alguien jugando dañe producción»). Los paneles muestran el
 * estado; esta nota dice cómo se cambia.
 */

export const CONTROL_COMMAND = "python -m src.plugins.chats.agent.sales.decisions.control";

export function ByCommandNote({ example }: { example: string }) {
  return (
    <div style={{ fontSize: 11, color: "var(--fg-mute)", marginTop: 10 }}>
      <div>Se cambia por comando, no desde aquí, para que nadie lo mueva por error en producción:</div>
      <code
        className="mono"
        style={{ display: "block", marginTop: 4, whiteSpace: "pre-wrap", wordBreak: "break-all", color: "var(--fg-soft)" }}
      >
        {`${CONTROL_COMMAND} ${example}`}
      </code>
      <div style={{ marginTop: 4 }}>{`Desde una máquina del equipo: infra/scripts/bot_control.sh ${example}`}</div>
    </div>
  );
}
