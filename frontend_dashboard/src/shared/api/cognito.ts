/**
 * Cliente Cognito nativo (flujo USER_PASSWORD_AUTH) para el login de la app
 * móvil — un formulario email+contraseña DENTRO de la app, sin navegador, sin
 * redirect, sin deep links.
 *
 * Pega directo al endpoint público de Cognito IDP
 * (`https://cognito-idp.<region>.amazonaws.com/`) con el protocolo JSON-1.1 de
 * AWS. NO usa el SDK de AWS: son 3 POST con `X-Amz-Target`, y evitamos ~200 KB
 * de bundle en el webview. Cliente PÚBLICO (sin secret) → no hay SECRET_HASH.
 *
 * Requisito en Cognito: el app client debe tener `ALLOW_USER_PASSWORD_AUTH`
 * habilitado (App integration → App client → Authentication flows).
 *
 * Las funciones NUNCA lanzan: devuelven una unión discriminada
 * (`tokens | new_password_required | error`) para que la UI ramifique sin
 * try/catch y los errores de red no tumben la pantalla de login.
 */

const AMZ_JSON = "application/x-amz-json-1.1";

export interface CognitoConfig {
  /** URL del endpoint IDP (`env.cognitoIdpEndpoint`) — la arma la capa config. */
  idpEndpoint: string;
  clientId: string;
}

export interface CognitoTokens {
  kind: "tokens";
  accessToken: string;
  idToken: string;
  refreshToken: string;
  /** Segundos de validez del access token. */
  expiresIn: number;
}

export interface CognitoNewPasswordRequired {
  kind: "new_password_required";
  session: string;
  username: string;
}

export interface CognitoError {
  kind: "error";
  code: string;
  message: string;
}

export type CognitoOutcome =
  | CognitoTokens
  | CognitoNewPasswordRequired
  | CognitoError;

/** Mensajes de usuario por `__type` de Cognito. Credenciales malas y usuario
 *  inexistente colapsan al MISMO mensaje (anti-enumeración de cuentas). */
function friendlyError(type: string, fallback: string): string {
  const code = type.split("#").pop() ?? type;
  switch (code) {
    case "NotAuthorizedException":
    case "UserNotFoundException":
      return "Email o contraseña incorrectos.";
    case "PasswordResetRequiredException":
      return "Tu contraseña necesita reinicio. Contactá al administrador.";
    case "UserNotConfirmedException":
      return "La cuenta todavía no está confirmada.";
    case "TooManyRequestsException":
    case "LimitExceededException":
      return "Demasiados intentos. Esperá un momento y reintentá.";
    case "InvalidPasswordException":
      return "La contraseña no cumple la política de seguridad.";
    case "CodeMismatchException":
    case "ExpiredCodeException":
      return "El código es inválido o expiró.";
    default:
      return fallback || "No se pudo autenticar. Reintentá.";
  }
}

async function amzPost(
  cfg: CognitoConfig,
  target: string,
  payload: Record<string, unknown>,
): Promise<{ ok: true; body: Record<string, unknown> } | CognitoError> {
  try {
    const res = await fetch(cfg.idpEndpoint, {
      method: "POST",
      headers: {
        "Content-Type": AMZ_JSON,
        "X-Amz-Target": `AWSCognitoIdentityProviderService.${target}`,
      },
      body: JSON.stringify(payload),
    });
    const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
    if (!res.ok) {
      const type = String(body.__type ?? "UnknownException");
      return {
        kind: "error",
        code: type.split("#").pop() ?? type,
        message: friendlyError(type, String(body.message ?? "")),
      };
    }
    return { ok: true, body };
  } catch {
    return {
      kind: "error",
      code: "network",
      message: "Sin conexión con el servidor de login. Revisá la señal.",
    };
  }
}

function toTokens(
  authResult: Record<string, unknown>,
  fallbackRefresh?: string,
): CognitoTokens {
  return {
    kind: "tokens",
    accessToken: String(authResult.AccessToken ?? ""),
    idToken: String(authResult.IdToken ?? ""),
    // El refresh flow NO devuelve un RefreshToken nuevo → reusamos el entrante.
    refreshToken: String(authResult.RefreshToken ?? fallbackRefresh ?? ""),
    expiresIn: Number(authResult.ExpiresIn ?? 3600),
  };
}

/** Login inicial. Devuelve tokens, o el challenge de contraseña nueva, o error. */
export async function cognitoInitiateAuth(
  cfg: CognitoConfig,
  username: string,
  password: string,
): Promise<CognitoOutcome> {
  const r = await amzPost(cfg, "InitiateAuth", {
    AuthFlow: "USER_PASSWORD_AUTH",
    ClientId: cfg.clientId,
    AuthParameters: { USERNAME: username, PASSWORD: password },
  });
  if ("kind" in r) return r; // error
  const body = r.body;
  if (body.ChallengeName === "NEW_PASSWORD_REQUIRED") {
    return {
      kind: "new_password_required",
      session: String(body.Session ?? ""),
      username,
    };
  }
  if (body.AuthenticationResult) {
    return toTokens(body.AuthenticationResult as Record<string, unknown>);
  }
  return {
    kind: "error",
    code: String(body.ChallengeName ?? "UnsupportedChallenge"),
    message: "Este tipo de verificación aún no está soportado en la app.",
  };
}

/** Renueva el access token con el refresh token (sin re-tipear la contraseña). */
export async function cognitoRefresh(
  cfg: CognitoConfig,
  refreshToken: string,
): Promise<CognitoOutcome> {
  const r = await amzPost(cfg, "InitiateAuth", {
    AuthFlow: "REFRESH_TOKEN_AUTH",
    ClientId: cfg.clientId,
    AuthParameters: { REFRESH_TOKEN: refreshToken },
  });
  if ("kind" in r) return r;
  if (r.body.AuthenticationResult) {
    return toTokens(
      r.body.AuthenticationResult as Record<string, unknown>,
      refreshToken,
    );
  }
  return { kind: "error", code: "refresh_failed", message: "Sesión expirada." };
}

/** Completa el challenge NEW_PASSWORD_REQUIRED (usuario creado por admin). */
export async function cognitoRespondNewPassword(
  cfg: CognitoConfig,
  username: string,
  session: string,
  newPassword: string,
): Promise<CognitoOutcome> {
  const r = await amzPost(cfg, "RespondToAuthChallenge", {
    ChallengeName: "NEW_PASSWORD_REQUIRED",
    ClientId: cfg.clientId,
    Session: session,
    ChallengeResponses: { USERNAME: username, NEW_PASSWORD: newPassword },
  });
  if ("kind" in r) return r;
  if (r.body.AuthenticationResult) {
    return toTokens(r.body.AuthenticationResult as Record<string, unknown>);
  }
  // Puede devolver OTRO challenge encadenado — fuera de scope por ahora.
  return {
    kind: "error",
    code: String(r.body.ChallengeName ?? "UnsupportedChallenge"),
    message: "No se pudo completar el cambio de contraseña.",
  };
}
