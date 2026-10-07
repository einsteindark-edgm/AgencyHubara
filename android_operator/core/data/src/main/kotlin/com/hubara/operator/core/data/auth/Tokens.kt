package com.hubara.operator.core.data.auth

import kotlinx.serialization.Serializable

@Serializable
data class SessionTokens(
    val accessToken: String,
    val idToken: String,
    val refreshToken: String,
    val expiresAtMs: Long,
    val username: String,
)

/** Dónde vive la sesión. En la app, cifrada con Tink; en los tests, en memoria. */
interface TokenStore {
    suspend fun load(): SessionTokens?
    suspend fun save(tokens: SessionTokens)
    suspend fun clear()
}

class InMemoryTokenStore(private var tokens: SessionTokens? = null) : TokenStore {
    override suspend fun load() = tokens
    override suspend fun save(tokens: SessionTokens) { this.tokens = tokens }
    override suspend fun clear() { tokens = null }
}
