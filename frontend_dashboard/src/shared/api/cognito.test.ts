/**
 * Tests del cliente Cognito nativo (USER_PASSWORD_AUTH) — la app móvil pega
 * directo al endpoint de Cognito IDP sin navegador ni redirect. Mockeamos
 * `fetch` y verificamos: shape de la request (target + body), parseo de la
 * respuesta a la unión discriminada, y mapeo de errores a mensajes legibles.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  cognitoInitiateAuth,
  cognitoRefresh,
  cognitoRespondNewPassword,
} from "./cognito";

const fetchMock = vi.fn();

const CFG = {
  idpEndpoint: "https://cognito-idp.us-east-1.amazonaws.com/",
  clientId: "abc123",
};

function okResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/x-amz-json-1.1" },
  });
}
function errResponse(type: string, message: string, status = 400) {
  return new Response(JSON.stringify({ __type: type, message }), {
    status,
    headers: { "content-type": "application/x-amz-json-1.1" },
  });
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("cognitoInitiateAuth", () => {
  it("POSTea a cognito-idp.<region> con target InitiateAuth y devuelve tokens", async () => {
    fetchMock.mockResolvedValue(
      okResponse({
        AuthenticationResult: {
          AccessToken: "acc.tok",
          IdToken: "id.tok",
          RefreshToken: "ref.tok",
          ExpiresIn: 3600,
          TokenType: "Bearer",
        },
      }),
    );

    const out = await cognitoInitiateAuth(CFG, "op@hubara.co", "s3cret");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://cognito-idp.us-east-1.amazonaws.com/");
    expect(init.headers["X-Amz-Target"]).toBe(
      "AWSCognitoIdentityProviderService.InitiateAuth",
    );
    const body = JSON.parse(init.body);
    expect(body.AuthFlow).toBe("USER_PASSWORD_AUTH");
    expect(body.ClientId).toBe("abc123");
    expect(body.AuthParameters.USERNAME).toBe("op@hubara.co");
    expect(body.AuthParameters.PASSWORD).toBe("s3cret");

    expect(out.kind).toBe("tokens");
    if (out.kind === "tokens") {
      expect(out.accessToken).toBe("acc.tok");
      expect(out.refreshToken).toBe("ref.tok");
      expect(out.expiresIn).toBe(3600);
    }
  });

  it("propaga el challenge NEW_PASSWORD_REQUIRED con la Session", async () => {
    fetchMock.mockResolvedValue(
      okResponse({
        ChallengeName: "NEW_PASSWORD_REQUIRED",
        Session: "sess-xyz",
        ChallengeParameters: { requiredAttributes: "[]" },
      }),
    );

    const out = await cognitoInitiateAuth(CFG, "op@hubara.co", "temp");
    expect(out.kind).toBe("new_password_required");
    if (out.kind === "new_password_required") {
      expect(out.session).toBe("sess-xyz");
      expect(out.username).toBe("op@hubara.co");
    }
  });

  it("mapea NotAuthorizedException a un mensaje legible sin filtrar detalle", async () => {
    fetchMock.mockResolvedValue(
      errResponse("NotAuthorizedException", "Incorrect username or password."),
    );
    const out = await cognitoInitiateAuth(CFG, "op@hubara.co", "mala");
    expect(out.kind).toBe("error");
    if (out.kind === "error") {
      expect(out.code).toBe("NotAuthorizedException");
      expect(out.message.toLowerCase()).toContain("contraseña");
    }
  });

  it("UserNotFound se reporta igual que credenciales malas (anti-enumeración)", async () => {
    fetchMock.mockResolvedValue(
      errResponse("UserNotFoundException", "User does not exist."),
    );
    const out = await cognitoInitiateAuth(CFG, "nadie@x.co", "x");
    expect(out.kind).toBe("error");
    if (out.kind === "error") {
      expect(out.message.toLowerCase()).toContain("contraseña");
    }
  });

  it("un error de red devuelve kind=error, no lanza", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const out = await cognitoInitiateAuth(CFG, "op@hubara.co", "x");
    expect(out.kind).toBe("error");
    if (out.kind === "error") {
      expect(out.code).toBe("network");
    }
  });
});

describe("cognitoRefresh", () => {
  it("usa REFRESH_TOKEN_AUTH y conserva el refresh token entrante", async () => {
    fetchMock.mockResolvedValue(
      okResponse({
        AuthenticationResult: {
          AccessToken: "new.acc",
          IdToken: "new.id",
          ExpiresIn: 3600,
          TokenType: "Bearer",
          // Cognito NO devuelve un RefreshToken nuevo en el refresh.
        },
      }),
    );

    const out = await cognitoRefresh(CFG, "ref.tok");
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.AuthFlow).toBe("REFRESH_TOKEN_AUTH");
    expect(body.AuthParameters.REFRESH_TOKEN).toBe("ref.tok");

    expect(out.kind).toBe("tokens");
    if (out.kind === "tokens") {
      expect(out.accessToken).toBe("new.acc");
      // Reusa el refresh token que le pasamos.
      expect(out.refreshToken).toBe("ref.tok");
    }
  });

  it("refresh inválido → error (fuerza re-login)", async () => {
    fetchMock.mockResolvedValue(
      errResponse("NotAuthorizedException", "Refresh Token has expired."),
    );
    const out = await cognitoRefresh(CFG, "viejo");
    expect(out.kind).toBe("error");
  });
});

describe("cognitoRespondNewPassword", () => {
  it("responde el challenge con NEW_PASSWORD y devuelve tokens", async () => {
    fetchMock.mockResolvedValue(
      okResponse({
        AuthenticationResult: {
          AccessToken: "acc2",
          IdToken: "id2",
          RefreshToken: "ref2",
          ExpiresIn: 3600,
        },
      }),
    );

    const out = await cognitoRespondNewPassword(
      CFG,
      "op@hubara.co",
      "sess-xyz",
      "NuevaClave123!",
    );

    const [url, init] = fetchMock.mock.calls[0];
    expect(init.headers["X-Amz-Target"]).toBe(
      "AWSCognitoIdentityProviderService.RespondToAuthChallenge",
    );
    const body = JSON.parse(init.body);
    expect(body.ChallengeName).toBe("NEW_PASSWORD_REQUIRED");
    expect(body.Session).toBe("sess-xyz");
    expect(body.ChallengeResponses.NEW_PASSWORD).toBe("NuevaClave123!");
    expect(body.ChallengeResponses.USERNAME).toBe("op@hubara.co");
    expect(url).toContain("us-east-1");

    expect(out.kind).toBe("tokens");
  });
});
