/**
 * Marco visual de un paso del builder: número (o check verde al completar),
 * título y contenido. Puro presentacional.
 */

import type { ReactNode, Ref } from "react";

import { Icon } from "@/shared/ui";

interface Props {
  n: number;
  title: string;
  done: boolean;
  hint?: string;
  children: ReactNode;
  sectionRef?: Ref<HTMLElement>;
}

export function StepShell({ n, title, done, hint, children, sectionRef }: Props) {
  return (
    <section
      ref={sectionRef}
      className="rounded-lg border border-line bg-sidebar/40"
    >
      <header className="flex items-center gap-2.5 border-b border-line/60 px-4 py-2.5">
        <span
          className={
            "inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10.5px] font-bold " +
            (done ? "bg-ok-soft text-ok" : "bg-line/60 text-fg-muted")
          }
          aria-label={done ? `Paso ${n} completo` : `Paso ${n}`}
        >
          {done ? <Icon.check /> : n}
        </span>
        <h2 className="text-[13px] font-semibold tracking-tight text-fg">{title}</h2>
        {hint ? (
          <span className="ml-auto text-[11px] text-fg-faint">{hint}</span>
        ) : null}
      </header>
      <div className="px-4 py-3">{children}</div>
    </section>
  );
}
