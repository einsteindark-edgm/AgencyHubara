/**
 * Barras apiladas por día (últimos 14): cada columna representa los chats
 * iniciados ese día, segmentados por su estado final. Apilado en orden
 * `ADS_STATE_ORDER.reverse()` para que "ganado" aparezca arriba.
 */

import {
  ADS_STATES,
  ADS_STATE_ORDER,
  type AdsDailyPoint,
} from "@plugins/ads/frontend/entities/ads-campaign";

import { fmtN } from "@plugins/ads/frontend/lib/format";

interface Props {
  series: AdsDailyPoint[];
}

function pointTotal(p: AdsDailyPoint): number {
  return ADS_STATE_ORDER.reduce((acc, s) => acc + (p[s] || 0), 0);
}

export function AdsDailyTrend({ series }: Props) {
  const max = Math.max(1, ...series.map(pointTotal));
  const grandTotal = series.reduce((acc, d) => acc + pointTotal(d), 0);

  return (
    <section className="ads-card">
      <header className="ads-card-h">
        <div>
          <h3>Conversaciones por día</h3>
          <p>Últimos {series.length} días · apiladas por estado final</p>
        </div>
        <span className="ads-card-meta">{fmtN(grandTotal)} chats</span>
      </header>
      <div className="trend">
        <div className="trend-bars">
          {series.map((d) => {
            const total = pointTotal(d);
            const h = (total / max) * 100;
            return (
              <div key={d.d} className="trend-col" title={`${d.d}: ${total} chats`}>
                <div className="trend-bar" style={{ height: h + "%" }}>
                  {ADS_STATE_ORDER.slice().reverse().map((s) => {
                    const v = d[s] || 0;
                    if (!v) return null;
                    const segh = total > 0 ? (v / total) * 100 : 0;
                    return (
                      <span
                        key={s}
                        style={{
                          height: segh + "%",
                          background: ADS_STATES[s].color,
                        }}
                      />
                    );
                  })}
                </div>
                <div className="trend-lbl">{d.d.split(" ")[0]}</div>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
