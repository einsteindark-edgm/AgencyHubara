package com.hubara.operator.core.ui

/** Qué muestra una lista que se llena desde la caché y se recarga del servidor. */
enum class ListContent { LOADING, EMPTY, ERROR, LIST }

/** Lo guardado se muestra siempre; vacía solo cuenta como «vacía» (o error) después de la primera recarga. */
fun listContent(isEmpty: Boolean, refreshed: Boolean, failed: Boolean): ListContent = when {
    !isEmpty -> ListContent.LIST
    !refreshed -> ListContent.LOADING
    failed -> ListContent.ERROR
    else -> ListContent.EMPTY
}
