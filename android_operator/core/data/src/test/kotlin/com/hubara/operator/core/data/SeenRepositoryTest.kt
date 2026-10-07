package com.hubara.operator.core.data

import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.data.mapping.toEntity
import com.hubara.operator.core.data.repo.SeenRepository
import com.hubara.operator.core.database.OperatorDatabase
import com.hubara.operator.core.model.Conversation
import com.hubara.operator.core.model.Route
import com.hubara.operator.core.model.SeenCounts
import com.hubara.operator.core.model.SessionId
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class SeenRepositoryTest {
    private lateinit var db: OperatorDatabase
    private lateinit var seen: SeenRepository
    private val laura = SessionId.parse("wa_test_laura")!!

    private fun laura(inbound: Int) = Conversation(laura, "", "", Route.HUMAN, 0, null, inbound, null)

    @Before fun setUp() {
        db = Room.inMemoryDatabaseBuilder(ApplicationProvider.getApplicationContext(), OperatorDatabase::class.java)
            .allowMainThreadQueries().build()
        seen = SeenRepository(ApplicationProvider.getApplicationContext(), db.conversations())
        // El DataStore «seen» es uno por proceso: sin limpiarlo, esta prueba hereda lo que dejó otra clase del mismo
        // JVM con el mismo chat (AppSourcesTest escribe la línea base de wa_test_laura). Falló así en CI el 6-oct-2026.
        runBlocking { seen.clear() }
    }

    @After fun tearDown() = db.close()

    // Con el chat abierto, lo que escribe el cliente queda leído: al volver a la bandeja no hay contador.
    @Test fun con_el_chat_a_la_vista_lo_que_llega_queda_leido() = runTest {
        db.conversations().replaceAll(listOf(laura(inbound = 3).toEntity()))
        // Room y DataStore emiten en sus propios hilos: se espera con reloj real y un tope corto.
        val watching = launch(Dispatchers.Default) { seen.keepSeen(laura) }
        awaitSeen(3)
        db.conversations().replaceAll(listOf(laura(inbound = 5).toEntity()))
        val counts = awaitSeen(5)
        assertThat(counts.unseen(laura(inbound = 5))).isEqualTo(0)
        watching.cancel()

        // Ya fuera del chat, el siguiente mensaje sí cuenta.
        assertThat(counts.unseen(laura(inbound = 6))).isEqualTo(1)
    }

    // 20 s y no 5: en los runners de GitHub, con todos los módulos probando en paralelo, Room + DataStore llegaron a
    // tardar más de 5 s (falló así el 1-oct-2026 sin cambios en este código). Aquí tarda milisegundos.
    private suspend fun awaitSeen(n: Int): SeenCounts = withContext(Dispatchers.Default) {
        withTimeoutOrNull(20_000) { seen.counts.first { it.seen[laura.raw] == n } }
    } ?: error("el chat abierto no quedó visto hasta $n (${seen.counts.first()})")
}
