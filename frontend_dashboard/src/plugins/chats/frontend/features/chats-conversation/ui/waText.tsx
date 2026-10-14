import type { ReactNode } from "react";

/**
 * El formato de WhatsApp: `*negrita*`, `_cursiva_`, `~tachado~`. Como en
 * WhatsApp, la marca abre al inicio o después de algo que no es letra ni
 * número, no empieza ni termina con espacio, no cruza un salto de línea y
 * cierra antes de algo que no es letra ni número: «2 * 3» o
 * «nombre_apellido@correo.com» quedan como están.
 */
const WA_FORMAT = /(^|[^\p{L}\p{N}])([*_~])(?!\s)([^\n]*?[^\s])\2(?=$|[^\p{L}\p{N}])/gu;

const TAG: Record<string, (children: string, key: number) => ReactNode> = {
  "*": (c, key) => <strong key={key}>{c}</strong>,
  _: (c, key) => <em key={key}>{c}</em>,
  "~": (c, key) => <s key={key}>{c}</s>,
};

/** El texto que leyó el cliente, como lo vio en WhatsApp. */
export function WaText({ text }: { text: string }) {
  const parts: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(WA_FORMAT)) {
    const [whole, before, mark, inner] = match;
    const start = match.index + before.length;
    parts.push(text.slice(last, start));
    parts.push(TAG[mark](inner, start));
    last = match.index + whole.length;
  }
  parts.push(text.slice(last));
  return <>{parts}</>;
}
