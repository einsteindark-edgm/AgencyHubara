package com.hubara.operator.core.database

import androidx.room.testing.MigrationTestHelper
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.google.common.truth.Truth.assertThat
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Actualizar la app no puede perder lo que el operador tiene en el teléfono (outbox sin enviar, borradores, bandeja):
 * cada migración de Room corre sobre una base real de la versión vieja y se valida contra el esquema exportado.
 * La auditoría encontró dos AutoMigration (1→2 renombra una columna) sin ningún test.
 */
@RunWith(AndroidJUnit4::class)
class MigrationTest {
    @get:Rule val helper = MigrationTestHelper(InstrumentationRegistry.getInstrumentation(), OperatorDatabase::class.java)

    @Test fun de_la_version_1_a_la_3_conserva_bandeja_outbox_y_borradores() {
        helper.createDatabase(DB, 1).use { db ->
            db.execSQL(
                "INSERT INTO conversations (sessionId, phone, tag, route, lastUpdatedMs, lastInboundMs, unansweredCount, " +
                    "orderId, orderDisplayId, orderPayment, orderCount, windowExpiresAtMs) " +
                    "VALUES ('wa_000000000101', '000000000101', 'INTERESADO', 'ventas', 1, 1, 4, NULL, NULL, NULL, 0, NULL)",
            )
            db.execSQL(
                "INSERT INTO outbox (clientActionId, sessionId, kind, text, toolName, argsJson, label, state, error, createdMs) " +
                    "VALUES ('ca-1', 'wa_000000000101', 'text', 'hola', NULL, NULL, 'hola', 'PENDING_UNDO', NULL, 1)",
            )
            db.execSQL("INSERT INTO drafts (sessionId, text, updatedMs) VALUES ('wa_000000000101', 'te confirmo el', 1)")
        }

        helper.runMigrationsAndValidate(DB, 3, true).use { db ->
            db.query("SELECT inboundCount, customerName, lastMessagePreview FROM conversations WHERE sessionId = 'wa_000000000101'").use { c ->
                assertThat(c.moveToFirst()).isTrue()
                assertThat(c.getInt(0)).isEqualTo(4)       // unansweredCount → inboundCount (v2)
                assertThat(c.isNull(1)).isTrue()           // columnas nuevas de v3, vacías
                assertThat(c.isNull(2)).isTrue()
            }
            db.query("SELECT text FROM outbox WHERE clientActionId = 'ca-1'").use { c ->
                assertThat(c.moveToFirst()).isTrue()
                assertThat(c.getString(0)).isEqualTo("hola")
            }
            db.query("SELECT text FROM drafts WHERE sessionId = 'wa_000000000101'").use { c ->
                assertThat(c.moveToFirst()).isTrue()
                assertThat(c.getString(0)).isEqualTo("te confirmo el")
            }
        }
    }

    @Test fun de_la_version_3_a_la_4_conserva_los_mensajes_sin_marca_de_tardio() {
        helper.createDatabase(DB, 3).use { db ->
            db.execSQL(
                "INSERT INTO messages (sessionId, key, position, author, text, imageUrl, timestampMs) " +
                    "VALUES ('wa_000000000101', 'w1', 0, 'CUSTOMER', 'hola', NULL, 1)",
            )
        }

        helper.runMigrationsAndValidate(DB, 4, true).use { db ->
            db.query("SELECT text, sentAtMs, arrivedAfterWindow FROM messages WHERE key = 'w1'").use { c ->
                assertThat(c.moveToFirst()).isTrue()
                assertThat(c.getString(0)).isEqualTo("hola")
                assertThat(c.isNull(1)).isTrue()           // columnas nuevas de v4
                assertThat(c.getInt(2)).isEqualTo(0)
            }
        }
    }

    // 2026-10-13: actualizar la app no puede perder el historial guardado; las columnas nuevas nacen vacías.
    @Test fun de_la_version_4_a_la_5_conserva_los_mensajes_sin_tarjeta() {
        helper.createDatabase(DB, 4).use { db ->
            db.execSQL(
                "INSERT INTO messages (sessionId, key, position, author, text, imageUrl, timestampMs, arrivedAfterWindow) " +
                    "VALUES ('wa_000000000101', 'w1', 0, 'SYSTEM', '🛍️ El bot envió el catálogo con 2 productos', NULL, 1, 0)",
            )
        }

        helper.runMigrationsAndValidate(DB, 5, true).use { db ->
            db.query("SELECT text, cardJson, tap FROM messages WHERE key = 'w1'").use { c ->
                assertThat(c.moveToFirst()).isTrue()
                assertThat(c.getString(0)).isEqualTo("🛍️ El bot envió el catálogo con 2 productos")
                assertThat(c.isNull(1)).isTrue()           // columnas nuevas de v5
                assertThat(c.isNull(2)).isTrue()
            }
        }
    }

    // 2026-10-15: el doble check de WhatsApp; el historial guardado sigue y nace «sin confirmación» (columnas null).
    @Test fun de_la_version_5_a_la_6_conserva_los_mensajes_sin_check() {
        helper.createDatabase(DB, 5).use { db ->
            db.execSQL(
                "INSERT INTO messages (sessionId, key, position, author, text, imageUrl, timestampMs, arrivedAfterWindow) " +
                    "VALUES ('wa_000000000101', 'w1', 0, 'BOT', '¡Hola!', NULL, 1, 0)",
            )
        }

        helper.runMigrationsAndValidate(DB, 6, true).use { db ->
            db.query("SELECT text, waStatus, waStatusAtMs, waErrorCode, waErrorTitle FROM messages WHERE key = 'w1'").use { c ->
                assertThat(c.moveToFirst()).isTrue()
                assertThat(c.getString(0)).isEqualTo("¡Hola!")
                assertThat((1..4).all { c.isNull(it) }).isTrue()   // columnas nuevas de v6
            }
        }
    }

    private companion object {
        const val DB = "migracion-test.db"
    }
}
