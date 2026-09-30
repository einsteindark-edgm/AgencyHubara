package com.hubara.operator.core.database

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

/** Una fila de la bandeja, más los datos del chat que llegan con el detalle (ventana de 24 h). */
@Entity(tableName = "conversations")
data class ConversationEntity(
    @PrimaryKey val sessionId: String,
    val phone: String,
    val tag: String,
    val route: String,
    val lastUpdatedMs: Long,
    val lastInboundMs: Long?,
    val inboundCount: Int,
    val orderId: String?,
    val orderDisplayId: String?,
    val orderPayment: String?,
    val orderCount: Int,
    val windowExpiresAtMs: Long? = null,
)

@Entity(tableName = "messages", primaryKeys = ["sessionId", "key"], indices = [Index("sessionId", "position")])
data class MessageEntity(
    val sessionId: String,
    val key: String,
    val position: Int,
    val author: String,
    val text: String?,
    val imageUrl: String?,
    val timestampMs: Long?,
)

/** Envíos del operador que todavía no confirmó el servidor. */
@Entity(tableName = "outbox", indices = [Index("sessionId")])
data class OutboxEntity(
    @PrimaryKey val clientActionId: String,
    val sessionId: String,
    /** `text` o `tool`. */
    val kind: String,
    val text: String?,
    val toolName: String?,
    val argsJson: String?,
    /** Lo que ve el operador en la burbuja pendiente («Enviar aromas»). */
    val label: String,
    /** `pending_undo`, `queued`, `sent`, `failed`. */
    val state: String,
    val error: String?,
    val createdMs: Long,
)

@Entity(tableName = "drafts")
data class DraftEntity(
    @PrimaryKey val sessionId: String,
    val text: String,
    val updatedMs: Long,
)

@Entity(tableName = "suggestion_sets")
data class SuggestionSetEntity(
    @PrimaryKey val sessionId: String,
    val version: Long,
    val json: String,
)

@Entity(tableName = "fires")
data class FireEntity(
    @PrimaryKey val fireId: String,
    val severity: Int,
    val updatedMs: Long,
    val json: String,
)

/** Ocultar ≠ resolver: el incendio sigue en el feed, pero no vuelve al radar. */
@Entity(tableName = "hidden_fires")
data class HiddenFireEntity(
    @PrimaryKey val fireId: String,
    val hiddenAtMs: Long,
)
