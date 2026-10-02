package com.hubara.operator.core.designsystem

/** Cuántos tonos de avatar hay (primario, secundario, terciario y sus variantes). */
const val AVATAR_TONES = 4

/** Iniciales (hasta dos) de un nombre de perfil; null si no tiene letras. Emojis y símbolos no cuentan. */
fun initials(name: String?): String? = name
    ?.split(Regex("\\s+"))
    ?.mapNotNull { word -> word.firstOrNull(Char::isLetter) }
    ?.take(2)
    ?.joinToString("") { it.uppercase() }
    ?.ifEmpty { null }

/** Tono estable del avatar de un cliente, en `0 until AVATAR_TONES`. */
fun avatarTone(key: String): Int = key.hashCode().mod(AVATAR_TONES)
