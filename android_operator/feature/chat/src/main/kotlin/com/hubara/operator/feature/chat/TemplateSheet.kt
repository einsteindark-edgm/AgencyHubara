package com.hubara.operator.feature.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import com.hubara.operator.core.designsystem.segmentedShape
import com.hubara.operator.core.designsystem.Spacing
import com.hubara.operator.core.designsystem.SegmentGap
import com.hubara.operator.core.designsystem.OperatorTheme
import com.hubara.operator.core.designsystem.ExpressiveShapes
import androidx.compose.foundation.layout.heightIn
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.Alignment
import androidx.compose.material3.Surface
import androidx.compose.material3.ListItemDefaults
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import com.hubara.operator.core.data.outbox.OutboxRepository
import com.hubara.operator.core.data.repo.TemplateRepository
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.model.Template
import dagger.assisted.Assisted
import dagger.assisted.AssistedFactory
import dagger.assisted.AssistedInject
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

data class TemplateSheetState(
    val loading: Boolean = true,
    val templates: List<Template> = emptyList(),
    val selected: Template? = null,
    val error: String? = null,
)

@HiltViewModel(assistedFactory = TemplateSheetViewModel.Factory::class)
class TemplateSheetViewModel @AssistedInject constructor(
    @Assisted sessionRaw: String,
    private val templates: TemplateRepository,
    private val outbox: OutboxRepository,
) : ViewModel() {
    private val sessionId = requireNotNull(SessionId.parse(sessionRaw))
    private val _state = MutableStateFlow(TemplateSheetState())
    val state: StateFlow<TemplateSheetState> = _state.asStateFlow()

    init {
        viewModelScope.launch {
            templates.list()
                .onSuccess { list -> _state.value = TemplateSheetState(loading = false, templates = list, selected = list.firstOrNull { it.isDefault }) }
                .onFailure { _state.value = TemplateSheetState(loading = false, error = "No se pudieron cargar las plantillas.") }
        }
    }

    fun select(t: Template?) { _state.value = _state.value.copy(selected = t) }

    fun send(values: Map<String, String>) {
        val t = _state.value.selected ?: return
        viewModelScope.launch { outbox.sendTemplate(sessionId, t, values) }
    }

    @AssistedFactory
    interface Factory { fun create(sessionRaw: String): TemplateSheetViewModel }
}

@Composable
fun TemplateSheet(vm: TemplateSheetViewModel, onDone: () -> Unit) {
    val ui by vm.state.collectAsStateWithLifecycle()
    // La hoja ya aplica las barras del sistema y el teclado (ModalBottomSheet consume safeDrawing).
    Column(
        Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(start = Spacing.lg, end = Spacing.lg, bottom = Spacing.xl),
        verticalArrangement = Arrangement.spacedBy(Spacing.md),
    ) {
        Text("Reactivar la conversación", style = OperatorTheme.emphasized.titleLarge)
        Text("La ventana de 24 h está cerrada: solo se puede escribir con una plantilla aprobada.",
            style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        val selected = ui.selected
        when {
            ui.loading -> CircularProgressIndicator(Modifier.align(Alignment.CenterHorizontally))
            ui.error != null -> Text(ui.error!!, color = MaterialTheme.colorScheme.error)
            selected == null -> Column(verticalArrangement = Arrangement.spacedBy(SegmentGap)) {
                ui.templates.forEachIndexed { i, t ->
                    Surface(
                        onClick = { vm.select(t) },
                        enabled = !t.needsImage,
                        shape = segmentedShape(i, ui.templates.size),
                        color = MaterialTheme.colorScheme.surfaceContainerHigh,
                    ) {
                        ListItem(
                            headlineContent = { Text(t.label, style = OperatorTheme.emphasized.bodyLarge) },
                            supportingContent = {
                                Text(if (t.needsImage) "Lleva foto: todavía se envía desde el dashboard" else t.body.orEmpty(), maxLines = 2)
                            },
                            colors = ListItemDefaults.colors(containerColor = Color.Transparent),
                        )
                    }
                }
            }
            else -> TemplateForm(selected, onBack = { vm.select(null) }, onSend = { values -> vm.send(values); onDone() })
        }
    }
}

@Composable
private fun TemplateForm(t: Template, onBack: () -> Unit, onSend: (Map<String, String>) -> Unit) {
    val values = remember(t.name) { mutableStateMapOf<String, String>() }
    Text(t.label, style = OperatorTheme.emphasized.labelLarge, color = MaterialTheme.colorScheme.primary)
    t.variables.forEach { v ->
        OutlinedTextField(
            value = values[v.name].orEmpty(),
            onValueChange = { new -> values[v.name] = v.maxLength?.let { new.take(it) } ?: new },
            label = { Text(v.description ?: v.name) },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
    }
    // Vista previa como la verá el cliente: una burbuja.
    Surface(shape = ExpressiveShapes.largeIncreased, color = OperatorTheme.colors.bubbleOperator, contentColor = OperatorTheme.colors.onBubbleOperator) {
        Text(t.preview(values), style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(Spacing.md))
    }
    Button(
        onClick = { onSend(values.toMap()) }, enabled = t.missing(values).isEmpty(),
        modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp),
    ) { Text("Enviar plantilla", style = MaterialTheme.typography.titleSmall) }
    TextButton(onClick = onBack) { Text("Elegir otra") }
}
