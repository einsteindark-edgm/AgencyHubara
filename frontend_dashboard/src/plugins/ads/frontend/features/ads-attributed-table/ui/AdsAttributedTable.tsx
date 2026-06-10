/**
 * Tabla de conversaciones de WhatsApp originadas por una campaña. Cada fila
 * representa un contacto, su ciudad, timing, agente asignado, estado y valor
 * (si llegó a "ganado"). Filtrable por estado vía pills sobre la cabecera.
 *
 * Tolera campos `null` que devuelve el backend (name, city, agent, state,
 * value) — para esos slots renderiza un `<MissingField />` con tooltip
 * explicativo. Cuando se integren las fuentes upstream (CRM, clasificador,
 * orders) los nulls van desapareciendo sin tocar este componente.
 */

import {
  ADS_STATES,
  type AttributedConversation,
} from "@plugins/ads/frontend/entities/ads-campaign";
import { Avatar } from "@/shared/ui";

import { fmtMoney, fmtN, fmtUsd } from "@plugins/ads/frontend/lib/format";
import { MissingField } from "@plugins/ads/frontend/lib/MissingField";

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
              <th className="num">Costo LLM</th>
            </tr>
          </thead>
          <tbody>
            {list.map((c) => {
              const meta = c.state ? ADS_STATES[c.state] : null;
              return (
                <tr key={c.id}>
                  <td>
                    <div className="att-cust">
                      <Avatar initials={c.short} color={c.color} size={26} />
                      <div>
                        <div className="att-n">
                          {c.name ?? <MissingField />}
                        </div>
                        <div className="att-id">{c.id}</div>
                      </div>
                    </div>
                  </td>
                  <td>{c.city ?? <MissingField />}</td>
                  <td>{c.started}</td>
                  <td>{c.lastMsg ?? <MissingField />}</td>
                  <td className="num">{c.msgs}</td>
                  <td>{c.agent ?? <MissingField />}</td>
                  <td>
                    {meta ? (
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
                    ) : (
                      <MissingField withIcon />
                    )}
                  </td>
                  <td className="num">
                    {c.value !== null && c.value > 0 ? (
                      fmtMoney(c.value)
                    ) : c.value === null ? (
                      <MissingField />
                    ) : (
                      <span style={{ color: "var(--fg-faint)" }}>—</span>
                    )}
                  </td>
                  <td className="num">
                    {c.llmCostUsd != null ? (
                      <div>
                        <div>{fmtUsd(c.llmCostUsd)}</div>
                        {c.llmTokens != null && (
                          <div
                            style={{ fontSize: 11, color: "var(--fg-mute)" }}
                          >
                            {fmtN(c.llmTokens)} tok
                          </div>
                        )}
                      </div>
                    ) : (
                      <MissingField />
                    )}
                  </td>
                </tr>
              );
            })}
            {list.length === 0 && (
              <tr>
                <td colSpan={9} className="att-empty">
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
