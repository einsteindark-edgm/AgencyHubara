package com.hubara.operator.core.data

import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.data.mapping.toEntity
import com.hubara.operator.core.data.repo.ConversationRepository
import com.hubara.operator.core.data.repo.SeenRepository
import com.hubara.operator.core.data.screens.sources.ConversationsSource
import com.hubara.operator.core.database.OperatorDatabase
import com.hubara.operator.core.model.Conversation
import com.hubara.operator.core.model.Route
import com.hubara.operator.core.model.SessionId
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * La bandeja como fuente del teléfono: viene de Room (al día por el SSE) y trae lo no leído de ESTE teléfono. La
 * primera bandeja cuenta como vista (si no, amanecería con contadores de hace semanas); lo que llega después, no.
 */
@RunWith(AndroidJUnit4::class)
class AppSourcesTest {
    private lateinit var db: OperatorDatabase
    private lateinit var source: ConversationsSource
    private val laura = SessionId.parse("wa_test_laura")!!

    private fun laura(inbound: Int) = Conversation(laura, "570000000000", "INTERESADO", Route.BOT, 0, null, inbound, null, customerName = "Laura Prueba")

    @Before fun setUp() {
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        db = Room.inMemoryDatabaseBuilder(context, OperatorDatabase::class.java).allowMainThreadQueries().build()
        val seen = SeenRepository(context, db.conversations())
        // El DataStore «seen» es uno por proceso: cada prueba arranca sin lo que dejó otra (la línea base es lo que se prueba).
        runBlocking { seen.clear() }
        source = ConversationsSource(ConversationRepository(FakeOperatorApi(), db.conversations()), seen)
    }

    @After fun tearDown() = db.close()

    private suspend fun awaitRow(predicate: (JsonObject) -> Boolean): JsonObject = withContext(Dispatchers.Default) {
        withTimeout(20_000) {
            source.observe(emptyMap()).first { list -> (list as JsonArray).any { predicate(it as JsonObject) } }
                .let { (it as JsonArray).first { row -> predicate(row as JsonObject) } as JsonObject }
        }
    }

    @Test fun la_primera_bandeja_cuenta_como_vista_y_lo_nuevo_como_no_leido() = runTest {
        db.conversations().replaceAll(listOf(laura(inbound = 3).toEntity()))
        val first = awaitRow { it["session_id"] == JsonPrimitive("wa_test_laura") }
        assertThat(first["title"]).isEqualTo(JsonPrimitive("Laura Prueba"))

        // La línea base ya quedó: Laura escribe otro mensaje y la fila lo cuenta.
        awaitRow { it["unseen"] == JsonPrimitive(0) }
        db.conversations().replaceAll(listOf(laura(inbound = 4).toEntity()))
        val after = awaitRow { it["unseen"] == JsonPrimitive(1) }
        assertThat(after["unread"]).isEqualTo(JsonPrimitive(true))
        assertThat(after["unseen_label"]).isEqualTo(JsonPrimitive("1 mensaje sin leer"))
    }
}
