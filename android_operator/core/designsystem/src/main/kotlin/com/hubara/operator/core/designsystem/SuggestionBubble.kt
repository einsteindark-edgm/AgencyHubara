package com.hubara.operator.core.designsystem

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.onLongClick
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp

/**
 * Una burbuja de acción: tocar envía, mantener presionado edita. La principal va rellena; las demás,
 * con borde. Es un `Surface` propio (no `SuggestionChip`) porque necesita las dos acciones.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun SuggestionBubble(
    label: String,
    primary: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    onLongClick: (() -> Unit)? = null,
) {
    val colors = MaterialTheme.colorScheme
    Surface(
        shape = RoundedCornerShape(18.dp),
        color = if (primary) colors.primary else colors.surface,
        contentColor = if (primary) colors.onPrimary else colors.onSurface,
        border = if (primary) null else BorderStroke(1.dp, colors.outline),
        modifier = modifier
            .defaultMinSize(minHeight = 40.dp)
            .semantics { if (onLongClick != null) onLongClick(label = "Editar antes de enviar") { onLongClick(); true } }
            .combinedClickable(role = Role.Button, onClick = onClick, onLongClick = onLongClick, onLongClickLabel = "Editar"),
    ) {
        Text(label, style = MaterialTheme.typography.labelLarge, modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp))
    }
}
