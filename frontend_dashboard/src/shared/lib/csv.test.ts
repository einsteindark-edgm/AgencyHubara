/**
 * `toCsv`: un texto que empieza como fórmula (=, +, -, @, tabulador, retorno)
 * no se ejecuta al abrir el archivo en Excel. El nombre del cliente puede
 * venir del perfil de WhatsApp: lo escribe un tercero.
 */
import { describe, expect, it } from "vitest";
import { toCsv } from "./csv";

const BOM = String.fromCharCode(0xfeff);

function cells(csv: string): string[] {
  return csv.slice(BOM.length).trim().split(";");
}

describe("toCsv", () => {
  it("neutraliza el texto que empieza como fórmula", () => {
    const out = cells(toCsv([["=HYPERLINK(1)", "+57", "-x", "@a", "\tz", "normal"]]));

    expect(out).toEqual(["'=HYPERLINK(1)", "'+57", "'-x", "'@a", "'\tz", "normal"]);
  });

  it("los números negativos siguen siendo números", () => {
    expect(cells(toCsv([[-5000, 0]]))).toEqual(["-5000", "0"]);
  });
});
