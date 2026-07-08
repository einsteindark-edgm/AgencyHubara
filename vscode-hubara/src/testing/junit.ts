/**
 * Parser mínimo de JUnit XML (el formato que emiten `pytest --junitxml` y
 * `vitest --reporter=junit`) — regex sobre un archivo self-generado y bien
 * formado, no un parser XML genérico. Alcanza para extraer classname/name/
 * time/failure por `<testcase>`, que es todo lo que necesita el runner.
 */
export interface JUnitCase {
  classname: string;
  name: string;
  time: number;
  /** presente si el caso falló o erroró; el mensaje ya viene sin escapes XML. */
  failureMessage?: string;
  skipped: boolean;
}

export function parseJUnitXml(xml: string): JUnitCase[] {
  const cases: JUnitCase[] = [];
  const testcaseRe = /<testcase\b([^>]*?)(?:\/>|>([\s\S]*?)<\/testcase>)/g;
  let m: RegExpExecArray | null;
  while ((m = testcaseRe.exec(xml))) {
    const attrs = parseAttrs(m[1]);
    const body = m[2] ?? "";
    const failureMatch = /<(failure|error)\b([^>]*?)(?:\/>|>([\s\S]*?)<\/\1>)/.exec(body);
    const skipped = /<skipped\b/.test(body);
    let failureMessage: string | undefined;
    if (failureMatch) {
      const failureAttrs = parseAttrs(failureMatch[2] ?? "");
      failureMessage = decodeXmlEntities(failureMatch[3]?.trim() || failureAttrs.message || "falló");
    }
    cases.push({
      classname: attrs.classname ?? "",
      name: attrs.name ?? "(sin nombre)",
      time: Number(attrs.time ?? 0),
      failureMessage,
      skipped,
    });
  }
  return cases;
}

function parseAttrs(s: string): Record<string, string> {
  const out: Record<string, string> = {};
  const re = /([\w:-]+)="([^"]*)"/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(s))) {
    out[m[1]] = decodeXmlEntities(m[2]);
  }
  return out;
}

function decodeXmlEntities(s: string): string {
  return s
    .replace(/&#(\d+);/g, (_, code: string) => String.fromCharCode(Number(code)))
    .replace(/&#x([0-9a-fA-F]+);/g, (_, code: string) => String.fromCharCode(parseInt(code, 16)))
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, "&");
}
