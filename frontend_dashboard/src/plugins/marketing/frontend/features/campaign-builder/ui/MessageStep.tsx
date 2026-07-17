/**
 * Paso 3 — Mensaje: header (60) + body (640, el límite real de la variable
 * del template) con contadores; footer y CTA se muestran pero van FIJOS en
 * el template MARKETING aprobado — solo header+body+oferta viajan como
 * variables. El saludo con nombre lo pone el sistema por destinatario.
 */

import type { CampaignDraft } from "../model/draft";

interface Props {
  draft: CampaignDraft;
  editable: boolean;
  onPatch: (p: Partial<CampaignDraft>) => void;
  onCommit: (p?: Partial<CampaignDraft>) => void;
}

export function MessageStep({ draft, editable, onPatch, onCommit }: Props) {
  const m = draft.message;
  const patchMessage = (field: keyof CampaignDraft["message"], value: string) =>
    onPatch({ message: { ...m, [field]: value } });

  return (
    <div className="flex flex-col gap-3">
      <label className="flex flex-col gap-1">
        <span className="flex items-baseline justify-between text-[11px] font-medium text-fg-muted">
          Encabezado
          <span className="tabular-nums text-fg-faint">{m.header.length}/60</span>
        </span>
        <input
          type="text"
          maxLength={60}
          disabled={!editable}
          value={m.header}
          placeholder="¡Se acerca el Día del Padre! 🎁"
          onChange={(e) => patchMessage("header", e.target.value)}
          onBlur={() => onCommit()}
          className="rounded-md border border-line bg-transparent px-2.5 py-1.5 text-[12.5px] text-fg outline-none focus:border-accent disabled:opacity-60 placeholder:text-fg-faint"
        />
      </label>

      <label className="flex flex-col gap-1">
        <span className="flex items-baseline justify-between text-[11px] font-medium text-fg-muted">
          Cuerpo
          <span className="tabular-nums text-fg-faint">{m.body.length}/640</span>
        </span>
        <textarea
          rows={4}
          maxLength={640}
          disabled={!editable}
          value={m.body}
          placeholder="Contale a tu cliente qué hay de nuevo…"
          onChange={(e) => patchMessage("body", e.target.value)}
          onBlur={() => onCommit()}
          className="resize-y rounded-md border border-line bg-transparent px-2.5 py-1.5 text-[12.5px] leading-relaxed text-fg outline-none focus:border-accent disabled:opacity-60 placeholder:text-fg-faint"
        />
      </label>

      <div className="grid grid-cols-2 gap-2.5 max-[900px]:grid-cols-1">
        <label className="flex flex-col gap-1">
          <span className="text-[11px] font-medium text-fg-muted">Pie (fijo)</span>
          <input
            type="text"
            maxLength={60}
            disabled={!editable}
            value={m.footer}
            onChange={(e) => patchMessage("footer", e.target.value)}
            onBlur={() => onCommit()}
            className="rounded-md border border-line bg-transparent px-2.5 py-1.5 text-[12.5px] text-fg outline-none focus:border-accent disabled:opacity-60"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[11px] font-medium text-fg-muted">Botón (fijo)</span>
          <input
            type="text"
            maxLength={20}
            disabled={!editable}
            value={m.cta}
            onChange={(e) => patchMessage("cta", e.target.value)}
            onBlur={() => onCommit()}
            className="rounded-md border border-line bg-transparent px-2.5 py-1.5 text-[12.5px] text-fg outline-none focus:border-accent disabled:opacity-60"
          />
        </label>
      </div>

      <p className="text-[11px] leading-snug text-fg-faint">
        El saludo con nombre ("Hola Camila") lo agrega el sistema por
        destinatario. Pie, botón y el texto de baja van fijos en el template
        aprobado de WhatsApp — solo encabezado, cuerpo y oferta viajan como
        variables.
      </p>
    </div>
  );
}
