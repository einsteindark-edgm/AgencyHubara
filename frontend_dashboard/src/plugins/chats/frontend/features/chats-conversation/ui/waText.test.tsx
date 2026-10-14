/**
 * El texto que leyó el cliente, con el formato de WhatsApp: `*negrita*`,
 * `_cursiva_` y `~tachado~`. Los asteriscos crudos («*Hubara*») no son lo que
 * vio el cliente.
 */
import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { WaText } from "./waText";

function html(text: string): string {
  const { container } = render(<WaText text={text} />);
  return container.innerHTML;
}

describe("WaText", () => {
  it("la negrita de WhatsApp se ve en negrita, sin asteriscos", () => {
    expect(html("bienvenido a *Hubara*, velas")).toBe("bienvenido a <strong>Hubara</strong>, velas");
  });

  it("cursiva y tachado", () => {
    expect(html("_hecho a mano_ y ~$30.000~")).toBe("<em>hecho a mano</em> y <s>$30.000</s>");
  });

  it("un asterisco suelto o un formato partido por un salto de línea queda como está", () => {
    expect(html("2 * 3 = 6")).toBe("2 * 3 = 6");
    expect(html("*a\nb*")).toBe("*a\nb*");
  });

  it("no inventa formato dentro de una palabra (correo, multiplicación)", () => {
    expect(html("nombre_apellido@correo.com")).toBe("nombre_apellido@correo.com");
  });
});
