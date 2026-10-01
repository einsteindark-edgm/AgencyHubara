package com.hubara.operator.core.designsystem

import androidx.compose.animation.core.FiniteAnimationSpec
import androidx.compose.animation.core.spring

/**
 * El movimiento de Material 3 Expressive: resortes con rebote en lo que se desplaza (espacial) y sin rebote en color
 * y opacidad (efectos). Mismos valores que `ExpressiveMotionTokens` de material3. En la 1.4 estable el
 * `MotionScheme` y `MaterialExpressiveTheme` son internos, así que los componentes propios (radar, burbujas,
 * tarjetas) toman los resortes de aquí. Con material3 1.5 estable: `MaterialExpressiveTheme` y se borra esto.
 */
object ExpressiveMotion {
    fun <T> defaultSpatial(): FiniteAnimationSpec<T> = spring(dampingRatio = 0.8f, stiffness = 380f)
    fun <T> fastSpatial(): FiniteAnimationSpec<T> = spring(dampingRatio = 0.6f, stiffness = 800f)
    fun <T> slowSpatial(): FiniteAnimationSpec<T> = spring(dampingRatio = 0.8f, stiffness = 200f)
    fun <T> defaultEffects(): FiniteAnimationSpec<T> = spring(dampingRatio = 1f, stiffness = 1600f)
    fun <T> fastEffects(): FiniteAnimationSpec<T> = spring(dampingRatio = 1f, stiffness = 3800f)
    fun <T> slowEffects(): FiniteAnimationSpec<T> = spring(dampingRatio = 1f, stiffness = 800f)
}
