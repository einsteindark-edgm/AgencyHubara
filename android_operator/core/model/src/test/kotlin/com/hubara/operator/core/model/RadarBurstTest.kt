package com.hubara.operator.core.model

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class RadarBurstTest {

    private fun grave(session: String, title: String = "$session pide un humano") = Fire(
        id = FireId.parse("chat:$session")!!,
        subject = FireSubject.Chat(SessionId.parse(session)!!),
        severity = Severity.GRAVE, kind = FireKind.WANTS_HUMAN, gettingWorse = false,
        title = title, subtitle = "", primaryAction = ActionRef("open_chat"), decidedBy = "rules", updatedMs = 0,
    )

    private val sofia = grave("wa_test_sofia")
    private val mateo = grave("wa_test_mateo")
    private val lucia = grave("wa_test_lucia")
    private val pedro = grave("wa_test_pedro")

    /** Lo que ya ardía al abrir la app se anunció y se plegó al chip. */
    private fun calm(): RadarBurst = RadarBurst().onRadar(listOf(sofia), expanded = false).folded()

    // En el emulador (09-30): con el operador escribiendo llegaron Mateo, Lucía y Pedro con 2 s de diferencia
    // y cada tarjeta reemplazaba a la anterior: la de Mateo se vio 2 s y la de Lucía casi nada.
    @Test fun tres_incendios_seguidos_se_juntan_y_el_mas_nuevo_queda_arriba() {
        var burst = calm().onRadar(listOf(sofia, mateo), expanded = false)
        burst = burst.onRadar(listOf(sofia, mateo, lucia), expanded = false)
        burst = burst.onRadar(listOf(sofia, mateo, lucia, pedro), expanded = false)

        assertThat(burst.fires).containsExactly(pedro, lucia, mateo).inOrder()
        assertThat(burst.arrivals).isEqualTo(calm().arrivals + 3)
    }

    // La recarga junta señales: dos incendios pueden llegar en la misma lista. Arriba, el del mensaje más reciente.
    @Test fun si_llegan_dos_en_la_misma_recarga_queda_arriba_el_del_mensaje_mas_reciente() {
        val antes = mateo.copy(updatedMs = 1_000)
        val despues = lucia.copy(updatedMs = 2_500)

        val burst = calm().onRadar(listOf(sofia, antes, despues), expanded = false)

        assertThat(burst.fires).containsExactly(despues, antes).inOrder()
        assertThat(burst.arrivals).isEqualTo(calm().arrivals + 1)
    }

    @Test fun lo_resuelto_u_oculto_sale_de_la_rafaga_sin_reiniciar_la_espera() {
        val burst = calm().onRadar(listOf(sofia, mateo), expanded = false).onRadar(listOf(sofia, mateo, lucia), expanded = false)

        val after = burst.onRadar(listOf(sofia, lucia), expanded = false)

        assertThat(after.fires).containsExactly(lucia)
        assertThat(after.arrivals).isEqualTo(burst.arrivals)
    }

    @Test fun con_el_radar_desplegado_no_aparece_nada_solo_y_al_plegarlo_tampoco() {
        val open = calm().onRadar(listOf(sofia, mateo), expanded = true)
        assertThat(open.fires).isEmpty()

        val closed = open.onRadar(listOf(sofia, mateo), expanded = false)
        assertThat(closed.fires).isEmpty()
        assertThat(closed.arrivals).isEqualTo(open.arrivals)
    }

    @Test fun si_llegan_mas_de_tres_se_ven_tres_y_se_cuenta_el_resto() {
        val otros = (1..5).map { grave("wa_test_n$it") }
        var burst = calm()
        otros.indices.forEach { i -> burst = burst.onRadar(listOf(sofia) + otros.take(i + 1), expanded = false) }

        assertThat(burst.visible()).containsExactly(otros[4], otros[3], otros[2]).inOrder()
        assertThat(burst.overflow()).isEqualTo(2)
    }

    @Test fun un_incendio_que_se_apago_y_vuelve_a_prender_se_anuncia_otra_vez() {
        val burst = calm().onRadar(listOf(sofia, mateo), expanded = false).folded()
            .onRadar(listOf(sofia), expanded = false)

        val again = burst.onRadar(listOf(sofia, mateo), expanded = false)

        assertThat(again.fires).containsExactly(mateo)
        assertThat(again.arrivals).isEqualTo(burst.arrivals + 1)
    }

    @Test fun un_incendio_que_cambia_de_texto_no_es_nuevo_pero_la_tarjeta_muestra_lo_ultimo() {
        val burst = calm().onRadar(listOf(sofia, mateo), expanded = false)
        val peor = mateo.copy(title = "Mateo pide un humano · 3 mensajes", gettingWorse = true)

        val after = burst.onRadar(listOf(sofia, peor), expanded = false)

        assertThat(after.fires).containsExactly(peor)
        assertThat(after.arrivals).isEqualTo(burst.arrivals)
    }
}
