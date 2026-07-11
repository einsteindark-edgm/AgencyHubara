/**
 * Tests de la pantalla de login nativa: submit llama a onSignIn con las
 * credenciales, muestra el error, y en modo "contraseña nueva" pide la clave
 * nueva + confirmación y llama a onCompleteNewPassword.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { LoginScreen } from "./LoginScreen";

describe("LoginScreen", () => {
  it("submit con email+contraseña llama a onSignIn", () => {
    const onSignIn = vi.fn();
    render(
      <LoginScreen
        state={{ status: "unauthenticated" }}
        submitting={false}
        onSignIn={onSignIn}
        onCompleteNewPassword={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: "op@hubara.co" },
    });
    fireEvent.change(screen.getByLabelText(/contraseña/i), {
      target: { value: "clave123" },
    });
    fireEvent.click(screen.getByRole("button", { name: /ingresar/i }));
    expect(onSignIn).toHaveBeenCalledWith("op@hubara.co", "clave123");
  });

  it("muestra el mensaje de error de credenciales", () => {
    render(
      <LoginScreen
        state={{ status: "unauthenticated", error: "Email o contraseña incorrectos." }}
        submitting={false}
        onSignIn={vi.fn()}
        onCompleteNewPassword={vi.fn()}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/incorrectos/i);
  });

  it("deshabilita el botón mientras submitting", () => {
    render(
      <LoginScreen
        state={{ status: "unauthenticated" }}
        submitting={true}
        onSignIn={vi.fn()}
        onCompleteNewPassword={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /ingresando/i })).toBeDisabled();
  });

  it("modo contraseña nueva: exige coincidencia y llama a onCompleteNewPassword", () => {
    const onComplete = vi.fn();
    render(
      <LoginScreen
        state={{ status: "new_password_required", session: "s", username: "op@hubara.co" }}
        submitting={false}
        onSignIn={vi.fn()}
        onCompleteNewPassword={onComplete}
      />,
    );
    const nueva = screen.getByLabelText(/nueva contraseña/i);
    const confirmar = screen.getByLabelText(/confirmar/i);

    // No coincide → no llama.
    fireEvent.change(nueva, { target: { value: "Abcdef123!" } });
    fireEvent.change(confirmar, { target: { value: "otra" } });
    fireEvent.click(screen.getByRole("button", { name: /cambiar contraseña/i }));
    expect(onComplete).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/no coinciden/i);

    // Coincide → llama.
    fireEvent.change(confirmar, { target: { value: "Abcdef123!" } });
    fireEvent.click(screen.getByRole("button", { name: /cambiar contraseña/i }));
    expect(onComplete).toHaveBeenCalledWith("Abcdef123!");
  });
});
