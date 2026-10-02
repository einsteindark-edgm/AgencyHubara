package com.hubara.operator.core.push

import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.model.ActionRef
import com.hubara.operator.core.model.Fire
import com.hubara.operator.core.model.FireId
import com.hubara.operator.core.model.FireKind
import com.hubara.operator.core.model.FireSubject
import com.hubara.operator.core.model.OrderId
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.model.Severity
import org.junit.Test

class NotificationsTest {
    private fun fire(id: String, subject: FireSubject, action: String = "open_chat") =
        Fire(FireId.parse(id)!!, subject, Severity.GRAVE, FireKind.OTHER, false, "t", "", ActionRef(action), "rules", 0)

    private val sofia = SessionId.parse("wa_test_sofia")!!

    @Test fun cada_aviso_abre_su_caso() {
        assertThat(deepLinkFor(fire("chat:wa_test_sofia", FireSubject.Chat(sofia)))).isEqualTo("hubara://chat/wa_test_sofia")
        val order = OrderId.parse("order_01HX")!!
        assertThat(deepLinkFor(fire("order:order_01HX", FireSubject.Order(order, sofia), "open_order"))).isEqualTo("hubara://order/order_01HX")
        assertThat(deepLinkFor(fire("order:order_01HX", FireSubject.Order(null, null), "open_order"))).isNull()
    }

    @Test fun solo_se_avisan_los_graves_nuevos() {
        val a = fire("chat:wa_a", FireSubject.Chat(SessionId.parse("wa_a")!!))
        val b = fire("chat:wa_b", FireSubject.Chat(SessionId.parse("wa_b")!!))
        assertThat(firesToNotify(listOf(a, b), alreadyNotified = setOf("chat:wa_a"))).containsExactly(b)
        assertThat(firesToNotify(listOf(a), alreadyNotified = setOf("chat:wa_a"))).isEmpty()
    }
}
