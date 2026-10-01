package com.hubara.operator.core.data.auth

import com.hubara.operator.core.data.Clock
import com.hubara.operator.core.network.auth.CognitoClient
import com.hubara.operator.core.network.auth.CognitoOutcome
import com.hubara.operator.core.network.di.AccessTokenProvider
import com.hubara.operator.core.network.di.ApiConfig
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

sealed interface AuthState {
    data object Loading : AuthState
    data object SignedOut : AuthState
    data class NeedsNewPassword(val username: String, val session: String) : AuthState
    data object SignedIn : AuthState
    /** Sin Cognito configurado y build debug (backend local): no hay login. */
    data object DevMode : AuthState
}

@Singleton
class AuthRepository @Inject constructor(
    private val config: ApiConfig,
    private val cognito: CognitoClient,
    private val store: TokenStore,
    private val clock: Clock,
) : AccessTokenProvider {
    private val _state = MutableStateFlow<AuthState>(AuthState.Loading)
    val state: StateFlow<AuthState> = _state.asStateFlow()

    @Volatile private var tokens: SessionTokens? = null
    private val refreshLock = Mutex()

    suspend fun restore() {
        if (!config.cognitoEnabled) {
            // Falla cerrada: un release compilado sin client id pide login (y no entra), nunca abre sin sesión.
            _state.value = if (config.devModeAllowed) AuthState.DevMode else AuthState.SignedOut
            return
        }
        tokens = store.load()
        _state.value = if (tokens != null) AuthState.SignedIn else AuthState.SignedOut
    }

    /** Devuelve el mensaje de error para el formulario, o null si salió bien. */
    suspend fun login(email: String, password: String): String? =
        handle(cognito.login(email.trim(), password), username = email.trim())

    suspend fun completeNewPassword(newPassword: String): String? {
        val pending = _state.value as? AuthState.NeedsNewPassword ?: return "Vuelve a iniciar sesión."
        return handle(cognito.completeNewPassword(pending.username, pending.session, newPassword), pending.username)
    }

    suspend fun logout() {
        tokens = null
        store.clear()
        _state.value = if (!config.cognitoEnabled && config.devModeAllowed) AuthState.DevMode else AuthState.SignedOut
    }

    override fun currentAccessToken(): String? = tokens?.accessToken

    override fun refreshAccessTokenBlocking(rejectedToken: String?): String? = runBlocking { forceRefresh(rejectedToken) }

    /** Refresco preventivo: si al token le queda menos de un minuto. */
    suspend fun refreshIfExpiring(): String? {
        val current = tokens ?: return null
        return if (current.expiresAtMs - clock.nowMs() > FRESH_MARGIN_MS) current.accessToken
        else forceRefresh(current.accessToken)
    }

    /**
     * Refresca una sola vez aunque lo pidan varias llamadas a la vez. Si Cognito lo rechaza, cierra la sesión; sin red
     * la conserva (se reintenta en la próxima llamada): una caída de señal no saca al operador.
     */
    suspend fun forceRefresh(rejectedToken: String?): String? = refreshLock.withLock {
        val current = tokens ?: return@withLock null
        if (rejectedToken != null && current.accessToken != rejectedToken) return@withLock current.accessToken
        when (val out = cognito.refresh(current.refreshToken)) {
            is CognitoOutcome.Tokens -> save(out, current.username).accessToken
            is CognitoOutcome.Failure if out.code == CognitoClient.NETWORK -> null
            else -> {
                logout()
                null
            }
        }
    }

    private suspend fun handle(outcome: CognitoOutcome, username: String): String? = when (outcome) {
        is CognitoOutcome.Tokens -> {
            save(outcome, username)
            _state.value = AuthState.SignedIn
            null
        }
        is CognitoOutcome.NewPasswordRequired -> {
            _state.value = AuthState.NeedsNewPassword(outcome.username, outcome.session)
            null
        }
        is CognitoOutcome.Failure -> outcome.message
    }

    private suspend fun save(t: CognitoOutcome.Tokens, username: String): SessionTokens {
        val saved = SessionTokens(t.accessToken, t.idToken, t.refreshToken, clock.nowMs() + t.expiresInSec * 1000L, username)
        tokens = saved
        store.save(saved)
        return saved
    }

    private companion object {
        const val FRESH_MARGIN_MS = 60_000L
    }
}
