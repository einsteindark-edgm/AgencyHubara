package com.hubara.operator.core.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Modifier

/**
 * Lo que un componente nativo recibe de una pantalla del servidor: sus propiedades de texto ya evaluadas y sus
 * propiedades-acción como funciones (`on_more` → abrir la paleta, lo que diga el JSON).
 */
@Immutable
data class NativeProps(
    val values: Map<String, String>,
    val actions: Map<String, () -> Unit>,
    /** Abre una pantalla del servidor como hoja, con sus parámetros. */
    val open: (screen: String, params: Map<String, String>) -> Unit = { _, _ -> },
) {
    operator fun get(key: String): String? = values[key]

    fun action(key: String): (() -> Unit)? = actions[key]
}

/**
 * Una pieza nativa que las pantallas del servidor usan como componente (`{"type": "chat", "session": …}`): lo crítico
 * del teléfono que no se arma con componentes genéricos (el timeline y el composer del chat, el permiso de
 * notificaciones). Cada módulo registra las suyas por nombre (`@IntoMap @StringKey`); los nombres están en `Catalog`.
 */
interface NativeComponent {
    @Composable
    fun Content(props: NativeProps, modifier: Modifier)
}

/** Las piezas nativas registradas, por nombre. La app las provee en la raíz de la composición. */
val LocalNativeComponents = staticCompositionLocalOf<Map<String, NativeComponent>> { emptyMap() }
