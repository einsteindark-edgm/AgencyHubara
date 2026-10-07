package com.hubara.operator.core.data.screens

import kotlinx.coroutines.flow.Flow

/** Avisos de que algo cambió en el servidor, por dominio. Lo implementa `SyncEngine` con el SSE del dashboard. */
interface ServerChanges {
    val changes: Flow<String>
}
