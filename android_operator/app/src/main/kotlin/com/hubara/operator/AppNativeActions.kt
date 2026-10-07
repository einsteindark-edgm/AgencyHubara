package com.hubara.operator

import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import com.hubara.operator.core.data.config.ServerConfigStore
import com.hubara.operator.core.data.outbox.OutboxRepository
import com.hubara.operator.core.data.repo.ChatRepository
import com.hubara.operator.core.data.repo.FireRepository
import com.hubara.operator.core.data.repo.SuggestionRepository
import com.hubara.operator.core.data.repo.TemplateRepository
import com.hubara.operator.core.data.screens.NativeActions
import com.hubara.operator.core.data.screens.NativeOutcome
import com.hubara.operator.core.model.ActionRef
import com.hubara.operator.core.model.FireId
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.push.SignOut
import com.hubara.operator.core.ui.NativeComponent
import com.hubara.operator.core.ui.NativeProps
import com.hubara.operator.core.ui.NotificationPermissionBanner
import dagger.Binds
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import dagger.multibindings.IntoMap
import dagger.multibindings.StringKey
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * Las acciones nativas de las pantallas del servidor (`{"type": "native", "name": …}`). Viven en `:app` porque tocan
 * módulos que las pantallas no ven: el outbox (con deshacer), la sesión, los incendios. Los nombres y sus argumentos
 * están en `Catalog.nativeActions`; uno que esta versión no conoce responde un aviso, nunca se cae.
 */
@Singleton
class AppNativeActions @Inject constructor(
    private val signOut: SignOut,
    private val config: ServerConfigStore,
    private val fires: FireRepository,
    private val chats: ChatRepository,
    private val suggestions: SuggestionRepository,
    private val outbox: OutboxRepository,
    private val templates: TemplateRepository,
) : NativeActions {

    override suspend fun run(name: String, args: JsonObject): NativeOutcome {
        fun text(key: String) = (args[key] as? JsonPrimitive)?.content.orEmpty()
        val session = SessionId.parse(text("session"))
        return when (name) {
            "sign_out" -> {
                signOut()
                NativeOutcome.Done
            }
            "open_privacy" -> config.current.value.privacyUrl?.let(NativeOutcome::OpenUrl)
                ?: NativeOutcome.Failed("No hay política de privacidad configurada.")
            "hide_fire" -> FireId.parse(text("fire_id"))?.let {
                fires.hide(it)
                NativeOutcome.Done
            } ?: NativeOutcome.Failed("No se pudo ocultar ese incendio.")
            "take_over" -> session?.let { s ->
                if (chats.intervene(s).isSuccess) {
                    suggestions.refresh(s)
                    NativeOutcome.Done
                } else {
                    NativeOutcome.Failed("No se pudo tomar la conversación.")
                }
            } ?: NativeOutcome.Failed("No se pudo tomar la conversación.")
            "return_to_bot" -> session?.let { s ->
                if (chats.returnToBot(s).isSuccess) NativeOutcome.Done else NativeOutcome.Failed("No se pudo devolver la conversación al bot.")
            } ?: NativeOutcome.Failed("No se pudo devolver la conversación al bot.")
            // Por el outbox: Room primero, WorkManager después, con los 5 s de deshacer.
            "send_tool" -> session?.let { s ->
                outbox.sendTool(s, ActionRef(text("tool"), (args["args"] as? JsonObject) ?: JsonObject(emptyMap())), text("label"))
                NativeOutcome.Done
            } ?: NativeOutcome.Failed("No se pudo enviar.")
            "send_template" -> {
                val template = templates.list().getOrNull()?.firstOrNull { it.name == text("template") }
                val values = (args["values"] as? JsonObject).orEmpty().mapValues { (_, v) -> (v as? JsonPrimitive)?.content.orEmpty() }
                if (session == null || template == null) {
                    NativeOutcome.Failed("No se pudo enviar la plantilla.")
                } else {
                    outbox.sendTemplate(session, template, values)
                    NativeOutcome.Done
                }
            }
            else -> NativeOutcome.Failed("Esta versión de la app no sabe hacer «$name».")
        }
    }
}

/** El aviso para activar las notificaciones como pieza nativa (`{"type": "notifications_banner"}`): pide el permiso. */
object NotificationsBannerComponent : NativeComponent {
    @Composable
    override fun Content(props: NativeProps, modifier: Modifier) = NotificationPermissionBanner(modifier)
}

@Module
@InstallIn(SingletonComponent::class)
abstract class AppScreensModule {
    @Binds abstract fun nativeActions(impl: AppNativeActions): NativeActions

    companion object {
        @Provides @IntoMap @StringKey("notifications_banner")
        fun notificationsBanner(): NativeComponent = NotificationsBannerComponent
    }
}
