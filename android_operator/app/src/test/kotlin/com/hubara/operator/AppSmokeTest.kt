package com.hubara.operator

import android.content.Context
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.printToString
import androidx.compose.ui.test.performClick
import androidx.hilt.work.HiltWorkerFactory
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.work.Configuration
import androidx.work.WorkManager
import androidx.work.testing.SynchronousExecutor
import androidx.work.testing.WorkManagerTestInitHelper
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.data.auth.InMemoryTokenStore
import com.hubara.operator.core.data.auth.TokenStore
import com.hubara.operator.core.data.di.DataBindings
import com.hubara.operator.core.data.outbox.OutboxScheduler
import com.hubara.operator.core.data.outbox.WorkManagerOutboxScheduler
import com.hubara.operator.core.network.di.AccessTokenProvider
import com.hubara.operator.core.network.di.ApiConfig
import com.hubara.operator.core.data.auth.AuthRepository
import dagger.Binds
import dagger.Module
import dagger.Provides
import dagger.hilt.android.testing.HiltAndroidRule
import dagger.hilt.android.testing.HiltAndroidTest
import dagger.hilt.android.testing.HiltTestApplication
import dagger.hilt.components.SingletonComponent
import dagger.hilt.testing.TestInstallIn
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton
import mockwebserver3.Dispatcher
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import mockwebserver3.RecordedRequest
import org.junit.AfterClass
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

/** Backend falso con las respuestas del contrato (mismas formas que el API real). */
object FakeBackend {
    val server = MockWebServer()
    @Volatile var route = "ventas"
    val toolBodies = java.util.concurrent.CopyOnWriteArrayList<String>()
    val paths = java.util.concurrent.CopyOnWriteArrayList<String>()

    private const val SESSION = "wa_test_laura"

    fun start() {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.url.encodedPath
                paths += request.method + " " + path
                return when {
                    path == "/api/dashboard/sessions" -> json("""{"sessions":[{"session_id":"$SESSION","phone_number":"570000000000",
                        "tag":"INTERESADO","active_agent_route":"$route","last_updated_timestamp":1727640000,"unanswered_count":1}]}""")
                    path == "/api/dashboard/sessions/$SESSION" -> json("""{"session_id":"$SESSION","phone_number":"570000000000",
                        "tag":"INTERESADO","active_agent_route":"$route","service_window_expires_at_ms":4102444800000,
                        "messages":[{"ui_type":"user_message","role":"user","content":"¿y qué aromas tienen? quiero 2","timestamp":"2026-09-29T15:00:00+00:00"}]}""")
                    path == "/api/dashboard/sessions/$SESSION/intervene" -> {
                        route = "humano"
                        json("""{"ok":true,"active_route":"humano","tag":"HUMANO","motivo":"","terminated_workflows":[]}""")
                    }
                    path == "/api/chats/mobile/suggestions/$SESSION" -> json("""{"session_id":"$SESSION","version":2,"decided_by":"rules",
                        "stage":"etapa_variantes","window_open":true,"in_control":"${if (route == "humano") "human" else "bot"}","suggestions":[
                        {"id":"present_variant_picker","label":"Enviar aromas","prominence":"primary","editable":true,
                         "action":{"name":"present_variant_picker","args":{"product":"duo-zodiacal","attribute":"aroma"}}}]}""")
                    path.startsWith("/api/chats/session-actions/$SESSION/tools/") -> {
                        toolBodies += path + " " + request.body!!.utf8()
                        json("""{"sent":true,"tool":"present_variant_picker","client_action_id":"x","deduplicated":false}""")
                    }
                    path == "/api/chats/mobile/fires" -> json("""{"decided_by":"rules","fires":[{"fire_id":"chat:wa_test_sofia",
                        "subject":{"kind":"chat","session_id":"wa_test_sofia"},"severity":"grave","kind":"wants_human",
                        "getting_worse":true,"title":"Sofía pide un humano","subtitle":"12 min sin respuesta",
                        "primary_action":{"name":"open_chat","args":{}},"updated_ms":1}]}""")
                    path == "/api/orders/orders" -> json("""{"orders":[]}""")
                    path == "/api/dashboard/sse-ticket" -> json("""{"ticket":"t"}""")
                    path == "/api/dashboard/events" -> MockResponse.Builder().code(503).build()
                    else -> MockResponse.Builder().code(404).build()
                }
            }
        }
        server.start()
    }

    private fun json(body: String) = MockResponse.Builder().code(200).addHeader("Content-Type", "application/json").body(body).build()
}

@Module
@TestInstallIn(components = [SingletonComponent::class], replaces = [AppModule::class])
object TestAppModule {
    // Cognito vacío = modo dev: la app entra sin login, como contra el backend local.
    @Provides @Singleton
    fun apiConfig(): ApiConfig = ApiConfig(FakeBackend.server.url("/"), "", "us-east-1")
}

@Module
@TestInstallIn(components = [SingletonComponent::class], replaces = [DataBindings::class])
abstract class TestDataBindings {
    @Binds abstract fun accessTokens(impl: AuthRepository): AccessTokenProvider
    @Binds abstract fun outboxScheduler(impl: WorkManagerOutboxScheduler): OutboxScheduler

    companion object {
        // El Android Keystore no existe en Robolectric: la sesión va en memoria.
        @Provides @Singleton fun tokenStore(): TokenStore = InMemoryTokenStore()
    }
}

@HiltAndroidTest
@Config(application = HiltTestApplication::class)
@RunWith(AndroidJUnit4::class)
class AppSmokeTest {
    @get:Rule(order = 0) val hilt = HiltAndroidRule(this)
    @get:Rule(order = 1) val compose = createAndroidComposeRule<MainActivity>()

    @Inject lateinit var workerFactory: HiltWorkerFactory

    companion object {
        init { FakeBackend.start() }

        @AfterClass @JvmStatic fun stop() = FakeBackend.server.close()
    }

    @Before fun setUp() {
        hilt.inject()
        val context = ApplicationProvider.getApplicationContext<Context>()
        WorkManagerTestInitHelper.initializeTestWorkManager(
            context, Configuration.Builder().setWorkerFactory(workerFactory).setExecutor(SynchronousExecutor()).build(),
        )
    }

    private fun waitFor(text: String) = try {
        compose.waitUntil(10_000) { compose.onAllNodes(hasText(text, substring = true)).fetchSemanticsNodes().isNotEmpty() }
    } catch (e: Throwable) {
        println("PEDIDOS: " + FakeBackend.paths.joinToString(" | "))
        println("ARBOL: " + compose.onRoot(useUnmergedTree = true).printToString())
        throw e
    }

    @Test fun bandeja_chat_tomar_la_conversacion_y_enviar_una_burbuja() {
        // La bandeja y el radar cargan del backend. El incendio nuevo se ve unos segundos y se pliega al chip.
        waitFor("+57 000 000 0000")
        waitFor("Sofía pide un humano")
        compose.waitUntil(10_000) { compose.onAllNodes(hasText("Sofía pide un humano")).fetchSemanticsNodes().isEmpty() }

        compose.onNodeWithText("+57 000 000 0000").performClick()
        waitFor("¿y qué aromas tienen?")
        waitFor("Tomar la conversación")

        compose.onNodeWithText("Tomar la conversación").performClick()
        waitFor("Tú atiendes")
        waitFor("Enviar aromas")

        compose.onNodeWithText("Enviar aromas").performClick()
        waitFor("Enviando «Enviar aromas»")

        // Pasan los 5 s de deshacer: el worker manda la tool con su client_action_id.
        val context = ApplicationProvider.getApplicationContext<Context>()
        val driver = WorkManagerTestInitHelper.getTestDriver(context)!!
        compose.waitUntil(10_000) { WorkManager.getInstance(context).getWorkInfosByTag("outbox").get().isNotEmpty() }
        WorkManager.getInstance(context).getWorkInfosByTag("outbox").get(5, TimeUnit.SECONDS).forEach {
            driver.setAllConstraintsMet(it.id)
            driver.setInitialDelayMet(it.id)
        }
        compose.waitUntil(10_000) { FakeBackend.toolBodies.isNotEmpty() }
        val sent = FakeBackend.toolBodies.single()
        assertThat(sent).startsWith("/api/chats/session-actions/wa_test_laura/tools/present_variant_picker ")
        assertThat(sent).contains("\"client_action_id\":\"")
        assertThat(sent).contains("\"args\":{\"product\":\"duo-zodiacal\",\"attribute\":\"aroma\"}")
    }
}
