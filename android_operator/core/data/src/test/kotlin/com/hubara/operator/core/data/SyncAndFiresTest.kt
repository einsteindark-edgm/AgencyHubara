package com.hubara.operator.core.data

import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.data.repo.ChatRepository
import com.hubara.operator.core.data.repo.ConversationRepository
import com.hubara.operator.core.data.repo.FireRepository
import com.hubara.operator.core.data.repo.OrderRepository
import com.hubara.operator.core.data.repo.SuggestionRepository
import com.hubara.operator.core.data.sync.SyncEngine
import com.hubara.operator.core.database.OperatorDatabase
import com.hubara.operator.core.model.FireId
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.network.api.OperatorService
import com.hubara.operator.core.network.dto.ActionRefDto
import com.hubara.operator.core.network.dto.FireDto
import com.hubara.operator.core.network.dto.FireSubjectDto
import com.hubara.operator.core.network.dto.FiresDto
import com.hubara.operator.core.network.dto.SuggestionsDto
import com.hubara.operator.core.network.sse.EventStream
import com.hubara.operator.core.network.sse.ServerEvent
import com.hubara.operator.core.network.toDomain
import com.hubara.operator.core.network.dto.ChatSessionDto
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class SyncAndFiresTest {
    private lateinit var db: OperatorDatabase
    private val api = FakeOperatorApi()
    private lateinit var sync: SyncEngine
    private lateinit var fires: FireRepository
    private lateinit var suggestions: SuggestionRepository
    private val laura = SessionId.parse("wa_test_laura")!!
    private val carlos = SessionId.parse("wa_test_carlos")!!

    @Before fun setUp() {
        db = Room.inMemoryDatabaseBuilder(ApplicationProvider.getApplicationContext(), OperatorDatabase::class.java)
            .allowMainThreadQueries().build()
        fires = FireRepository(api, db.fires()) { 5L }
        suggestions = SuggestionRepository(api, db.suggestions())
        sync = SyncEngine(
            events = EventStream(OkHttpClient(), "http://localhost/".toHttpUrl(), api),
            conversations = ConversationRepository(api, db.conversations()),
            chats = ChatRepository(api, db.conversations(), db.messages(), db.outbox(), db.drafts()),
            suggestions = suggestions,
            fires = fires,
            orders = OrderRepository(OperatorService(api)),
        )
    }

    @After fun tearDown() = db.close()

    @Test fun solo_se_refresca_el_chat_que_esta_abierto() = runTest {
        val handle = sync.watch(laura)
        sync.handle(ServerEvent.SessionUpdated(laura))
        sync.handle(ServerEvent.SessionUpdated(carlos))
        assertThat(api.calls).containsAtLeast("session:wa_test_laura", "suggestions:wa_test_laura")
        assertThat(api.calls).doesNotContain("session:wa_test_carlos")

        handle.close()
        api.calls.clear()
        sync.handle(ServerEvent.SessionUpdated(laura))
        assertThat(api.calls).doesNotContain("session:wa_test_laura")
    }

    @Test fun el_snapshot_reemplaza_la_bandeja() = runTest {
        val snapshot = listOf(ChatSessionDto("wa_test_laura", activeAgentRoute = "humano"), ChatSessionDto("wa_test_carlos"))
            .mapNotNull { it.toDomain() }
        sync.handle(ServerEvent.SessionsSnapshot(snapshot))
        assertThat(ConversationRepository(api, db.conversations()).observeInbox().first().map { it.sessionId.raw })
            .containsExactly("wa_test_laura", "wa_test_carlos")
    }

    @Test fun ocultar_saca_del_radar_pero_no_del_feed() = runTest {
        api.firesResponse = FiresDto(fires = listOf(
            FireDto("chat:wa_test_sofia", FireSubjectDto("chat", "wa_test_sofia"), "grave", "wants_human", title = "Sofía",
                primaryAction = ActionRefDto("open_chat"), updatedMs = 1),
            FireDto("order:order_01HX", FireSubjectDto("order", "wa_test_carlos", "order_01HX"), "hoy", "delayed", title = "#41",
                primaryAction = ActionRefDto("open_order"), updatedMs = 2),
        ))
        fires.refresh()
        assertThat(fires.observeRadar().first().map { it.title }).containsExactly("Sofía")
        fires.hide(FireId.parse("chat:wa_test_sofia")!!)
        assertThat(fires.observeRadar().first()).isEmpty()
        assertThat(fires.observeFeed().first().map { it.title }).containsExactly("Sofía", "#41").inOrder()
    }

    @Test fun una_respuesta_vieja_de_burbujas_no_pisa_la_nueva() = runTest {
        api.suggestionsResponse = { SuggestionsDto(sessionId = it, version = 5, decidedBy = "nueva") }
        suggestions.refresh(laura)
        api.suggestionsResponse = { SuggestionsDto(sessionId = it, version = 3, decidedBy = "vieja") }
        suggestions.refresh(laura)
        assertThat(suggestions.observe(laura).first()!!.decidedBy).isEqualTo("nueva")
    }
}
