/**
 * Tabla de creativos de un segmento (2026-09-10): una fila por anuncio con sus
 * métricas Meta (impresiones/alcance/clics/CTR/CPC/inversión/conversaciones),
 * el embudo WhatsApp del anuncio (chats/ganados/costo por chat) y su PESO
 * sobre la campaña completa (% de impresiones, clics, inversión y chats de la
 * campaña que aporta ese creativo — la pregunta "cómo impacta cada creativo").
 *
 * Click en una fila selecciona el anuncio (scope del canvas + inspector con la
 * vista previa real); click de nuevo lo deselecciona. Campos `null` (sin
 * conexión a Meta) → `<MissingField />`, nunca un 0 falso.
 */

import {
  totalConversations,
  type AdsCampaign,
} from "@plugins/ads/frontend/entities/ads-campaign";

import { fmtMoney, fmtN, fmtPct } from "@plugins/ads/frontend/lib/format";
import { MissingField } from "@plugins/ads/frontend/lib/MissingField";

interface Props {
  /** Anuncios del segmento seleccionado. */
  rows: AdsCampaign[];
  /** Fila de referencia para el peso (la campaña completa). */
  reference: AdsCampaign;
  selectedAdId: string | null;
  onSelect: (adId: string | null) => void;
}

/** `part / whole` si ambos existen y `whole > 0`; si no, null (dato pendiente). */
function share(part: number | null, whole: number | null): number | null {
  return part !== null && whole !== null && whole > 0 ? part / whole : null;
}

function ratio(num: number | null, den: number | null): number | null {
  return num !== null && den !== null && den > 0 ? num / den : null;
}

export function AdsCreativesTable({ rows, reference, selectedAdId, onSelect }: Props) {
  const refChats = totalConversations(reference);

  return (
    <section className="ads-card">
      <header className="ads-card-h">
        <div>
          <h3>Anuncios del segmento</h3>
          <p>
            {fmtN(rows.length)} creativos · clic en una fila para ver su vista
            previa y scopear el tablero · el peso es sobre la campaña completa
          </p>
        </div>
      </header>
      {rows.length === 0 ? (
        <div className="ads-empty" style={{ padding: 16 }}>
          Sin anuncios con actividad en este segmento.
        </div>
      ) : (
        <div className="att-tbl-wrap">
          <table className="att-tbl">
            <thead>
              <tr>
                <th>Creativo</th>
                <th className="num">Impresiones</th>
                <th className="num">Alcance</th>
                <th className="num">Clics</th>
                <th className="num">CTR</th>
                <th className="num">CPC</th>
                <th className="num">Inversión</th>
                <th className="num" title="Conversaciones iniciadas según Meta">Conv. Meta</th>
                <th className="num">Chats</th>
                <th className="num">Ganados</th>
                <th className="num">Costo/chat</th>
                <th className="num" title="Peso sobre la campaña completa">% Impr.</th>
                <th className="num" title="Peso sobre la campaña completa">% Clics</th>
                <th className="num" title="Peso sobre la campaña completa">% Inv.</th>
                <th className="num" title="Peso sobre la campaña completa">% Chats</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const chats = totalConversations(r);
                const won = r.conversations?.ganado ?? null;
                const ctr = ratio(r.clicks, r.impressions);
                const cpc = ratio(r.spend, r.clicks);
                const costPerChat = chats > 0 && r.spend !== null ? r.spend / chats : null;
                const sel = selectedAdId === r.id;
                return (
                  <tr
                    key={r.id}
                    tabIndex={0}
                    onClick={() => onSelect(sel ? null : r.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onSelect(sel ? null : r.id);
                      }
                    }}
                    aria-selected={sel}
                    style={{
                      cursor: "pointer",
                      background: sel ? "rgba(10,132,255,0.12)" : undefined,
                    }}
                  >
                    <td>
                      <div className="att-cust">
                        {r.creativeThumbnailUrl ? (
                          <img
                            src={r.creativeThumbnailUrl}
                            alt=""
                            width={32}
                            height={32}
                            style={{ borderRadius: 6, objectFit: "cover", flexShrink: 0 }}
                          />
                        ) : (
                          <span
                            aria-hidden
                            style={{
                              width: 32,
                              height: 32,
                              borderRadius: 6,
                              background: "rgba(255,255,255,0.06)",
                              flexShrink: 0,
                            }}
                          />
                        )}
                        <div>
                          <div className="att-n">{r.name ?? <MissingField />}</div>
                          <div className="att-id">{r.creativeTitle ?? r.id}</div>
                        </div>
                      </div>
                    </td>
                    <td className="num">{cell(r.impressions, fmtN)}</td>
                    <td className="num">{cell(r.reach, fmtN)}</td>
                    <td className="num">{cell(r.clicks, fmtN)}</td>
                    <td className="num">{cell(ctr, (v) => fmtPct(v, 2))}</td>
                    <td className="num">{cell(cpc, (v) => fmtMoney(Math.round(v)))}</td>
                    <td className="num">{cell(r.spend, fmtMoney)}</td>
                    <td className="num">{cell(r.conversationsStarted, fmtN)}</td>
                    <td className="num">{fmtN(chats)}</td>
                    <td className="num">{won !== null ? fmtN(won) : <MissingField />}</td>
                    <td className="num">{cell(costPerChat, (v) => fmtMoney(Math.round(v)))}</td>
                    <td className="num">{cell(share(r.impressions, reference.impressions), fmtPct)}</td>
                    <td className="num">{cell(share(r.clicks, reference.clicks), fmtPct)}</td>
                    <td className="num">{cell(share(r.spend, reference.spend), fmtPct)}</td>
                    <td className="num">{cell(share(chats, refChats), fmtPct)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function cell(v: number | null, fmt: (n: number) => string) {
  return v !== null ? fmt(v) : <MissingField />;
}
