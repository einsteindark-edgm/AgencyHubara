package com.hubara.operator.core.network

import kotlinx.serialization.json.Json

/** Un solo `Json` para toda la app: tolera campos nuevos del backend y no exige nulos explícitos. */
val OperatorJson: Json = Json {
    ignoreUnknownKeys = true
    explicitNulls = false
    // Un campo con valor por defecto igual viaja: el backend no conoce los defaults de Kotlin
    // (`target_route = "ventas"` salía como `{}` y devolver al bot daba 422).
    encodeDefaults = true
    coerceInputValues = true
    isLenient = false
}
