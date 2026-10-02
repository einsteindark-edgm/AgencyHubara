package com.hubara.operator

import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.network.di.ApiConfig
import org.junit.Test

class ApiConfigTest {
    @Test fun la_url_base_de_retrofit_siempre_termina_en_barra() {
        assertThat(normalizeBaseUrl("http://10.0.2.2:8000").toString()).isEqualTo("http://10.0.2.2:8000/")
        assertThat(normalizeBaseUrl("https://api.example.com/").toString()).isEqualTo("https://api.example.com/")
    }

    @Test fun sin_cliente_de_cognito_la_app_arranca_en_modo_dev() {
        val fallback = AppModule.serverConfigDefaults().fallback
        assertThat(ApiConfig({ fallback }).cognitoEnabled).isEqualTo(BuildConfig.COGNITO_CLIENT_ID.isNotBlank())
    }
}
