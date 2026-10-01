package com.hubara.operator.core.network.auth

import com.hubara.operator.core.network.OperatorJson
import java.io.IOException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.HttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

sealed interface CognitoOutcome {
    data class Tokens(val accessToken: String, val idToken: String, val refreshToken: String, val expiresInSec: Int) : CognitoOutcome
    data class NewPasswordRequired(val session: String, val username: String) : CognitoOutcome
    data class Failure(val code: String, val message: String) : CognitoOutcome
}

/**
 * Login nativo contra Cognito (USER_PASSWORD_AUTH), el mismo flujo de la app actual: tres POST con el
 * protocolo JSON-1.1 de AWS, sin SDK. Cliente público: no hay SECRET_HASH. Nunca lanza.
 */
class CognitoClient(
    private val http: OkHttpClient,
    private val endpoint: HttpUrl,
    private val clientId: String,
) {
    suspend fun login(username: String, password: String): CognitoOutcome {
        val body = buildJsonObject {
            put("AuthFlow", "USER_PASSWORD_AUTH")
            put("ClientId", clientId)
            put("AuthParameters", buildJsonObject { put("USERNAME", username); put("PASSWORD", password) })
        }
        return call("InitiateAuth", body).let { it.toOutcome(username = username, fallbackRefresh = null) }
    }

    suspend fun completeNewPassword(username: String, session: String, newPassword: String): CognitoOutcome {
        val body = buildJsonObject {
            put("ChallengeName", "NEW_PASSWORD_REQUIRED")
            put("ClientId", clientId)
            put("Session", session)
            put("ChallengeResponses", buildJsonObject { put("USERNAME", username); put("NEW_PASSWORD", newPassword) })
        }
        return call("RespondToAuthChallenge", body).toOutcome(username = username, fallbackRefresh = null)
    }

    suspend fun refresh(refreshToken: String): CognitoOutcome {
        val body = buildJsonObject {
            put("AuthFlow", "REFRESH_TOKEN_AUTH")
            put("ClientId", clientId)
            put("AuthParameters", buildJsonObject { put("REFRESH_TOKEN", refreshToken) })
        }
        return call("InitiateAuth", body).toOutcome(username = "", fallbackRefresh = refreshToken)
    }

    /**
     * Revoca el refresh token (y los access tokens que emitió) en Cognito. true si Cognito lo confirmó; sin red o con
     * error, false: quien llama igual borra la sesión del teléfono.
     */
    suspend fun revoke(refreshToken: String): Boolean {
        val body = buildJsonObject {
            put("Token", refreshToken)
            put("ClientId", clientId)
        }
        return call("RevokeToken", body) is Raw.Ok
    }

    private sealed interface Raw {
        data class Ok(val body: JsonObject) : Raw
        data class Err(val failure: CognitoOutcome.Failure) : Raw
    }

    private suspend fun call(target: String, payload: JsonObject): Raw = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url(endpoint)
            .header("X-Amz-Target", "AWSCognitoIdentityProviderService.$target")
            .post(payload.toString().toRequestBody(AMZ_JSON))
            .build()
        try {
            http.newCall(request).execute().use { response ->
                val text = response.body.string()
                val json = runCatching { OperatorJson.parseToJsonElement(text).jsonObject }.getOrDefault(JsonObject(emptyMap()))
                if (response.isSuccessful) {
                    Raw.Ok(json)
                } else {
                    val type = json["__type"]?.jsonPrimitive?.contentOrNull ?: "UnknownException"
                    val code = type.substringAfterLast('#')
                    Raw.Err(CognitoOutcome.Failure(code, friendlyMessage(code)))
                }
            }
        } catch (_: IOException) {
            Raw.Err(CognitoOutcome.Failure(NETWORK, "Sin conexión con el servidor de inicio de sesión. Revisa la señal."))
        }
    }

    private fun Raw.toOutcome(username: String, fallbackRefresh: String?): CognitoOutcome = when (this) {
        is Raw.Err -> failure
        is Raw.Ok -> {
            val challenge = body["ChallengeName"]?.jsonPrimitive?.contentOrNull
            val result = body["AuthenticationResult"]?.jsonObject
            when {
                challenge == "NEW_PASSWORD_REQUIRED" ->
                    CognitoOutcome.NewPasswordRequired(body["Session"]?.jsonPrimitive?.contentOrNull.orEmpty(), username)
                result != null -> CognitoOutcome.Tokens(
                    accessToken = result["AccessToken"]?.jsonPrimitive?.contentOrNull.orEmpty(),
                    idToken = result["IdToken"]?.jsonPrimitive?.contentOrNull.orEmpty(),
                    refreshToken = result["RefreshToken"]?.jsonPrimitive?.contentOrNull ?: fallbackRefresh.orEmpty(),
                    expiresInSec = result["ExpiresIn"]?.jsonPrimitive?.intOrNull ?: 3600,
                )
                else -> CognitoOutcome.Failure("unsupported_challenge", "No se pudo iniciar sesión. Vuelve a intentar.")
            }
        }
    }

    companion object {
        /** Código de [CognitoOutcome.Failure] cuando no hubo red (no es un rechazo de Cognito). */
        const val NETWORK = "network"

        private val AMZ_JSON = "application/x-amz-json-1.1".toMediaType()

        /** Credenciales malas y usuario inexistente dan el MISMO mensaje: no se revela qué cuentas existen. */
        fun friendlyMessage(code: String): String = when (code) {
            "NotAuthorizedException", "UserNotFoundException" -> "Email o contraseña incorrectos."
            "PasswordResetRequiredException" -> "Tu contraseña necesita un reinicio. Pídeselo al administrador."
            "UserNotConfirmedException" -> "La cuenta todavía no está confirmada."
            "TooManyRequestsException", "LimitExceededException" -> "Demasiados intentos. Espera un momento y vuelve a intentar."
            "InvalidPasswordException" -> "La contraseña no cumple la política de seguridad."
            "CodeMismatchException", "ExpiredCodeException" -> "El código no es válido o ya venció."
            else -> "No se pudo iniciar sesión. Vuelve a intentar."
        }
    }
}
