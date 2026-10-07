package com.hubara.operator.core.data

import javax.inject.Qualifier
import kotlin.coroutines.cancellation.CancellationException

fun interface Clock {
    fun nowMs(): Long

    companion object {
        val SYSTEM = Clock { System.currentTimeMillis() }
    }
}

/** Scope de la app: vive mientras vive el proceso (sync, refrescos en segundo plano). */
@Qualifier
@Retention(AnnotationRetention.BINARY)
annotation class ApplicationScope

/** `runCatching` que NO se traga la cancelación de corrutinas. */
suspend fun <T> safeCall(block: suspend () -> T): Result<T> = try {
    Result.success(block())
} catch (e: CancellationException) {
    throw e
} catch (e: Exception) {
    Result.failure(e)
}
