/**
 * Paso 5 — Envío de prueba: POST /test a UN número del operador. El 404
 * (número sin conversación previa con el bot) se superficie con su detail.
 * El historial `test_sends` se lista como chips.
 */

import { useState } from "react";

import { Icon } from "@/shared/ui";

import {
  useTestSend,
  type Campaign,
} from "@plugins/marketing/frontend/entities/campaign";
import {
  apiErrorDetail,
  fmtDateTimeMs,
} from "@plugins/marketing/frontend/lib/format";

interface Props {
  campaign: Campaign;
  editable: boolean;
}

export function TestSendStep({ campaign, editable }: Props) {
  const [phone, setPhone] = useState("");
  const test = useTestSend(campaign.id);

  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-center gap-2">
        <span className="text-fg-muted">
          <Icon.phone />
        </span>
        <input
          type="tel"
          disabled={!editable}
          value={phone}
          placeholder="573001234567"
          onChange={(e) => setPhone(e.target.value)}
          aria-label="Teléfono de prueba"
          className="w-56 rounded-md border border-line bg-transparent px-2.5 py-1.5 text-[12.5px] tabular-nums text-fg outline-none focus:border-accent disabled:opacity-60 placeholder:text-fg-faint"
        />
        <button
          type="button"
          disabled={!editable || test.isPending || phone.trim() === ""}
          onClick={() => test.mutate(phone.trim())}
          className="rounded-md border border-line px-3 py-1.5 text-[12px] font-semibold text-fg hover:bg-white/[0.05] disabled:opacity-50"
        >
          Enviar prueba
        </button>
        {test.isPending ? (
          <span className="text-[11.5px] text-fg-muted">Enviando…</span>
        ) : null}
      </div>

      {test.error ? (
        <p className="text-[11.5px] leading-snug text-danger">
          {apiErrorDetail(test.error)}
        </p>
      ) : null}
      {test.data ? (
        <p className="text-[11.5px] text-ok">Prueba enviada.</p>
      ) : null}

      {campaign.testSends.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {campaign.testSends.map((t, i) => (
            <span
              key={`${t.phone}-${t.atMs}-${i}`}
              className="inline-flex items-center gap-1.5 rounded-full bg-white/[0.06] px-2 py-0.5 text-[10.5px] tabular-nums text-fg-soft"
            >
              <Icon.check />
              {t.phone} · {fmtDateTimeMs(t.atMs)}
            </span>
          ))}
        </div>
      ) : (
        <p className="text-[11px] text-fg-faint">
          El número debe haber chateado con el bot al menos una vez.
        </p>
      )}
    </div>
  );
}
