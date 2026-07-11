/**
 * Pantalla de login nativa de la app móvil (email + contraseña, sin navegador).
 * Presentacional: recibe el estado y las acciones del `MobileAuthGate`. Dos
 * modos: login normal y "contraseña nueva" (usuario creado por el admin con
 * clave temporal → Cognito exige cambiarla en el primer ingreso).
 */

import { useState, type FormEvent } from "react";
import type { MobileAuthState } from "./useMobileAuth";

interface Props {
  state: MobileAuthState;
  submitting: boolean;
  onSignIn: (username: string, password: string) => void;
  onCompleteNewPassword: (newPassword: string) => void;
}

export function LoginScreen({
  state,
  submitting,
  onSignIn,
  onCompleteNewPassword,
}: Props) {
  if (state.status === "new_password_required") {
    return (
      <NewPasswordForm
        submitting={submitting}
        serverError={state.error}
        onSubmit={onCompleteNewPassword}
      />
    );
  }
  const error = state.status === "unauthenticated" ? state.error : undefined;
  return <SignInForm submitting={submitting} error={error} onSubmit={onSignIn} />;
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="login-screen">
      <div className="login-card">
        <div className="login-brand">Hubara Chats</div>
        {children}
      </div>
    </div>
  );
}

function SignInForm({
  submitting,
  error,
  onSubmit,
}: {
  submitting: boolean;
  error?: string;
  onSubmit: (u: string, p: string) => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!email.trim() || !password || submitting) return;
    onSubmit(email, password);
  };

  return (
    <Shell>
      <form className="login-form" onSubmit={submit}>
        <label className="login-field">
          <span>Email</span>
          <input
            type="email"
            autoComplete="username"
            autoCapitalize="none"
            inputMode="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label className="login-field">
          <span>Contraseña</span>
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && (
          <div className="login-error" role="alert">
            {error}
          </div>
        )}
        <button
          type="submit"
          className="login-submit"
          disabled={submitting || !email.trim() || !password}
        >
          {submitting ? "Ingresando…" : "Ingresar"}
        </button>
      </form>
    </Shell>
  );
}

function NewPasswordForm({
  submitting,
  serverError,
  onSubmit,
}: {
  submitting: boolean;
  serverError?: string;
  onSubmit: (newPassword: string) => void;
}) {
  const [pw, setPw] = useState("");
  const [confirm, setConfirm] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    if (pw !== confirm) {
      setLocalError("Las contraseñas no coinciden.");
      return;
    }
    // La política del user pool exige 12 chars + mayús/minús/número/símbolo.
    if (pw.length < 12) {
      setLocalError("La contraseña debe tener al menos 12 caracteres.");
      return;
    }
    setLocalError(null);
    onSubmit(pw);
  };

  return (
    <Shell>
      <form className="login-form" onSubmit={submit}>
        <p className="login-hint">
          Es tu primer ingreso: definí una contraseña nueva.
        </p>
        <label className="login-field">
          <span>Nueva contraseña</span>
          <input
            type="password"
            autoComplete="new-password"
            value={pw}
            onChange={(e) => setPw(e.target.value)}
          />
        </label>
        <label className="login-field">
          <span>Confirmar contraseña</span>
          <input
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        </label>
        {(localError || serverError) && (
          <div className="login-error" role="alert">
            {localError ?? serverError}
          </div>
        )}
        <button type="submit" className="login-submit" disabled={submitting}>
          {submitting ? "Guardando…" : "Cambiar contraseña"}
        </button>
      </form>
    </Shell>
  );
}
