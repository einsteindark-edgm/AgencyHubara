package com.hubara.operator.feature.inbox

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hubara.operator.core.data.repo.ConversationRepository
import com.hubara.operator.core.model.Conversation
import com.hubara.operator.core.model.OrderRef
import com.hubara.operator.core.model.Route
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.collections.immutable.ImmutableList
import kotlinx.collections.immutable.persistentListOf
import kotlinx.collections.immutable.toImmutableList
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

enum class InboxFilter(val label: String) {
    TODOS("Todos"), SIN_RESPONDER("Sin responder"), HUMANO("Humano"), CON_PEDIDO("Con pedido");

    fun apply(list: List<Conversation>): List<Conversation> = when (this) {
        TODOS -> list
        SIN_RESPONDER -> list.filter { it.unansweredCount > 0 }
        HUMANO -> list.filter { it.route == Route.HUMAN }
        CON_PEDIDO -> list.filter { it.orderRef != null }
    }
}

/** El teléfono en formato legible. En notificaciones y widget NO se muestra (privacidad). */
fun displayPhone(raw: String): String {
    val digits = raw.filter(Char::isDigit)
    return when {
        digits.isEmpty() -> "Cliente"
        digits.length == 12 && digits.startsWith("57") ->
            "+57 ${digits.substring(2, 5)} ${digits.substring(5, 8)} ${digits.substring(8)}"
        else -> raw
    }
}

/** Segunda línea de la fila: quién atiende, en qué va y si tiene pedido. Sin códigos del backend. */
fun conversationSubtitle(route: Route, tag: String, orderRef: OrderRef?): String {
    val who = if (route == Route.HUMAN) "Humano" else "Bot"
    val stage = TAG_LABELS[tag]?.takeIf { it != who }
    val order = orderRef?.let { ref -> ref.displayId?.let { "Pedido #$it" } ?: "Pedido" }
    return listOfNotNull(who, stage, order).joinToString(" · ")
}

/** Las etiquetas que escribe el bot, con los nombres de la bandeja del dashboard web (`chat/api.ts`). */
private val TAG_LABELS = mapOf(
    "INTERESADO" to "Interesado",
    "COMPRA_EXITOSA" to "Cliente",
    "CLIENTE" to "Cliente",
    "RECHAZO" to "Frío",
    "FRÍO" to "Frío",
    "FRIO" to "Frío",
    "SIN_RESPUESTA" to "Sin respuesta",
    "CONFIRMADO_SIN_DATOS" to "Pendiente",
    "CONFIRMADO_PAGO_PENDIENTE" to "Pendiente",
    "NO_ETIQUETADO" to "Pendiente",
    "PENDIENTE" to "Pendiente",
    "REMARKETING" to "Remarketing",
    "HUMANO" to "Humano",
)

data class InboxUiState(
    val filter: InboxFilter = InboxFilter.TODOS,
    val conversations: ImmutableList<Conversation> = persistentListOf(),
    val offline: Boolean = false,
)

@HiltViewModel
class InboxViewModel @Inject constructor(private val repo: ConversationRepository) : ViewModel() {
    private val filter = MutableStateFlow(InboxFilter.TODOS)
    private val offline = MutableStateFlow(false)

    val state: StateFlow<InboxUiState> = combine(repo.observeInbox(), filter, offline) { list, f, off ->
        InboxUiState(f, f.apply(list).toImmutableList(), off)
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), InboxUiState())

    init { refresh() }

    fun setFilter(f: InboxFilter) { filter.value = f }

    fun refresh() {
        viewModelScope.launch { offline.value = repo.refresh().isFailure }
    }
}
