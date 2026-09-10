/**
 * Calendario de la bandeja: filtra las conversaciones por día o por rango.
 *
 * Arranca COLAPSADO — la sidebar mide 280px y lo que el operador vino a ver es
 * la lista, no un mes entero. El disparador muestra el rango vigente ("Hoy",
 * "Ayer", "1 de septiembre de 2026 – 10 de septiembre de 2026") para que el
 * estado del filtro sea legible sin abrir nada; ese es el fallo clásico de un
 * filtro plegable: la lista se ve corta y nadie sabe por qué.
 *
 * Todo el estado de aquí es UI pura (abierto/cerrado, mes en pantalla) y vive
 * local (regla 3). El rango elegido es del dueño — `useInboxFilters` —, así que
 * viaja por props.
 */

import { useState } from "react";
import { Icon } from "@/shared/ui";
import { formatIsoDateEs, shiftIsoDay } from "@/shared/lib";
import type { DateRange } from "../model/useInboxFilters";
import {
  buildMonthGrid,
  isInSelectedRange,
  monthLabelEs,
  monthOf,
  nextRange,
  shiftMonth,
} from "../model/calendar";

interface Props {
  value: DateRange;
  onChange: (range: DateRange) => void;
  onClear: () => void;
  /** Días con al menos una conversación — se marcan con un punto para que el
   *  operador no vaya cazando días vacíos a ciegas. */
  activeDays: Set<string>;
  /** Hoy en Bogotá. Llega por prop (no se consulta el reloj acá) para que el
   *  componente sea puro y el hook siga siendo la única fuente del "hoy". */
  today: string;
  /** Texto del rango vigente, ya formateado por el hook. */
  label: string;
}

const WEEKDAYS = ["L", "M", "M", "J", "V", "S", "D"];

export function InboxDateFilter({
  value,
  onChange,
  onClear,
  activeDays,
  today,
  label,
}: Props) {
  const [open, setOpen] = useState(false);
  // El mes que se está mirando. Se siembra con el del rango elegido (si lo hay)
  // para que reabrir el calendario no te tire de vuelta al mes actual.
  const [month, setMonth] = useState(() => monthOf(value.from ?? today));

  const hasRange = value.from !== null;

  const toggle = () => {
    if (!open) setMonth(monthOf(value.from ?? today));
    setOpen((v) => !v);
  };

  const pick = (iso: string) => onChange(nextRange(value, iso));
  const pickRange = (from: string, to: string) => onChange({ from, to });

  return (
    <div className="cal-filter">
      <button
        className={"cal-trigger" + (hasRange ? " on" : "")}
        onClick={toggle}
        aria-expanded={open}
        aria-label={hasRange ? label : "Filtrar por fecha"}
      >
        <Icon.cal />
        <span className="cal-trigger-label">{label}</span>
        <span className={"cal-caret" + (open ? " open" : "")}>
          <Icon.caret />
        </span>
      </button>

      {open && (
        <div className="cal-panel">
          <div className="cal-head">
            <button
              className="cal-nav"
              aria-label="Mes anterior"
              onClick={() => setMonth((m) => shiftMonth(m, -1))}
            >
              ‹
            </button>
            <span className="cal-month">{monthLabelEs(month)}</span>
            <button
              className="cal-nav"
              aria-label="Mes siguiente"
              onClick={() => setMonth((m) => shiftMonth(m, 1))}
            >
              ›
            </button>
          </div>

          <div className="cal-week">
            {WEEKDAYS.map((d, i) => (
              <span key={i} className="cal-wd">
                {d}
              </span>
            ))}
          </div>

          {/* `grid` + `row` + `gridcell` con el <button> ADENTRO de la celda:
              poner role="gridcell" sobre el propio botón le pisa su rol y lo
              deja fuera del alcance de un lector de pantalla que busca
              controles. */}
          <div className="cal-grid" role="grid">
            {buildMonthGrid(month).map((week) => (
              <div className="cal-row" role="row" key={week[0].iso}>
                {week.map((cell) => {
                  const selected = isInSelectedRange(cell.iso, value);
                  const isEdge = cell.iso === value.from || cell.iso === value.to;
                  return (
                    <div role="gridcell" key={cell.iso}>
                      <button
                        className={
                          "cal-day" +
                          (cell.inMonth ? "" : " out") +
                          (selected ? " sel" : "") +
                          (isEdge ? " edge" : "")
                        }
                        data-active={String(activeDays.has(cell.iso))}
                        data-today={String(cell.iso === today)}
                        aria-label={formatIsoDateEs(cell.iso)}
                        aria-pressed={selected}
                        onClick={() => pick(cell.iso)}
                      >
                        {Number(cell.iso.slice(8))}
                      </button>
                    </div>
                  );
                })}
              </div>
            ))}
          </div>

          <div className="cal-presets">
            <button className="cal-preset" onClick={() => pickRange(today, today)}>
              Hoy
            </button>
            <button
              className="cal-preset"
              onClick={() => {
                const y = shiftIsoDay(today, -1);
                pickRange(y, y);
              }}
            >
              Ayer
            </button>
            <button
              className="cal-preset"
              onClick={() => pickRange(shiftIsoDay(today, -6), today)}
            >
              7 días
            </button>
            <button
              className="cal-preset"
              onClick={() => pickRange(shiftIsoDay(today, -29), today)}
            >
              30 días
            </button>
            {hasRange && (
              <button className="cal-preset clear" onClick={onClear}>
                Limpiar
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
