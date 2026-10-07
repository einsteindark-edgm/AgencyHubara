package com.hubara.operator.core.ui

import androidx.compose.runtime.Immutable
import androidx.compose.runtime.compositionLocalOf

/**
 * Lo que la sesión le ofrece a cualquier pantalla (lo provee el contenedor de la app). [openPrivacy] abre la política
 * de privacidad pública (Google Play la exige dentro de la app); null si el build no tiene URL.
 */
@Immutable
data class SessionActions(val signOut: (() -> Unit)? = null, val openPrivacy: (() -> Unit)? = null)

val LocalSessionActions = compositionLocalOf<SessionActions?> { null }
