/**
 * Tabla de conversaciones de WhatsApp originadas por una campaña. Cada fila
 * representa un contacto, su ciudad, timing, agente asignado, estado y valor
 * (si llegó a "ganado"). Filtrable por estado vía pills sobre la cabecera.
 */

import {
  ADS_STATES,
  type AttributedConversation,
} from "@/entities/ads-campaign";
import { Avatar } from "@/shared/ui";

import { fmtMoney, fmtN } from "@plugins/ads/frontend/lib/format";

import {
  ATTRIBUTED_STATE_FILTERS,
  useStateFilter,
} from "../model/useStateFilter";

interface Props {
  rows: AttributedConversation[];
}

export function AdsAttributedTable({ rows }: Props) {
  const { filter, setFilter, list } = useStateFilter(rows);

  return (
    <section className="ads-card">
      <header className="ads-card-h">
        <div>
          <h3>Conversaciones atribuidas</h3>
          <p>
            {fmtN(rows.length)} chats originados por el anuncio · clic en una
            fila para abrir el chat
          </p>
        </div>
        <div className="att-filter">
          {ATTRIBUTED_STATE_FILTERS.map((f) => (
            <button
              key={f}
              className={"af-pill" + (filter === f ? " on" : "")}
              onClick={() => setFilter(f)}
            >
              {f !== "all" && (
                <span
                  className="af-dot"
                  style={{ background: ADS_STATES[f].color }}
                />
              )}
              {f === "all" ? "Todas" : ADS_STATES[f].label}
            </button>
          ))}
        </div>
      </header>
      <div className="att-tbl-wrap">
        <table className="att-tbl">
          <thead>
            <tr>
              <th>Contacto</th>
              <th>Ciudad</th>
              <th>Iniciado</th>
              <th>Último msg</th>
              <th className="num">Msgs</th>
              <th>Agente</th>
              <th>Estado</th>
              <th className="num">Valor</th>
            </tr>
          </thead>
          <tbody>
            {list.map((c) => {
              const meta = ADS_STATES[c.state];
              return (
                <tr key={c.id}>
                  <td>
                    <div className="att-cust">
                      <Avatar initials={c.short} color={c.color} size={26} />
                      <div>
                        <div className="att-n">{c.name}</div>
                        <div className="att-id">{c.id}</div>
                      </div>
                    </div>
                  </td>
                  <td>{c.city}</td>
                  <td>{c.started}</td>
                  <td>{c.lastMsg}</td>
                  <td className="num">{c.msgs}</td>
                  <td>{c.agent}</td>
                  <td>
                    <span
                      className="att-state"
                      style={{ background: meta.bg, color: meta.color }}
                    >
                      <span
                        className="att-dot"
                        style={{ background: meta.color }}
                      />
                      {meta.label}
                    </span>
                  </td>
                  <td className="num">
                    {c.value > 0 ? (
                      fmtMoney(c.value)
                    ) : (
                      <span style={{ color: "var(--fg-faint)" }}>—</span>
                    )}
                  </td>
                </tr>
              );
            })}
            {list.length === 0 && (
              <tr>
                <td colSpan={8} className="att-empty">
                  Sin chats que coincidan con el filtro.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
