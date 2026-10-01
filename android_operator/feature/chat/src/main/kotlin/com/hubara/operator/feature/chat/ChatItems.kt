package com.hubara.operator.feature.chat

import com.hubara.operator.core.model.Author
import com.hubara.operator.core.model.Message
import com.hubara.operator.core.ui.dayLabel
import java.time.Instant
import java.time.ZoneId

/** Dónde cae una burbuja en su grupo (orden cronológico: FIRST arriba). */
enum class BubblePosition { SINGLE, FIRST, MIDDLE, LAST }

sealed interface ChatItem {
    data class Day(val label: String) : ChatItem
    data class Bubble(val message: Message, val position: BubblePosition) : ChatItem
}

/** Más de esto entre dos mensajes del mismo autor y ya son grupos distintos. */
private const val GROUP_GAP_MS = 5 * 60_000L

/**
 * Los mensajes en orden cronológico con sus separadores de día y su lugar en el grupo: seguidos, del mismo autor,
 * el mismo día y a menos de 5 minutos. Un mensaje sin hora va con el anterior.
 */
fun chatItems(messages: List<Message>, nowMs: Long, zone: ZoneId): List<ChatItem> {
    fun day(ms: Long) = Instant.ofEpochMilli(ms).atZone(zone).toLocalDate()
    fun joins(prev: Message, next: Message): Boolean {
        if (prev.author != next.author || prev.author == Author.SYSTEM) return false
        val a = prev.timestampMs ?: return true
        val b = next.timestampMs ?: return true
        return day(a) == day(b) && b - a <= GROUP_GAP_MS
    }

    val out = ArrayList<ChatItem>(messages.size + 4)
    var lastDay: java.time.LocalDate? = null
    messages.forEachIndexed { i, m ->
        m.timestampMs?.let { ms ->
            val d = day(ms)
            if (d != lastDay) {
                out += ChatItem.Day(dayLabel(ms, nowMs, zone))
                lastDay = d
            }
        }
        val withPrev = i > 0 && joins(messages[i - 1], m) && out.last() !is ChatItem.Day
        val withNext = i < messages.lastIndex && joins(m, messages[i + 1]) &&
            messages[i + 1].timestampMs?.let { day(it) != lastDay } != true
        out += ChatItem.Bubble(
            m,
            when {
                withPrev && withNext -> BubblePosition.MIDDLE
                withPrev -> BubblePosition.LAST
                withNext -> BubblePosition.FIRST
                else -> BubblePosition.SINGLE
            },
        )
    }
    return out
}
