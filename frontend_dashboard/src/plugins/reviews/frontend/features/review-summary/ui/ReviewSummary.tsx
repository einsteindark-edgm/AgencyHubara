/**
 * Encabezado de la sección Reseñas: cómo van las calificaciones, cuántas
 * invitaciones salieron y por qué camino, los cupones de agradecimiento y si
 * el envío está prendido. Solo lectura — el modo se cambia por Terraform.
 */

import type { ReactNode } from "react";

import {
  MODE_META,
  Stars,
  TONE_CLASS,
  formatAverage,
  reviewErrorDetail,
  useReviewsSummary,
  type ReviewsSummary,
} from "@plugins/reviews/frontend/entities/review";

const INT = new Intl.NumberFormat("es-CO", { maximumFractionDigits: 0 });
const fmtN = (n: number) => INT.format(n);

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
  const { requests, reviews, coupons, settings } = summary;
  const mode = MODE_META[settings.mode];
  const sent = requests.sentFlow + requests.sentTemplate;

  return (
    <section aria-label="Resumen de reseñas" className="flex flex-col gap-3 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2 text-[12px]">
        <span className="text-fg-muted">Invitaciones:</span>
        <span className={`rounded-full px-2.5 py-0.5 font-semibold ${TONE_CLASS[mode.tone]}`}>
          {mode.label}
        </span>
      </div>

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
        </Card>

        <Card title="Cupones de agradecimiento">
          <dl className="flex flex-col gap-1">
            <Stat label="Cupones dados" value={coupons.granted} strong />
            <Stat label="Cupones usados" value={coupons.used} />
          </dl>
          {settings.googleUrl ? (
            <a
              href={settings.googleUrl}
              target="_blank"
              rel="noreferrer"
              className="mt-1 text-[11.5px] text-accent-fg underline-offset-2 hover:underline"
            >
              Ver la página para opinar en Google
            </a>
          ) : null}
        </Card>
      </div>
    </section>
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
