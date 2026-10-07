package com.hubara.operator.core.data.auth

import android.content.Context
import android.util.Base64
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.google.crypto.tink.Aead
import com.google.crypto.tink.KeyTemplates
import com.google.crypto.tink.RegistryConfiguration
import com.google.crypto.tink.aead.AeadConfig
import com.google.crypto.tink.integration.android.AndroidKeysetManager
import com.hubara.operator.core.network.OperatorJson
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext

private val Context.sessionStore: DataStore<Preferences> by preferencesDataStore(name = "session")

/**
 * La sesión cifrada: DataStore guarda el texto cifrado y la clave de Tink vive protegida por el
 * Android Keystore. Reemplaza a EncryptedSharedPreferences, que está deprecado.
 */
@Singleton
class TinkTokenStore @Inject constructor(@ApplicationContext private val context: Context) : TokenStore {
    private val key = stringPreferencesKey("tokens")
    private val associatedData = "hubara-operator-session".toByteArray()

    private val aead: Aead by lazy {
        AeadConfig.register()
        AndroidKeysetManager.Builder()
            .withSharedPref(context, "hubara_session_keyset", "hubara_session_keyset_prefs")
            .withKeyTemplate(KeyTemplates.get("AES256_GCM"))
            .withMasterKeyUri("android-keystore://hubara_session_master_key")
            .build()
            .keysetHandle
            .getPrimitive(RegistryConfiguration.get(), Aead::class.java)
    }

    override suspend fun load(): SessionTokens? = withContext(Dispatchers.IO) {
        val stored = context.sessionStore.data.first()[key] ?: return@withContext null
        runCatching {
            val plain = aead.decrypt(Base64.decode(stored, Base64.NO_WRAP), associatedData)
            OperatorJson.decodeFromString(SessionTokens.serializer(), plain.decodeToString())
        }.getOrNull()
    }

    override suspend fun save(tokens: SessionTokens) = withContext(Dispatchers.IO) {
        val plain = OperatorJson.encodeToString(SessionTokens.serializer(), tokens).toByteArray()
        val cipher = Base64.encodeToString(aead.encrypt(plain, associatedData), Base64.NO_WRAP)
        context.sessionStore.edit { it[key] = cipher }
        Unit
    }

    override suspend fun clear() = withContext(Dispatchers.IO) {
        context.sessionStore.edit { it.remove(key) }
        Unit
    }
}
