package com.hubara.operator.core.designsystem

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.minimumInteractiveComponentSize
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp

/**
 * Una burbuja de acción: tocar envía, mantener presionado edita. La principal va rellena con el primario; las
 * demás, tonales (secundario). [order] = «Crear pedido»: va en verde (el par de éxito) para verse de un vistazo,
 * esté primera o no. Es un `Surface` propio (no `SuggestionChip`) porque necesita las dos acciones.
 * Se ve de 40 dp pero responde en 48 dp (área táctil mínima de Material).
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun SuggestionBubble(
    label: String,
    primary: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    onLongClick: (() -> Unit)? = null,
    icon: ImageVector? = null,
    order: Boolean = false,
) {
    val colors = MaterialTheme.colorScheme
    val brand = OperatorTheme.colors
    Surface(
        shape = CircleShape,
        color = when {
            order -> brand.successContainer
            primary -> colors.primary
            else -> colors.secondaryContainer
        },
        contentColor = when {
            order -> brand.onSuccessContainer
            primary -> colors.onPrimary
            else -> colors.onSecondaryContainer
        },
        modifier = modifier
            .minimumInteractiveComponentSize()
            .defaultMinSize(minHeight = 40.dp)
            .combinedClickable(
                role = Role.Button,
                onClick = onClick,
                onLongClick = onLongClick,
                onLongClickLabel = if (onLongClick != null) "Editar antes de enviar" else null,
            ),
    ) {
        Row(
            Modifier.padding(start = if (icon != null) 12.dp else 16.dp, end = 16.dp, top = 10.dp, bottom = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            icon?.let { Icon(it, contentDescription = null, modifier = Modifier.size(18.dp)) }
            Text(label, style = MaterialTheme.typography.labelLarge)
        }
    }
}
