@file:OptIn(ExperimentalMaterial3ExpressiveApi::class)

package com.hubara.operator.core.designsystem

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.LoadingIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay

/** Lo que oye TalkBack mientras una pantalla espera sus datos. Fijo: nada que cambie cada segundo (gotcha 13). */
const val LOADING_DESCRIPTION = "Cargando…"

/** Lo que oye TalkBack en el botón que espera al backend. */
const val WORKING_DESCRIPTION = "Trabajando…"

/**
 * Espera antes de mostrar el indicador: una carga rápida no hace parpadear la pantalla (como `ContentLoadingProgressBar`).
 */
const val LOADING_SHOW_AFTER_MS = 300L

/**
 * La pantalla todavía no tiene NADA que mostrar: el indicador expresivo de Material 3, centrado. Solo para eso; si hay
 * algo guardado se muestra y la recarga va callada. La descripción es fija y no anuncia progreso.
 */
@Composable
fun LoadingState(modifier: Modifier = Modifier, description: String = LOADING_DESCRIPTION, showAfterMs: Long = LOADING_SHOW_AFTER_MS) {
    var shown by remember { mutableStateOf(showAfterMs <= 0) }
    LaunchedEffect(Unit) {
        delay(showAfterMs)
        shown = true
    }
    Box(modifier.fillMaxWidth().padding(Spacing.xxl), contentAlignment = Alignment.Center) {
        if (shown) LoadingIndicator(Modifier.semantics { contentDescription = description })
    }
}

/** El indicador chico de un botón que espera al backend (va en el lugar del ícono). */
@Composable
fun WorkingIndicator(modifier: Modifier = Modifier, size: Dp = ButtonDefaults.IconSize + 4.dp) {
    LoadingIndicator(modifier.size(size).semantics { contentDescription = WORKING_DESCRIPTION })
}

/** No se pudo cargar y no hay nada guardado: qué pasó y cómo reintentar. Nunca un indicador eterno. */
@Composable
fun LoadError(
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
    title: String = "No se pudieron cargar los datos",
    body: String = "Revisa la conexión y vuelve a intentar.",
    button: String = "Reintentar",
) {
    Column(modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
        EmptyState(OperatorIcons.Error, title, body)
        Button(onClick = onRetry, shapes = ButtonDefaults.shapes()) { Text(button) }
    }
}

@Preview(showBackground = true)
@Composable
private fun LoadingPreview() = OperatorTheme {
    Column {
        LoadingState(showAfterMs = 0)
        LoadError(onRetry = {})
    }
}
