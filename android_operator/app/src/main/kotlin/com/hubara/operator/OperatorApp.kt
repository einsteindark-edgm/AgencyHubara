package com.hubara.operator

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import com.hubara.operator.core.ui.LocalRadarIndicator
import com.hubara.operator.core.ui.RadarDefaults
import com.hubara.operator.core.ui.RadarIndicatorModel
import kotlinx.coroutines.delay
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Email
import androidx.compose.material.icons.filled.ShoppingCart
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.material3.adaptive.ExperimentalMaterial3AdaptiveApi
import androidx.compose.material3.adaptive.navigation3.rememberListDetailSceneStrategy
import androidx.compose.material3.adaptive.navigationsuite.NavigationSuiteScaffold
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import androidx.navigation3.runtime.NavKey
import androidx.navigation3.runtime.entryProvider
import androidx.navigation3.ui.NavDisplay
import com.hubara.operator.core.data.auth.AuthRepository
import com.hubara.operator.core.data.auth.AuthState
import com.hubara.operator.core.data.repo.ConversationRepository
import com.hubara.operator.core.data.repo.FireRepository
import com.hubara.operator.core.model.Conversation
import com.hubara.operator.core.model.Fire
import com.hubara.operator.core.model.FireId
import com.hubara.operator.core.navigation.BottomSheetSceneStrategy
import com.hubara.operator.core.navigation.EntryProviderInstaller
import com.hubara.operator.core.navigation.FiresKey
import com.hubara.operator.core.navigation.InboxKey
import com.hubara.operator.core.navigation.Navigator
import com.hubara.operator.core.navigation.OrdersKey
import com.hubara.operator.core.navigation.SyntheticStack
import com.hubara.operator.core.navigation.rememberNavigationState
import com.hubara.operator.core.ui.RadarOverlay
import com.hubara.operator.feature.auth.LoginScreen
import com.hubara.operator.feature.fires.destinationFor
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.collections.immutable.ImmutableList
import kotlinx.collections.immutable.persistentListOf
import kotlinx.collections.immutable.toImmutableList
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

@Composable
fun OperatorApp(
    installers: Set<EntryProviderInstaller>,
    auth: AuthRepository,
    pendingLink: StateFlow<SyntheticStack?>,
    onLinkConsumed: () -> Unit,
) {
    val state by auth.state.collectAsStateWithLifecycle()
    when (state) {
        AuthState.Loading -> Box(Modifier.fillMaxSize()) { CircularProgressIndicator(Modifier.align(Alignment.Center)) }
        AuthState.SignedOut, is AuthState.NeedsNewPassword -> LoginScreen()
        AuthState.SignedIn, AuthState.DevMode -> MainShell(installers, pendingLink, onLinkConsumed)
    }
}

@HiltViewModel
class ShellViewModel @Inject constructor(
    private val fires: FireRepository,
    conversations: ConversationRepository,
) : ViewModel() {
    val radar: StateFlow<ImmutableList<Fire>> = fires.observeRadar().map { it.toImmutableList() }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), persistentListOf())

    val inbox: StateFlow<List<Conversation>> = conversations.observeInbox()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    fun find(id: FireId): Fire? = radar.value.firstOrNull { it.id == id }

    fun hide(id: FireId) { viewModelScope.launch { fires.hide(id) } }
}

/** El radar se abre debajo del encabezado (la barra superior mide 64 dp): nunca tapa atrás, título ni acciones. */
private val RADAR_TOP_OFFSET = 64.dp

@OptIn(ExperimentalMaterial3AdaptiveApi::class, ExperimentalMaterial3Api::class)
@Composable
private fun MainShell(installers: Set<EntryProviderInstaller>, pendingLink: StateFlow<SyntheticStack?>, onLinkConsumed: () -> Unit) {
    val state = rememberNavigationState()
    val navigator = remember(state) { Navigator(state) }
    val shell: ShellViewModel = hiltViewModel()
    val radar by shell.radar.collectAsStateWithLifecycle()
    val inbox by shell.inbox.collectAsStateWithLifecycle()

    val link by pendingLink.collectAsStateWithLifecycle()
    LaunchedEffect(link) {
        link?.let {
            navigator.apply(it)
            onLinkConsumed()
        }
    }

    // Radar: desplegado a pedido (chip) o, unos segundos, cuando llega un incendio grave nuevo.
    var expanded by rememberSaveable { mutableStateOf(false) }
    var transient by remember { mutableStateOf<ImmutableList<Fire>>(persistentListOf()) }
    var transientTick by remember { mutableIntStateOf(0) }
    val seen = remember { mutableSetOf<String>() }
    LaunchedEffect(radar) {
        val fresh = radar.filter { seen.add(it.id.raw) }
        if (fresh.isNotEmpty() && !expanded) {
            transient = fresh.toImmutableList()
            transientTick++
        } else {
            transient = transient.filter { t -> radar.any { it.id == t.id } }.toImmutableList()
        }
    }
    LaunchedEffect(transientTick) {
        if (transient.isNotEmpty()) {
            delay(RadarDefaults.TRANSIENT_MS)
            transient = persistentListOf()
        }
    }

    val sheets = remember { BottomSheetSceneStrategy<NavKey>() }
    val listDetail = rememberListDetailSceneStrategy<NavKey>()
    val provider = entryProvider<NavKey> { installers.forEach { install -> install(navigator) } }

    NavigationSuiteScaffold(
        navigationSuiteItems = {
            item(selected = state.topLevelRoute == InboxKey, onClick = { navigator.navigate(InboxKey) },
                icon = { Icon(Icons.Filled.Email, contentDescription = null) }, label = { Text("Chats") })
            item(selected = state.topLevelRoute == FiresKey, onClick = { navigator.navigate(FiresKey) },
                icon = {
                    BadgedBox(badge = { if (radar.isNotEmpty()) Badge { Text(radar.size.toString()) } }) {
                        Icon(Icons.Filled.Warning, contentDescription = null)
                    }
                },
                label = { Text("Incendios") })
            item(selected = state.topLevelRoute == OrdersKey, onClick = { navigator.navigate(OrdersKey) },
                icon = { Icon(Icons.Filled.ShoppingCart, contentDescription = null) }, label = { Text("Órdenes") })
        },
    ) {
        BoxWithConstraints(Modifier.fillMaxSize()) {
            CompositionLocalProvider(LocalRadarIndicator provides RadarIndicatorModel(radar.size) { expanded = true }) {
                NavDisplay(
                    entries = state.toDecoratedEntries(provider),
                    onBack = { navigator.goBack() },
                    sceneStrategies = listOf(sheets, listDetail),
                )
            }
            // El radar es una capa encima de cualquier pantalla, no una clave de la pila. Un incendio nuevo
            // aparece compacto unos segundos debajo del encabezado y se pliega al chip de la barra superior.
            RadarOverlay(
                cards = if (expanded) radar else transient,
                expanded = expanded || transient.isNotEmpty(),
                compact = !expanded,
                maxHeight = maxHeight * 0.75f - RADAR_TOP_OFFSET,
                onOpen = { id ->
                    shell.find(id)?.let { fire ->
                        expanded = false
                        transient = persistentListOf()
                        navigator.navigate(FiresKey)
                        destinationFor(fire)?.let(navigator::navigate)
                    }
                },
                onHide = shell::hide,
                onCollapse = {
                    expanded = false
                    transient = persistentListOf()
                },
                modifier = Modifier
                    .align(Alignment.TopCenter)
                    .windowInsetsPadding(WindowInsets.statusBars)
                    .padding(top = RADAR_TOP_OFFSET),
            )
            navigator.returnTarget()?.let { session ->
                val who = inbox.firstOrNull { it.sessionId == session }?.phone?.takeLast(4)?.let { "…$it" } ?: "el chat"
                ExtendedFloatingActionButton(
                    onClick = { navigator.navigate(InboxKey) },
                    modifier = Modifier.align(Alignment.BottomCenter).padding(bottom = 16.dp),
                ) { Text("Volver con $who") }
            }
        }
    }
}
