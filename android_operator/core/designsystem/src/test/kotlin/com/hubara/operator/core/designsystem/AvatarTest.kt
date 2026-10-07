package com.hubara.operator.core.designsystem

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/** El avatar de la bandeja: iniciales del nombre de perfil, o nada (ícono de persona) si solo hay número. */
class AvatarTest {
    @Test fun dos_iniciales_del_nombre_y_apellido() {
        assertThat(initials("Laura Prueba")).isEqualTo("LP")
        assertThat(initials("  andrés   gómez  pérez ")).isEqualTo("AG")
        assertThat(initials("ñandú")).isEqualTo("Ñ")
    }

    @Test fun ignora_emojis_y_simbolos_delante_del_nombre() {
        assertThat(initials("🔥 Sofía")).isEqualTo("S")
        assertThat(initials("~ Mateo ✨ Prueba")).isEqualTo("MP")
    }

    @Test fun sin_letras_no_hay_iniciales() {
        assertThat(initials(null)).isNull()
        assertThat(initials("   ")).isNull()
        assertThat(initials("+57 300 000 0000")).isNull()
        assertThat(initials("🔥🔥")).isNull()
    }

    @Test fun el_tono_del_avatar_es_estable_y_varia_entre_clientes() {
        val tones = (100..140).map { avatarTone("wa_000000000$it") }
        assertThat(tones.all { it in 0 until AVATAR_TONES }).isTrue()
        assertThat(avatarTone("wa_000000000101")).isEqualTo(avatarTone("wa_000000000101"))
        assertThat(tones.toSet().size).isGreaterThan(1)
    }
}
