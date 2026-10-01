package com.hubara.operator.core.network

import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.network.config.parseServerConfig
import org.junit.Test

/**
 * La dirección del backend y lo demás que la app necesita para arrancar ya no van fijos en el APK: llegan en
 * `mobile/config.json` (lo publica el deploy del dashboard con los valores de Terraform). Si la IP cambia, la app se
 * entera sin publicar otra versión. Como el token va a esa dirección, solo se acepta si es https.
 */
class ServerConfigTest {
    private val full = """
        {"version": 3, "api_base_url": "https://api.tienda.example", "cognito_region": "us-east-1",
         "cognito_client_id": "cliente-publico", "privacy_url": "https://tienda.example/privacidad",
         "min_version_code": 2, "campo_nuevo": {"x": 1}}
    """.trimIndent()

    @Test fun lee_la_configuracion_del_servidor_e_ignora_lo_que_no_conoce() {
        val c = parseServerConfig(full, allowCleartext = false)!!
        assertThat(c.apiBaseUrl.toString()).isEqualTo("https://api.tienda.example/")
        assertThat(c.cognitoRegion).isEqualTo("us-east-1")
        assertThat(c.cognitoClientId).isEqualTo("cliente-publico")
        assertThat(c.privacyUrl).isEqualTo("https://tienda.example/privacidad")
        assertThat(c.minVersionCode).isEqualTo(2)
    }

    @Test fun sin_https_no_se_acepta_porque_ahi_va_el_token() {
        assertThat(parseServerConfig("""{"api_base_url": "http://98.88.0.1"}""", allowCleartext = false)).isNull()
        assertThat(parseServerConfig("""{"api_base_url": "ftp://x"}""", allowCleartext = false)).isNull()
    }

    @Test fun una_politica_de_privacidad_sin_https_se_descarta_sin_tumbar_el_resto() {
        val c = parseServerConfig("""{"api_base_url": "https://api.tienda.example", "privacy_url": "http://x"}""", allowCleartext = false)!!
        assertThat(c.privacyUrl).isNull()
    }

    @Test fun lo_roto_o_incompleto_no_se_acepta() {
        assertThat(parseServerConfig("no es json", allowCleartext = false)).isNull()
        assertThat(parseServerConfig("""{"version": 1}""", allowCleartext = false)).isNull()
        assertThat(parseServerConfig("""{"api_base_url": ""}""", allowCleartext = false)).isNull()
    }

    @Test fun en_debug_se_acepta_http_solo_hacia_el_emulador_y_localhost() {
        assertThat(parseServerConfig("""{"api_base_url": "http://10.0.2.2:8010"}""", allowCleartext = true)?.apiBaseUrl?.port).isEqualTo(8010)
        assertThat(parseServerConfig("""{"api_base_url": "http://98.88.0.1"}""", allowCleartext = true)).isNull()
    }
}
