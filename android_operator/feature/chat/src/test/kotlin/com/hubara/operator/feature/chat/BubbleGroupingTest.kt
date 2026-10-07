package com.hubara.operator.feature.chat

import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.model.Author
import com.hubara.operator.core.model.Message
import java.time.ZoneId
import java.time.ZonedDateTime
import org.junit.Test

/**
 * Las burbujas seguidas del mismo autor se agrupan (esquinas pegadas, menos aire) y cada día abre con su separador,
 * como en las apps de mensajería. Un mensaje de sistema corta el grupo.
 */
class BubbleGroupingTest {
    private val bogota = ZoneId.of("America/Bogota")
    private fun at(d: Int, h: Int, mi: Int) = ZonedDateTime.of(2026, 9, d, h, mi, 0, 0, bogota).toInstant().toEpochMilli()
    private val now = at(30, 18, 0)
    private var n = 0
    private fun msg(author: Author, ms: Long?) = Message(key = "m${n++}", author = author, text = "hola", imageUrl = null, timestampMs = ms)

    private fun shape(items: List<ChatItem>) = items.map {
        when (it) {
            is ChatItem.Day -> "[${it.label}]"
            is ChatItem.Bubble -> "${it.message.author.name.first()}:${it.position.name.first()}"
        }
    }

    @Test fun agrupa_los_seguidos_del_mismo_autor_y_separa_por_dia() {
        val items = chatItems(
            listOf(
                msg(Author.CUSTOMER, at(29, 20, 0)),
                msg(Author.CUSTOMER, at(29, 20, 1)),
                msg(Author.CUSTOMER, at(29, 20, 2)),
                msg(Author.BOT, at(29, 20, 3)),
                msg(Author.CUSTOMER, at(30, 9, 0)),
                msg(Author.HUMAN, at(30, 9, 5)),
                msg(Author.HUMAN, at(30, 9, 6)),
            ),
            now, bogota,
        )
        assertThat(shape(items)).containsExactly(
            "[Ayer]", "C:F", "C:M", "C:L", "B:S",
            "[Hoy]", "C:S", "H:F", "H:L",
        ).inOrder()
    }

    @Test fun mas_de_cinco_minutos_o_un_mensaje_de_sistema_cortan_el_grupo() {
        val items = chatItems(
            listOf(
                msg(Author.BOT, at(30, 9, 0)),
                msg(Author.BOT, at(30, 9, 6)),
                msg(Author.SYSTEM, at(30, 9, 7)),
                msg(Author.BOT, at(30, 9, 8)),
            ),
            now, bogota,
        )
        assertThat(shape(items)).containsExactly("[Hoy]", "B:S", "B:S", "S:S", "B:S").inOrder()
    }

    @Test fun sin_hora_se_agrupa_con_el_anterior_y_no_abre_dia() {
        val items = chatItems(listOf(msg(Author.HUMAN, at(30, 9, 0)), msg(Author.HUMAN, null)), now, bogota)
        assertThat(shape(items)).containsExactly("[Hoy]", "H:F", "H:L").inOrder()
    }
}
