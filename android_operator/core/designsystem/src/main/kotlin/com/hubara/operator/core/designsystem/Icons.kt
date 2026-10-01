package com.hubara.operator.core.designsystem

import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.res.vectorResource

/**
 * Íconos de Material Symbols Rounded (los de Material 3 Expressive) como vectores propios: sin la biblioteca
 * `material-icons-extended`, que pesa megas y ya no se actualiza. Los «filled» son para la pestaña elegida.
 */
object OperatorIcons {
    val Chat: ImageVector @Composable get() = ImageVector.vectorResource(R.drawable.ds_ic_chat)
    val ChatFilled: ImageVector @Composable get() = ImageVector.vectorResource(R.drawable.ds_ic_chat_filled)
    val Fire: ImageVector @Composable get() = ImageVector.vectorResource(R.drawable.ds_ic_fire)
    val FireFilled: ImageVector @Composable get() = ImageVector.vectorResource(R.drawable.ds_ic_fire_filled)
    val Orders: ImageVector @Composable get() = ImageVector.vectorResource(R.drawable.ds_ic_orders)
    val OrdersFilled: ImageVector @Composable get() = ImageVector.vectorResource(R.drawable.ds_ic_orders_filled)
    val Send: ImageVector @Composable get() = ImageVector.vectorResource(R.drawable.ds_ic_send)
    val ArrowBack: ImageVector @Composable get() = ImageVector.vectorResource(R.drawable.ds_ic_arrow_back)
    val MoreVert: ImageVector @Composable get() = ImageVector.vectorResource(R.drawable.ds_ic_more_vert)
    val Bot: ImageVector @Composable get() = ImageVector.vectorResource(R.drawable.ds_ic_bot)
    val Person: ImageVector @Composable get() = ImageVector.vectorResource(R.drawable.ds_ic_person)
    val Schedule: ImageVector @Composable get() = ImageVector.vectorResource(R.drawable.ds_ic_schedule)
    val Check: ImageVector @Composable get() = ImageVector.vectorResource(R.drawable.ds_ic_check)
    val Shipping: ImageVector @Composable get() = ImageVector.vectorResource(R.drawable.ds_ic_shipping)
    val Payments: ImageVector @Composable get() = ImageVector.vectorResource(R.drawable.ds_ic_payments)
    val Inventory: ImageVector @Composable get() = ImageVector.vectorResource(R.drawable.ds_ic_inventory)
    val Notifications: ImageVector @Composable get() = ImageVector.vectorResource(R.drawable.ds_ic_notifications)
    val Close: ImageVector @Composable get() = ImageVector.vectorResource(R.drawable.ds_ic_close)
    val Add: ImageVector @Composable get() = ImageVector.vectorResource(R.drawable.ds_ic_add)
    val Bolt: ImageVector @Composable get() = ImageVector.vectorResource(R.drawable.ds_ic_bolt)
    val Error: ImageVector @Composable get() = ImageVector.vectorResource(R.drawable.ds_ic_error)
    val Storefront: ImageVector @Composable get() = ImageVector.vectorResource(R.drawable.ds_ic_storefront)
    val Call: ImageVector @Composable get() = ImageVector.vectorResource(R.drawable.ds_ic_call)
    val Undo: ImageVector @Composable get() = ImageVector.vectorResource(R.drawable.ds_ic_undo)
}
