/**
 * El CSV del reporte (tarjeta del ciclo #18) trae las mismas cifras que el
 * resumen en pantalla y una fila por pedido del rango (sin pruebas).
 */
import { describe, expect, it } from "vitest";
import { salesReport, type Order } from "@plugins/orders/frontend/entities/order";
import { salesReportCsv } from "./salesReportCsv";

function order(overrides: Partial<Order> & { id: string }): Order {
  return {
    customer: "Cliente",
    short: "CL",
    color: "a",
    phone: "—",
    city: "Bogotá",
    channel: "WhatsApp",
    status: "preparing",
    payStatus: "pending",
    payType: "cod",
    items: 1,
    total: 10000,
    shipping: 0,
    dueIso: "",
    dueTime: "—",
    pieces: 1,
    agent: "—",
    priority: "normal",
    isDraft: false,
    isDueEstimated: false,
    createdIso: "",
    ...overrides,
  };
}

const ORDERS = [
  order({ id: "#1", customer: "Ana; Pérez", dueIso: "2026-11-02", total: 30000, status: "delivered", payStatus: "paid" }),
  order({ id: "#2", dueIso: "2026-11-03", total: 20000 }),
  order({ id: "#9", dueIso: "2026-11-03", total: 55000, isTest: true }),
];
const RANGE = { from: "2026-11-01", to: "2026-11-05" };

function rows(csv: string): string[][] {
  return csv
    .replace(/^﻿/, "")
    .trim()
    .split("\r\n")
    .map((line) => line.split(";"));
}

describe("salesReportCsv", () => {
  it("el resumen del CSV tiene las mismas cifras que el reporte", () => {
    const report = salesReport(ORDERS, RANGE);
    const table = rows(salesReportCsv(report, ORDERS, RANGE));
    const value = (label: string) => table.find((r) => r[0] === label)?.[1];

    expect(value("Pedidos")).toBe(String(report.orders));
    expect(value("Ventas")).toBe(String(report.sales));
    expect(value("Cobrado")).toBe(String(report.collected));
    expect(value("Por cobrar")).toBe(String(report.toCollect));
    expect(value("Ticket promedio")).toBe(String(report.averageTicket));
  });

  it("trae una fila por pedido del rango, sin los de prueba", () => {
    const csv = salesReportCsv(salesReport(ORDERS, RANGE), ORDERS, RANGE);

    expect(csv).toContain("#1");
    expect(csv).toContain("#2");
    expect(csv).not.toContain("#9");
  });

  it("abre bien en Excel en español: BOM y campos con separador entre comillas", () => {
    const csv = salesReportCsv(salesReport(ORDERS, RANGE), ORDERS, RANGE);

    expect(csv.startsWith("﻿")).toBe(true);
    expect(csv).toContain('"Ana; Pérez"');
  });
});
