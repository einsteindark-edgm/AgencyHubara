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

    /**
     * Los íconos por nombre, para las pantallas que define el servidor (`"icon": "campaign"`). Los nombres son los del
     * catálogo (`Catalog.icons` en `:core:sdui`); un test de `:feature:screens` exige que estén todos.
     */
    private val byName: Map<String, Int> = mapOf(
        "add" to R.drawable.ds_ic_add, "apps" to R.drawable.ds_ic_apps, "arrow_back" to R.drawable.ds_ic_arrow_back,
        "bar_chart" to R.drawable.ds_ic_bar_chart, "bolt" to R.drawable.ds_ic_bolt, "bot" to R.drawable.ds_ic_bot,
        "calendar" to R.drawable.ds_ic_calendar, "call" to R.drawable.ds_ic_call, "campaign" to R.drawable.ds_ic_campaign,
        "chat" to R.drawable.ds_ic_chat, "chat_filled" to R.drawable.ds_ic_chat_filled, "check" to R.drawable.ds_ic_check, "check_circle" to R.drawable.ds_ic_check_circle,
        "chevron_right" to R.drawable.ds_ic_chevron_right, "close" to R.drawable.ds_ic_close, "edit" to R.drawable.ds_ic_edit,
        "error" to R.drawable.ds_ic_error, "filter" to R.drawable.ds_ic_filter, "fire" to R.drawable.ds_ic_fire, "fire_filled" to R.drawable.ds_ic_fire_filled,
        "group" to R.drawable.ds_ic_group, "info" to R.drawable.ds_ic_info, "inventory" to R.drawable.ds_ic_inventory,
        "link" to R.drawable.ds_ic_link, "location" to R.drawable.ds_ic_location, "mail" to R.drawable.ds_ic_mail,
        "more_vert" to R.drawable.ds_ic_more_vert, "notifications" to R.drawable.ds_ic_notifications,
        "orders" to R.drawable.ds_ic_orders, "orders_filled" to R.drawable.ds_ic_orders_filled, "payments" to R.drawable.ds_ic_payments, "person" to R.drawable.ds_ic_person,
        "receipt" to R.drawable.ds_ic_receipt, "refresh" to R.drawable.ds_ic_refresh, "schedule" to R.drawable.ds_ic_schedule,
        "search" to R.drawable.ds_ic_search, "sell" to R.drawable.ds_ic_sell, "send" to R.drawable.ds_ic_send,
        "settings" to R.drawable.ds_ic_settings, "shipping" to R.drawable.ds_ic_shipping, "star" to R.drawable.ds_ic_star,
        "storefront" to R.drawable.ds_ic_storefront, "trending_down" to R.drawable.ds_ic_trending_down,
        "trending_up" to R.drawable.ds_ic_trending_up, "undo" to R.drawable.ds_ic_undo, "warning" to R.drawable.ds_ic_warning,
    )

    val names: Set<String> get() = byName.keys

    /** El ícono de ese nombre, o null si esta versión de la app no lo trae (la pantalla se pinta sin él). */
    @Composable
    fun named(name: String?): ImageVector? = byName[name]?.let { ImageVector.vectorResource(it) }
}
