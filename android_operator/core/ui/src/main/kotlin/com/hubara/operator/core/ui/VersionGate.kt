package com.hubara.operator.core.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.hubara.operator.core.designsystem.IconTile
import com.hubara.operator.core.designsystem.OperatorIcons
import com.hubara.operator.core.designsystem.Spacing

/**
 * La versión mínima la manda el servidor (`min_version_code` de su configuración). Con una app más vieja no se entra:
 * se pide actualizar desde Google Play, en vez de dejar que falle a medias contra un backend que cambió.
 */
@Composable
fun VersionGate(minVersionCode: Int, appVersionCode: Int, onUpdate: () -> Unit, content: @Composable () -> Unit) {
    if (appVersionCode >= minVersionCode) {
        content()
        return
    }
    Column(
        Modifier.fillMaxSize().safeDrawingPadding().padding(Spacing.xl),
        verticalArrangement = Arrangement.spacedBy(Spacing.lg, Alignment.CenterVertically),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        IconTile(
            OperatorIcons.Notifications, MaterialTheme.colorScheme.primaryContainer, MaterialTheme.colorScheme.onPrimaryContainer,
            size = 72.dp, shape = MaterialTheme.shapes.extraLargeIncreased,
        )
        Text("Hay una versión nueva", style = MaterialTheme.typography.headlineSmallEmphasized, textAlign = TextAlign.Center)
        Text(
            "Esta versión de la app ya no funciona con el servidor. Actualízala en Google Play para seguir atendiendo.",
            style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center,
        )
        Button(onClick = onUpdate, modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp)) {
            Text("Actualizar", style = MaterialTheme.typography.titleSmall)
        }
    }
}
