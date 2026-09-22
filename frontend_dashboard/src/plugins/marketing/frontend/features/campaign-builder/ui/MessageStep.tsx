/**
 * Paso 3 — Mensaje: header (60) + body (640, el límite real de la variable
 * del template) con contadores. La plantilla MARKETING aprobada solo tiene un
 * cuerpo con saludo + mensaje + oferta y la baja fija: no tiene pie ni botón
 * (Meta los fija al aprobar y no admiten variables), así que no se ofrecen.
 * El saludo con nombre lo pone el sistema por destinatario.
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


      <p className="text-[11px] leading-snug text-fg-faint">
        El saludo con nombre ("Hola Camila") lo agrega el sistema por
        destinatario. Del mensaje viajan: encabezado, cuerpo y oferta, en un
        solo párrafo. El texto de baja va fijo al final. La plantilla aprobada
        de WhatsApp no tiene pie ni botón.
      </p>
    </div>
  );
}
