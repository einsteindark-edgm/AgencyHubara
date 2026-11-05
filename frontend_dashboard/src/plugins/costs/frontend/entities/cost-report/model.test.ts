import { describe, expect, it } from "vitest";

import { mapBackendReport } from "./api";
import { DEEPSEEK_REPORT_FIXTURE, NOT_CONNECTED_FIXTURE, WHATSAPP_REPORT_FIXTURE } from "./fixtures";
import { backendProviderReportSchema } from "./contracts";
import { fmtCop, fmtMoney, fmtUsd, summarizeTotal } from "./model";

const report = (raw: unknown) => mapBackendReport(backendProviderReportSchema.parse(raw));

describe("money formatting", () => {
  it("shows dollars with cents and sub-cent amounts with four decimals", () => {
    expect(fmtUsd(7_888_983)).toBe("US$7,89");
    expect(fmtUsd(12_000)).toBe("US$0,0120");
    expect(fmtUsd(0)).toBe("US$0,00");
  });

  it("shows pesos without decimals", () => {
    expect(fmtCop(3_222_400_000)).toBe("$3.222 COP");
  });

  it("formats each line in its own currency", () => {
    expect(fmtMoney(3_550_000, "CNY")).toBe("¥3,55 CNY");
    expect(fmtMoney(3_200_000_000, "COP")).toBe("$3.200 COP");
  });
});

describe("summarizeTotal", () => {
  it("adds only the production total of the providers that answered", () => {
    const total = summarizeTotal([
      { id: "deepseek", label: "DeepSeek", report: report(DEEPSEEK_REPORT_FIXTURE), state: "done" },
      { id: "whatsapp", label: "WhatsApp (Meta)", report: report(WHATSAPP_REPORT_FIXTURE), state: "done" },
    ]);
    expect(total.usdMicros).toBe(8_500_000);
    expect(total.copMicros).toBe(27_200_000_000);
    expect(total.complete).toBe(true);
    expect(total.parts.map((p) => p.id)).toEqual(["deepseek", "whatsapp"]);
  });

  it("says which providers are missing and why, instead of counting them as zero", () => {
    const total = summarizeTotal([
      { id: "deepseek", label: "DeepSeek", report: report(DEEPSEEK_REPORT_FIXTURE), state: "done" },
      { id: "openrouter", label: "OpenRouter (Jev)", report: report(NOT_CONNECTED_FIXTURE), state: "done" },
      { id: "gemini", label: "Gemini", report: undefined, state: "loading" },
      { id: "temporal", label: "Temporal Cloud", report: undefined, state: "failed" },
    ]);
    expect(total.usdMicros).toBe(7_500_000);
    expect(total.complete).toBe(false);
    expect(total.missing).toEqual([
      { id: "openrouter", label: "OpenRouter (Jev)", reason: "sin conectar" },
      { id: "gemini", label: "Gemini", reason: "consultando" },
      { id: "temporal", label: "Temporal Cloud", reason: "falló la consulta" },
    ]);
  });
});
