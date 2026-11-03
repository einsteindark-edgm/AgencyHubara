/**
 * Encabezado de la sección Reseñas: cómo van las calificaciones, cuántas
 * invitaciones salieron y por qué camino, los cupones de agradecimiento y si
 * el envío está prendido. Solo lectura — el modo se cambia por Terraform.
 *
 * También dice por qué NO salen invitaciones (premortem P4/I5): lo que falta
 * configurar («Qué falta»), los pedidos sin invitar y los que esperan, con su
 * motivo, los envíos cortados a mitad y si la tienda web se está
 * actualizando. El cupón es por calificar el pedido en NUESTRO formulario de
 * WhatsApp — nunca por opinar en Google (Google prohíbe premiar reseñas).
 */

import { useId, type ReactNode } from "react";

import {
  MODE_META,
  NOT_INVITED_LABELS,
  Stars,
  TONE_CLASS,
  WAITING_LABELS,
  formatAverage,
  formatTimeBogota,
  reasonLabel,
  reviewErrorDetail,
  useReviewsSummary,
  type ReasonCount,
  type ReviewTone,
  type ReviewsSummary,
} from "@plugins/reviews/frontend/entities/review";

const INT = new Intl.NumberFormat("es-CO", { maximumFractionDigits: 0 });
const fmtN = (n: number) => INT.format(n);

const MISSING_FLOW = "Falta el formulario de Meta (META_FLOW_ID_REVIEW): no se invita a nadie.";
const MISSING_COUPONS =
  "Faltan los cupones GRACIAS5 y GRACIAS10 en Marketing → Cupones, marcados «solo quien lo recibió» y vigentes 60 días más: no se invita a nadie hasta crearlos.";

/** Lo que impide invitar. Con el envío apagado no hay nada que avisar. */
function missingSetup(settings: ReviewsSummary["settings"]): string[] {
  if (settings.mode === "off") return [];
  return [
    ...(settings.flowConfigured ? [] : [MISSING_FLOW]),
    ...(settings.couponsReady ? [] : [MISSING_COUPONS]),
  ];
}

/** Cómo va la actualización de la tienda web (donde salen las reseñas). */
function storefrontLines(st: ReviewsSummary["storefront"]): Array<{ text: string; tone: ReviewTone }> {
  return [
    ...(st.configured
      ? []
      : [{ text: "La tienda web no se reconstruye sola (falta STOREFRONT_DEPLOY_HOOK_URL)", tone: "warn" as const }]),
    ...(st.lastError
      ? [{ text: `Último intento de actualizar la tienda falló: ${st.lastError}`, tone: "danger" as const }]
      : []),
    ...(st.dueAtMs !== null
      ? [{ text: `Actualización de la tienda programada a las ${formatTimeBogota(st.dueAtMs)}`, tone: "info" as const }]
      : []),
  ];
}

function unconfirmedLabel(n: number): string {
  return n === 1
    ? "1 envío sin confirmar (se cortó a mitad; no se reintenta)"
    : `${fmtN(n)} envíos sin confirmar (se cortaron a mitad; no se reintentan)`;
}

export function ReviewSummary() {
  const { data, error, isPending, refetch } = useReviewsSummary();

  if (isPending) {
    return (
      <section aria-label="Resumen de reseñas" className="px-4 py-3">
        <p className="text-[12.5px] text-fg-muted">Cargando el resumen…</p>
      </section>
    );
  }

  if (!data) {
    return (
      <section aria-label="Resumen de reseñas" className="px-4 py-3">
        <div role="alert" className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <p className="text-[12.5px] font-semibold text-danger">No se pudo cargar el resumen.</p>
          <p className="text-[12px] text-fg-muted">{reviewErrorDetail(error)}</p>
          <button
            type="button"
            onClick={() => void refetch()}
            className="rounded-md border border-line-strong px-2.5 py-1 text-[12px] font-semibold text-fg hover:bg-line focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            Reintentar
          </button>
        </div>
      </section>
    );
  }

  return <SummaryView summary={data} />;
}

function SummaryView({ summary }: { summary: ReviewsSummary }) {
  const { requests, reviews, coupons, settings, notInvited, waiting, storefront } = summary;
  const mode = MODE_META[settings.mode];
  const sent = requests.sentFlow + requests.sentTemplate;
  const missing = missingSetup(settings);
  const store = storefrontLines(storefront);

  return (
    <section aria-label="Resumen de reseñas" className="flex flex-col gap-3 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2 text-[12px]">
        <span className="text-fg-muted">Invitaciones:</span>
        <span className={`rounded-full px-2.5 py-0.5 font-semibold ${TONE_CLASS[mode.tone]}`}>
          {mode.label}
        </span>
        {settings.googleUrl ? (
          <a
            href={settings.googleUrl}
            target="_blank"
            rel="noreferrer"
            className="ml-auto text-[11.5px] text-accent-fg underline-offset-2 hover:underline"
          >
            Ver la página para opinar en Google
          </a>
        ) : null}
      </div>

      {missing.length > 0 ? <MissingSetup items={missing} /> : null}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Card title="Calificación">
          {reviews.averageRating === null ? (
            <p className="text-[13px] font-semibold text-fg">Todavía no hay calificaciones</p>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[28px] font-bold leading-none tracking-tight text-fg tabular-nums">
                  {formatAverage(reviews.averageRating)}
                </span>
                <Stars
                  value={reviews.averageRating}
                  size="lg"
                  label={`Promedio: ${formatAverage(reviews.averageRating)} de 5 estrellas`}
                />
              </div>
              <p className="text-[12.5px] text-fg-soft">
                {reviews.total === 1 ? "1 reseña" : `${fmtN(reviews.total)} reseñas`}
              </p>
            </>
          )}
          <p className="text-[11.5px] text-fg-muted">
            {`${fmtN(reviews.withPhoto)} con foto · ${fmtN(reviews.lowRating)} con 1 a 3 estrellas`}
          </p>
        </Card>

        <Card title="Reparto de estrellas">
          <Distribution rows={reviews.distribution} />
        </Card>

        <Card title="Invitaciones">
          <dl className="flex flex-col gap-1">
            <Stat label="Invitaciones enviadas" value={sent} strong />
            <Stat label="Con el formulario (escribió en las últimas 24 h)" value={requests.sentFlow} />
            <Stat label="Con la plantilla (al día siguiente)" value={requests.sentTemplate} />
            <Stat
              label="No se pudieron enviar"
              value={requests.failed}
              tone={requests.failed > 0 ? "text-danger" : undefined}
            />
            <Stat label="No se enviaron (no aplicaba)" value={requests.skipped} />
          </dl>
          {requests.unconfirmed > 0 ? (
            <p className="m-0 text-[11.5px] font-semibold text-warn">{unconfirmedLabel(requests.unconfirmed)}</p>
          ) : null}
        </Card>

        <Card title="Cupones de agradecimiento">
          <dl className="flex flex-col gap-1">
            <Stat label="Cupones dados" value={coupons.granted} strong />
            <Stat label="Cupones usados" value={coupons.used} />
          </dl>
          <p className="m-0 text-[11.5px] text-fg-muted">
            Por calificar el pedido en nuestro formulario de WhatsApp.
          </p>
        </Card>
      </div>

      {notInvited.length > 0 || waiting.length > 0 ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {notInvited.length > 0 ? (
            <Card title="Sin invitar">
              <p className="m-0 text-[11.5px] text-fg-muted">Pedidos que no recibieron la invitación, y por qué.</p>
              <Reasons rows={notInvited} labels={NOT_INVITED_LABELS} />
            </Card>
          ) : null}
          {waiting.length > 0 ? (
            <Card title="En espera">
              <p className="m-0 text-[11.5px] text-fg-muted">Pedidos cuya invitación todavía no sale.</p>
              <Reasons rows={waiting} labels={WAITING_LABELS} />
            </Card>
          ) : null}
        </div>
      ) : null}

      {store.length > 0 ? (
        <ul aria-label="Tienda web" className="m-0 flex list-none flex-col gap-1.5 p-0">
          {store.map((line) => (
            <li key={line.text} className={`rounded-md px-2.5 py-1.5 text-[12px] ${TONE_CLASS[line.tone]}`}>
              {line.text}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

/** Lo que falta configurar para que salgan invitaciones. */
function MissingSetup({ items }: { items: string[] }) {
  const titleId = useId();
  return (
    <div role="alert" aria-labelledby={titleId} className={`rounded-xl px-3 py-2 ${TONE_CLASS.danger}`}>
      <p id={titleId} className="m-0 text-[12.5px] font-semibold">
        Qué falta
      </p>
      <ul className="m-0 mt-1 flex list-disc flex-col gap-0.5 pl-4 text-[12px]">
        {items.map((text) => (
          <li key={text}>{text}</li>
        ))}
      </ul>
    </div>
  );
}

/** Motivo → cuántos pedidos. Un motivo nuevo sale con su nombre tal cual. */
function Reasons({ rows, labels }: { rows: ReasonCount[]; labels: Readonly<Record<string, string>> }) {
  return (
    <dl className="m-0 flex flex-col gap-1">
      {rows.map(({ reason, count }) => (
        <Stat key={reason} label={reasonLabel(labels, reason)} value={count} />
      ))}
    </dl>
  );
}

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-2 rounded-xl border border-line bg-sidebar p-3">
      <h2 className="m-0 text-[11px] font-semibold uppercase tracking-wide text-fg-muted">{title}</h2>
      {children}
    </div>
  );
}

function Stat({
  label,
  value,
  strong = false,
  tone,
}: {
  label: string;
  value: number;
  strong?: boolean;
  tone?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className={`text-[12px] ${strong ? "font-semibold text-fg" : "text-fg-soft"}`}>{label}</dt>
      <dd className={`m-0 text-[12.5px] tabular-nums ${tone ?? (strong ? "font-semibold text-fg" : "text-fg")}`}>
        {fmtN(value)}
      </dd>
    </div>
  );
}

function Distribution({ rows }: { rows: ReviewsSummary["reviews"]["distribution"] }) {
  const total = rows.reduce((acc, r) => acc + r.count, 0);
  return (
    <ul aria-label="Reparto de estrellas" className="m-0 flex list-none flex-col gap-1.5 p-0">
      {rows.map(({ stars, count }) => {
        const pct = total > 0 ? Math.round((count / total) * 100) : 0;
        return (
          <li
            key={stars}
            aria-label={`${stars} ${stars === 1 ? "estrella" : "estrellas"}: ${count}`}
            className="flex items-center gap-2 text-[12px]"
          >
            <span aria-hidden="true" className="w-7 shrink-0 tabular-nums text-fg-soft">
              {stars} <span className="text-yellow">★</span>
            </span>
            <span aria-hidden="true" className="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-line">
              <span className="block h-full rounded-full bg-yellow" style={{ width: `${pct}%` }} />
            </span>
            <span aria-hidden="true" className="w-8 shrink-0 text-right tabular-nums text-fg">
              {fmtN(count)}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
