package com.hubara.operator.feature.screens

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.navigation3.runtime.NavKey
import com.hubara.operator.core.data.Clock
import com.hubara.operator.core.data.safeCall
import com.hubara.operator.core.data.screens.AppSource
import com.hubara.operator.core.data.screens.NativeActions
import com.hubara.operator.core.data.screens.NativeOutcome
import com.hubara.operator.core.data.screens.ScreenCallError
import com.hubara.operator.core.data.screens.ScreenData
import com.hubara.operator.core.data.screens.ScreenDataCache
import com.hubara.operator.core.data.screens.ScreenDocs
import com.hubara.operator.core.data.screens.ServerChanges
import com.hubara.operator.core.model.OrderId
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.navigation.ChatKey
import com.hubara.operator.core.navigation.LiveKey
import com.hubara.operator.core.navigation.OrderSheetKey
import com.hubara.operator.core.navigation.ScreenKey
import com.hubara.operator.core.navigation.ScreenSheetKey
import com.hubara.operator.core.sdui.Action
import com.hubara.operator.core.sdui.AppRequest
import com.hubara.operator.core.sdui.Catalog
import com.hubara.operator.core.sdui.Confirm
import com.hubara.operator.core.sdui.appRequest
import com.hubara.operator.core.sdui.truthy
import com.hubara.operator.core.sdui.DataSource
import com.hubara.operator.core.sdui.Env
import com.hubara.operator.core.sdui.ScreenDoc
import com.hubara.operator.core.sdui.Scope
import com.hubara.operator.core.sdui.resolve
import com.hubara.operator.core.sdui.resolveJson
import com.hubara.operator.core.sdui.screenScope
import dagger.assisted.Assisted
import dagger.assisted.AssistedFactory
import dagger.assisted.AssistedInject
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

enum class ScreenPhase {
    /** Buscando la definición (la primera vez, sin nada guardado). */
    LOADING,
    READY,

    /** No hay definición ni guardada ni en el servidor. */
    MISSING,

    /** La pantalla usa un catálogo más nuevo que el de esta app: hay que actualizarla. */
    OUTDATED,
}

/** El diálogo de una llamada con `confirm`. La llamada sale solo si el operador acepta. */
data class PendingConfirm(val title: String, val body: String?, val accept: String, val dismiss: String)

data class ScreenUi(
    val phase: ScreenPhase = ScreenPhase.LOADING,
    val doc: ScreenDoc? = null,
    val params: Map<String, String> = emptyMap(),
    /** Lo último que trajo cada fuente (o lo guardado de la vez anterior). */
    val data: Map<String, JsonElement> = emptyMap(),
    /** Fuentes cuyo último intento falló. */
    val failed: Set<String> = emptySet(),
    /** Fuentes que se están pidiendo ahora. */
    val loading: Set<String> = emptySet(),
    val state: Map<String, JsonElement> = emptyMap(),
    val form: Map<String, JsonElement> = emptyMap(),
    /** Hay una llamada en curso (los botones esperan). */
    val busy: Boolean = false,
    /** Qué componente la lanzó (su `origin`): ese botón muestra que está trabajando. */
    val working: String? = null,
    /** El operador pidió recargar (tirar para actualizar o una acción `refresh`): se le muestra hasta que termina. */
    val manualRefresh: Boolean = false,
    val confirm: PendingConfirm? = null,
) {
    private val required: List<DataSource> get() = doc?.data?.values?.filterNot { it.optional }.orEmpty()

    /** Primera carga: falta un dato obligatorio que todavía no llegó. */
    val waiting: Boolean get() = phase == ScreenPhase.READY && required.any { it.id !in data && it.id !in failed }

    /** Un dato obligatorio falló y no hay nada que mostrar. */
    val broken: Boolean get() = phase == ScreenPhase.READY && required.any { it.id !in data && it.id in failed }

    /** Recargando con algo ya en pantalla. */
    val refreshing: Boolean get() = loading.isNotEmpty() && !waiting

    /** El indicador de «tirar para actualizar»: solo si lo pidió el operador; las recargas por eventos son calladas. */
    val pullIndicator: Boolean get() = manualRefresh && refreshing

    /** El estado de cada fuente para las plantillas: `{{status.chats.failed}}` (sin red, mostrando lo guardado). */
    private val status: JsonObject
        get() = JsonObject(doc?.data?.keys.orEmpty().associateWith { id ->
            JsonObject(mapOf("loading" to JsonPrimitive(id in loading), "failed" to JsonPrimitive(id in failed)))
        })

    /** Lo que leen las plantillas: datos, `params`, `state`, `form`, `now`, `status` y los valores calculados (`computed`). */
    fun scope(nowMs: Long): Scope =
        screenScope(params, state, form, data, nowMs, doc?.computed.orEmpty(), Env(nowMs = { nowMs }), status = status)
}

/** Lo que la pantalla hace una sola vez: navegar, abrir un enlace, copiar, avisar. */
sealed interface ScreenEffect {
    data class Open(val key: NavKey) : ScreenEffect
    data object Back : ScreenEffect
    data class OpenUrl(val url: String) : ScreenEffect
    data class Copy(val text: String) : ScreenEffect
    data class Message(val text: String) : ScreenEffect
}

/** Espera de un buscador (`text_field` atado a `state`) antes de volver a pedir: no un pedido por letra. */
private const val TYPING_DEBOUNCE_MS = 400L

/**
 * El ViewModel ÚNICO de todas las pantallas del servidor (MVI: la pantalla emite eventos y pinta [state]).
 *
 * 1. Consigue la definición: la guardada (o la del APK) al instante y después la del servidor.
 * 2. Pide cada fuente de `data` con los valores del momento; mientras tanto muestra lo último guardado.
 * 3. Vuelve a pedir una fuente cuando cambia lo que usa su ruta (`state`), cuando el servidor avisa por su dominio
 *    (`refreshOn`), cada `every` segundos mientras se ve, o al tirar para actualizar.
 * 4. Ejecuta las acciones del catálogo. Las que salen de la pantalla (navegar, enlaces) van por [effects].
 */
@HiltViewModel(assistedFactory = ScreenViewModel.Factory::class)
class ScreenViewModel @AssistedInject constructor(
    @Assisted private val screenId: String,
    @Assisted private val params: Map<String, String>,
    private val docs: ScreenDocs,
    private val data: ScreenData,
    private val cache: ScreenDataCache,
    private val changes: ServerChanges,
    private val clock: Clock,
    private val appSources: Map<String, @JvmSuppressWildcards AppSource>,
    private val native: NativeActions,
) : ViewModel() {

    private val _state = MutableStateFlow(ScreenUi(params = params))
    val state: StateFlow<ScreenUi> = _state.asStateFlow()

    private val _effects = Channel<ScreenEffect>(Channel.BUFFERED)
    val effects: Flow<ScreenEffect> = _effects.receiveAsFlow()

    private val env = Env(nowMs = { clock.nowMs() })

    /** Un pedido en curso por fuente: el nuevo cancela al viejo (una respuesta vieja nunca pisa a la nueva). */
    private val fetches = mutableMapOf<String, Job>()

    /** La ruta con la que se pidió cada fuente la última vez: si no cambia, no se vuelve a pedir. */
    private val requested = mutableMapOf<String, String>()

    /** Lo que se hace si el operador acepta la confirmación abierta (una llamada con `confirm` o el `then` de `confirm`). */
    private var pendingAction: Triple<Action, Scope, String?>? = null

    /** La fuente del teléfono que se está mirando por fuente de la pantalla (cambia si cambian sus parámetros). */
    private val watching = mutableMapOf<String, Pair<AppRequest, Job>>()

    /**
     * Fuentes del teléfono que ya cargaron bien una vez. Hasta entonces un valor vacío de Room (recién instalada, o
     * tras cerrar sesión) no se muestra: sería «No hay chats» mientras llegan. Se guarda aparte por si la carga trae
     * justo eso.
     */
    private val settled = mutableSetOf<String>()
    private val heldEmpty = mutableMapOf<String, JsonElement>()
    private var typingJob: Job? = null
    private var pollers: List<Job> = emptyList()
    private var visible = false

    init {
        viewModelScope.launch { load() }
        viewModelScope.launch {
            changes.changes.collect { domain ->
                _state.value.doc?.data?.values?.filter { domain in it.refreshOn }?.forEach { fetch(it) }
            }
        }
    }

    @AssistedFactory
    interface Factory {
        fun create(screenId: String, params: Map<String, String>): ScreenViewModel
    }

    fun retry() {
        if (_state.value.phase == ScreenPhase.READY) refresh() else viewModelScope.launch { load() }
    }

    /** Tirar para actualizar: todas las fuentes. */
    fun refresh() {
        val sources = _state.value.doc?.data?.values.orEmpty()
        if (sources.isNotEmpty()) _state.update { it.copy(manualRefresh = true) }
        sources.forEach { fetch(it) }
    }

    /** [origin]: quién la lanzó (un botón), para que ese muestre que está trabajando mientras espera al backend. */
    fun onAction(action: Action, scope: Scope, origin: String? = null) {
        viewModelScope.launch {
            safeCall { execute(action, scope, origin) }.onFailure {
                _state.update { it.copy(busy = false, working = null) }
                message("No se pudo completar.")
            }
        }
    }

    /**
     * Lo que escribe o elige el operador. `form.x` solo se guarda (se manda en una llamada); `state.x` además vuelve a
     * pedir lo que lo usa — al instante en chips e interruptores, y [typing] en un campo de texto (espera a que pare).
     */
    fun onBind(bind: String, value: JsonElement, typing: Boolean = false) {
        val (where, key) = bind.split('.', limit = 2).takeIf { it.size == 2 }?.let { it[0] to it[1] } ?: return
        when (where) {
            "form" -> _state.update { it.copy(form = it.form + (key to value)) }
            "state" -> {
                _state.update { it.copy(state = it.state + (key to value)) }
                typingJob?.cancel()
                if (typing) {
                    typingJob = viewModelScope.launch {
                        delay(TYPING_DEBOUNCE_MS)
                        refetchChanged()
                    }
                } else {
                    refetchChanged()
                }
            }
        }
    }

    fun onConfirm(accepted: Boolean) {
        val pending = pendingAction
        pendingAction = null
        _state.update { it.copy(confirm = null) }
        if (accepted && pending != null) {
            viewModelScope.launch {
                safeCall {
                    val (action, scope, origin) = pending
                    if (action is Action.Call) call(action, scope, origin) else execute(action, scope, origin)
                }.onFailure {
                    _state.update { it.copy(busy = false, working = null) }
                    message("No se pudo completar.")
                }
            }
        }
    }

    /** Las fuentes con `every` se renuevan solo mientras la pantalla está a la vista. */
    fun setVisible(visible: Boolean) {
        if (this.visible == visible) return
        this.visible = visible
        restartPollers()
    }

    // ── Definición ───────────────────────────────────────────────────────────────────────────────

    private suspend fun load() {
        val cached = docs.cached(screenId)
        cached?.let(::apply)
        val fresh = docs.fetch(screenId)
        when {
            fresh != null -> apply(fresh)
            cached == null -> _state.update { it.copy(phase = ScreenPhase.MISSING) }
        }
    }

    private fun apply(doc: ScreenDoc) {
        if (doc.requires > Catalog.VERSION) {
            _state.update { it.copy(phase = ScreenPhase.OUTDATED, doc = null) }
            return
        }
        val previous = _state.value.doc
        // Lo que el operador ya eligió se conserva si la clave sigue existiendo en la versión nueva.
        _state.update { ui ->
            ui.copy(
                phase = ScreenPhase.READY,
                doc = doc,
                state = doc.state + ui.state.filterKeys { it in doc.state },
                data = ui.data.filterKeys { it in doc.data },
            )
        }
        doc.data.values.forEach { source ->
            val same = previous?.data?.get(source.id) == source
            if (!same || source.id !in requested) fetch(source) else refetchIfChanged(source)
        }
        restartPollers()
    }

    // ── Datos ────────────────────────────────────────────────────────────────────────────────────

    private fun refetchChanged() {
        _state.value.doc?.data?.values?.forEach(::refetchIfChanged)
    }

    private fun refetchIfChanged(source: DataSource) {
        if (source.app != null) {
            val request = runCatching { source.appRequest(_state.value.scope(clock.nowMs()), env) }.getOrNull()
            if (request != watching[source.id]?.first) fetch(source)
            return
        }
        val path = source.resolve(_state.value.scope(clock.nowMs()), env)?.path
        if (path != requested[source.id]) fetch(source)
    }

    /**
     * Una fuente del teléfono: se mira su flujo (Room al día por el SSE, sin pedir nada) y se le pide al backend que la
     * renueve. Si la renovación falla, lo de Room sigue en pantalla y `{{status.<id>.failed}}` lo dice.
     */
    private fun fetchApp(source: DataSource) {
        val request = runCatching { source.appRequest(_state.value.scope(clock.nowMs()), env) }.getOrNull()
        val app = request?.let { appSources[it.name] }
        if (request == null || app == null) {
            _state.update { it.copy(failed = it.failed + source.id) }
            return
        }
        if (watching[source.id]?.first != request) {
            watching.remove(source.id)?.second?.cancel()
            settled -= source.id
            heldEmpty -= source.id
            watching[source.id] = request to viewModelScope.launch {
                safeCall {
                    app.observe(request.params).collect { value ->
                        if (source.id in settled || !value.isEmptyValue()) {
                            heldEmpty -= source.id
                            _state.update { it.copy(data = it.data + (source.id to value)) }
                        } else {
                            heldEmpty[source.id] = value
                        }
                    }
                }
            }
        }
        fetches[source.id]?.cancel()
        fetches[source.id] = viewModelScope.launch {
            starting(source.id)
            val ok = safeCall { app.refresh(request.params).getOrThrow() }.isSuccess
            if (ok) settled += source.id
            val held = if (ok) heldEmpty.remove(source.id) else null
            finished(source.id, ok) { ui -> if (held != null && source.id !in ui.data) ui.data + (source.id to held) else ui.data }
        }
    }

    /** Empieza a pedir una fuente. Sin nada en pantalla, un error anterior deja lugar al indicador (reintentar). */
    private fun starting(id: String) = _state.update { ui ->
        ui.copy(loading = ui.loading + id, failed = if (id in ui.data) ui.failed else ui.failed - id)
    }

    /** Terminó de pedir una fuente; con la última, se apaga el indicador de la recarga que pidió el operador. */
    private fun finished(id: String, ok: Boolean, data: (ScreenUi) -> Map<String, JsonElement> = { it.data }) = _state.update { ui ->
        val loading = ui.loading - id
        ui.copy(
            data = data(ui),
            failed = if (ok) ui.failed - id else ui.failed + id,
            loading = loading,
            manualRefresh = ui.manualRefresh && loading.isNotEmpty(),
        )
    }

    private fun fetch(source: DataSource) {
        if (source.app != null) return fetchApp(source)
        // Nada de lo que traiga una pantalla del servidor puede cerrar la app: lo que falle queda como fuente fallida.
        val call = runCatching { source.resolve(_state.value.scope(clock.nowMs()), env) }.getOrNull()
        if (call == null) {
            _state.update { it.copy(failed = it.failed + source.id) }
            return
        }
        requested[source.id] = call.path
        val key = "$screenId|${source.id}|${call.path}"
        fetches[source.id]?.cancel()
        fetches[source.id] = viewModelScope.launch {
            // Mientras llega, lo que se guardó la vez anterior con esta misma ruta.
            val saved = cache.read(key)
            if (saved != null) _state.update { ui -> if (source.id !in ui.data) ui.copy(data = ui.data + (source.id to saved)) else ui }
            starting(source.id)
            val result = safeCall { data.execute(call).getOrThrow() }
            result.onSuccess { value ->
                cache.write(key, value)
                finished(source.id, ok = true) { it.data + (source.id to value) }
            }.onFailure {
                finished(source.id, ok = false) { ui -> if (saved != null) ui.data + (source.id to saved) else ui.data }
            }
        }
    }

    private fun restartPollers() {
        pollers.forEach { it.cancel() }
        pollers = if (!visible) emptyList() else _state.value.doc?.data?.values?.mapNotNull { source ->
            val seconds = source.every?.coerceAtLeast(15) ?: return@mapNotNull null
            viewModelScope.launch {
                while (isActive) {
                    delay(seconds * 1000L)
                    fetch(source)
                }
            }
        }.orEmpty()
    }

    // ── Acciones ─────────────────────────────────────────────────────────────────────────────────

    private suspend fun execute(action: Action, scope: Scope, origin: String? = null) {
        when (action) {
            is Action.Navigate -> {
                val values = action.params.mapValues { it.value.text(scope, env) }
                _effects.send(ScreenEffect.Open(if (action.sheet) ScreenSheetKey(action.screen, values) else ScreenKey(action.screen, values)))
            }
            is Action.OpenChat -> SessionId.parse(action.session.text(scope, env))
                ?.let { _effects.send(ScreenEffect.Open(if (action.live) LiveKey(it) else ChatKey(it))) } ?: message("No se pudo abrir ese chat.")
            is Action.OpenOrder -> OrderId.parse(action.order.text(scope, env))
                ?.let { _effects.send(ScreenEffect.Open(OrderSheetKey(it))) } ?: message("No se pudo abrir ese pedido.")
            is Action.OpenUrl -> {
                val url = action.url.text(scope, env)
                if (url.startsWith("https://") || url.startsWith("tel:")) _effects.send(ScreenEffect.OpenUrl(url))
                else message("Ese enlace no se puede abrir desde la app.")
            }
            Action.Back -> _effects.send(ScreenEffect.Back)
            is Action.Refresh -> {
                val sources = _state.value.doc?.data?.values.orEmpty().filter { action.sources.isEmpty() || it.id in action.sources }
                if (sources.isNotEmpty()) _state.update { it.copy(manualRefresh = true) }
                sources.forEach { fetch(it) }
            }
            is Action.SetState -> {
                val values = (resolveJson(JsonObject(action.values), scope, env) as JsonObject)
                _state.update { it.copy(state = it.state + values) }
                refetchChanged()
            }
            is Action.Call -> {
                val confirm = action.confirm
                if (confirm == null) call(action, scope, origin) else ask(confirm, action, scope, origin)
            }
            is Action.AskFirst -> action.then?.let { ask(action.confirm, it, scope, origin) }
            is Action.If -> (if (action.condition.evaluate(scope, env).truthy) action.then else action.otherwise)?.let { execute(it, scope, origin) }
            is Action.Native -> {
                val args = action.args?.let { resolveJson(it, scope, env) } as? JsonObject ?: JsonObject(emptyMap())
                // Tomar, devolver, cerrar sesión… esperan al backend: ocupado mientras tanto y sin doble toque.
                if (!startWorking(origin)) return
                val outcome = try {
                    native.run(action.name, args)
                } finally {
                    _state.update { it.copy(busy = false, working = null) }
                }
                when (outcome) {
                    NativeOutcome.Done -> Unit
                    is NativeOutcome.Message -> message(outcome.text)
                    is NativeOutcome.OpenUrl -> _effects.send(ScreenEffect.OpenUrl(outcome.url))
                    is NativeOutcome.Failed -> message(outcome.text)
                }
            }
            is Action.Message -> message(action.text.text(scope, env))
            is Action.Copy -> _effects.send(ScreenEffect.Copy(action.text.text(scope, env)))
            is Action.Sequence -> action.actions.forEach { execute(it, scope, origin) }
            is Action.Unknown -> Unit // Una acción de un catálogo más nuevo: no hace nada (la CI ya la habría rechazado).
        }
    }

    /** Abre el diálogo de confirmación; si el operador acepta, [onConfirm] hace [then]. */
    private fun ask(confirm: Confirm, then: Action, scope: Scope, origin: String?) {
        pendingAction = Triple(then, scope, origin)
        _state.update {
            it.copy(
                confirm = PendingConfirm(
                    title = confirm.title.text(scope, env),
                    body = confirm.body?.text(scope, env)?.ifBlank { null },
                    accept = confirm.accept,
                    dismiss = confirm.dismiss,
                ),
            )
        }
    }

    /** Ocupa la pantalla para una llamada; false si ya hay otra en curso (un doble toque no sale dos veces). */
    private fun startWorking(origin: String?): Boolean {
        var started = false
        _state.update { ui ->
            if (ui.busy) ui else ui.copy(busy = true, working = origin).also { started = true }
        }
        return started
    }

    private suspend fun call(action: Action.Call, scope: Scope, origin: String?) {
        val http = action.resolve(scope, env)
        if (http == null) {
            message("Esa acción no se puede hacer desde la app.")
            return
        }
        if (!startWorking(origin)) return
        val result = safeCall { data.execute(http).getOrThrow() }
        _state.update { it.copy(busy = false, working = null) }
        result.onSuccess {
            action.success?.text(scope, env)?.takeIf { it.isNotBlank() }?.let { message(it) }
            action.then.forEach { execute(it, scope) }
        }.onFailure { e ->
            // Solo el motivo del backend es para el operador; un error interno no se muestra tal cual.
            message((e as? ScreenCallError)?.message?.takeIf { it.isNotBlank() } ?: "No se pudo completar.")
        }
    }

    private suspend fun message(text: String) = _effects.send(ScreenEffect.Message(text))
}

/** Lo que una fuente del teléfono da antes de tener nada: lista u objeto vacíos, o nada. */
private fun JsonElement.isEmptyValue(): Boolean = when (this) {
    is JsonArray -> isEmpty()
    is JsonObject -> isEmpty()
    is JsonNull -> true
    else -> false
}
