@file:OptIn(androidx.compose.material3.ExperimentalMaterial3ExpressiveApi::class)

package com.hubara.operator

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.material3.Badge
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.adaptive.ExperimentalMaterial3AdaptiveApi
import androidx.compose.material3.adaptive.currentWindowAdaptiveInfo
import androidx.compose.material3.adaptive.navigation3.rememberListDetailSceneStrategy
import androidx.compose.material3.adaptive.navigationsuite.NavigationSuiteScaffold
import androidx.compose.material3.adaptive.navigationsuite.NavigationSuiteScaffoldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
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
import com.hubara.operator.core.designsystem.OperatorIcons
import com.hubara.operator.core.designsystem.OperatorTheme
import com.hubara.operator.core.model.Conversation
import com.hubara.operator.core.model.Fire
import com.hubara.operator.core.model.FireId
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.navigation.BottomSheetSceneStrategy
import com.hubara.operator.core.navigation.EntryProviderInstaller
import com.hubara.operator.core.navigation.FiresKey
import com.hubara.operator.core.navigation.InboxKey
import com.hubara.operator.core.navigation.Navigator
import com.hubara.operator.core.navigation.OrdersKey
import com.hubara.operator.core.navigation.SyntheticStack
import com.hubara.operator.core.navigation.rememberNavigationState
import com.hubara.operator.core.ui.LocalRadarFloor
import com.hubara.operator.core.ui.LocalRadarIndicator
import com.hubara.operator.core.ui.RadarFloorState
import com.hubara.operator.core.ui.RadarIndicatorModel
import com.hubara.operator.core.ui.RadarLayer
import com.hubara.operator.feature.auth.LoginScreen
import com.hubara.operator.core.navigation.fireDestination
import com.hubara.operator.core.navigation.ScreenRoutes
import com.hubara.operator.core.data.screens.AppSource
import com.hubara.operator.core.data.screens.ScreenStore
import com.hubara.operator.core.sdui.screenScope
import com.hubara.operator.core.ui.LocalNativeComponents
import com.hubara.operator.core.ui.NativeComponent
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.combine
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
import com.hubara.operator.core.ui.SessionActions
import com.hubara.operator.core.ui.LocalSessionActions
import com.hubara.operator.core.push.SignOut
import androidx.compose.material3.LoadingIndicator
import androidx.compose.ui.platform.LocalUriHandler
import com.hubara.operator.core.ui.VersionGate
import com.hubara.operator.core.network.config.ServerConfig
import com.hubara.operator.core.navigation.ScreenKey
import com.hubara.operator.core.sdui.TabSpec

@Composable
fun OperatorApp(
    installers: Set<EntryProviderInstaller>,
    auth: AuthRepository,
    server: StateFlow<ServerConfig>,
    pendingLink: StateFlow<SyntheticStack?>,
    onLinkConsumed: () -> Unit,
    tabs: List<TabSpec>,
    natives: Map<String, NativeComponent> = emptyMap(),
) {
    val state by auth.state.collectAsStateWithLifecycle()
    val config by server.collectAsStateWithLifecycle()
    val uriHandler = LocalUriHandler.current
    // La política de privacidad y la versión mínima las manda el servidor (config.json).
    val session = remember(uriHandler, config.privacyUrl) {
        SessionActions(openPrivacy = config.privacyUrl?.let { url -> { uriHandler.openUri(url) } })
    }
    // Superficie de fondo en todas las ramas: sin ella, login y carga quedaban transparentes en modo oscuro.
    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) {
        VersionGate(
            minVersionCode = config.minVersionCode,
            appVersionCode = BuildConfig.VERSION_CODE,
            onUpdate = { uriHandler.openUri("https://play.google.com/store/apps/details?id=${BuildConfig.APPLICATION_ID}") },
        ) {
            CompositionLocalProvider(LocalSessionActions provides session, LocalNativeComponents provides natives) {
                when (state) {
                    AuthState.Loading -> Box(Modifier.fillMaxSize()) { LoadingIndicator(Modifier.align(Alignment.Center)) }
                    AuthState.SignedOut, is AuthState.NeedsNewPassword -> LoginScreen()
                    AuthState.SignedIn, AuthState.DevMode -> MainShell(installers, pendingLink, onLinkConsumed, tabs)
                }
            }
        }
    }
}

@HiltViewModel
class ShellViewModel @Inject constructor(
    private val fires: FireRepository,
    conversations: ConversationRepository,
    screens: ScreenStore,
    appSources: Map<String, @JvmSuppressWildcards AppSource>,
) : ViewModel() {
    val radar: StateFlow<ImmutableList<Fire>> = fires.observeRadar().map { it.toImmutableList() }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), persistentListOf())

    val inbox: StateFlow<List<Conversation>> = conversations.observeInbox()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    /**
     * El número de cada pestaña (`"badge": "{{radar | count}}"` en `app.json`), por pantalla. Se calcula con las fuentes
     * del teléfono que usa cada plantilla; vacío o «0» no se muestra.
     */
    val badges: StateFlow<Map<String, String>> = run {
        val withBadge = screens.manifest.tabs.mapNotNull { tab -> tab.badge?.let { tab.screen to it } }
        val names = withBadge.flatMap { it.second.roots() }.distinct().filter { it in appSources }
        if (names.isEmpty()) {
            MutableStateFlow(emptyMap())
        } else {
            combine(names.map { name -> appSources.getValue(name).observe(emptyMap()).map { name to it } }) { pairs ->
                val scope = screenScope(emptyMap(), emptyMap(), emptyMap(), pairs.toMap(), System.currentTimeMillis())
                withBadge.associate { (screen, badge) -> screen to badge.text(scope) }
            }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyMap())
        }
    }

    fun find(id: FireId): Fire? = radar.value.firstOrNull { it.id == id }

    fun hide(id: FireId) { viewModelScope.launch { fires.hide(id) } }
}

/** El radar se abre debajo del encabezado (la barra superior mide 64 dp): nunca tapa atrás, título ni acciones. */
private val RADAR_TOP_OFFSET = 64.dp

/** Un destino de la barra, de `app.json`: su clave de navegación, su pantalla, su texto y sus íconos del catálogo. */
private data class Tab(val key: NavKey, val screen: String, val label: String, val icon: String, val iconSelected: String?)

@OptIn(ExperimentalMaterial3AdaptiveApi::class, ExperimentalMaterial3Api::class)
@Composable
private fun MainShell(
    installers: Set<EntryProviderInstaller>,
    pendingLink: StateFlow<SyntheticStack?>,
    onLinkConsumed: () -> Unit,
    specs: List<TabSpec>,
) {
    // TODAS las pestañas salen de `app.json` (la primera es la de inicio), cada una con su pila. Chats, Incendios y
    // Órdenes usan las claves de siempre (enlaces, radar, «Volver con …»). El manifiesto se lee al arrancar y no cambia en
    // caliente: la lista de destinos es estable durante toda la vida del proceso.
    val tabs = remember(specs) { specs.map { Tab(ScreenRoutes.tab(it.screen), it.screen, it.label, it.icon, it.iconSelected) } }
    val state = rememberNavigationState(startRoute = tabs.first().key, topLevelRoutes = remember(tabs) { tabs.map { it.key } })
    val navigator = remember(state) { Navigator(state) }
    val shell: ShellViewModel = hiltViewModel()
    val radar by shell.radar.collectAsStateWithLifecycle()
    val badges by shell.badges.collectAsStateWithLifecycle()

    val link by pendingLink.collectAsStateWithLifecycle()
    LaunchedEffect(link) {
        link?.let {
            navigator.apply(it)
            onLinkConsumed()
        }
    }


    // Radar: desplegado a pedido (chip) o, unos segundos, cuando llegan incendios graves nuevos.
    var expanded by rememberSaveable { mutableStateOf(false) }
    val floor = remember { RadarFloorState() }

    val sheets = remember { BottomSheetSceneStrategy<NavKey>() }
    val listDetail = rememberListDetailSceneStrategy<NavKey>()
    val provider = remember(installers, navigator) { entryProvider<NavKey> { installers.forEach { install -> install(navigator) } } }

    // Material 3 Expressive: barra corta abajo en teléfono, riel ancho en tablet (lo decide el tamaño de ventana).
    NavigationSuiteScaffold(
        layoutType = NavigationSuiteScaffoldDefaults.navigationSuiteType(currentWindowAdaptiveInfo()),
        navigationSuiteItems = {
            tabs.forEach { tab ->
                val selected = state.topLevelRoute == tab.key
                item(
                    selected = selected,
                    onClick = { navigator.navigate(tab.key) },
                    icon = { Icon(tabIcon(tab, selected), contentDescription = null) },
                    label = { Text(tab.label) },
                    badge = badges[tab.screen]?.takeIf { it.isNotBlank() && it != "0" }?.let { count ->
                        {
                            Badge(containerColor = OperatorTheme.colors.grave, contentColor = MaterialTheme.colorScheme.onError) {
                                Text(count)
                            }
                        }
                    },
                )
            }
        },
    ) {
        BoxWithConstraints(Modifier.fillMaxSize()) {
            CompositionLocalProvider(
                LocalRadarIndicator provides RadarIndicatorModel(radar.size) { expanded = true },
                LocalRadarFloor provides floor,
            ) {
                NavDisplay(
                    entries = state.toDecoratedEntries(provider),
                    onBack = { navigator.goBack() },
                    sceneStrategies = listOf(sheets, listDetail),
                )
            }
            // El radar es una capa encima de cualquier pantalla, no una clave de la pila. Los incendios nuevos
            // aparecen compactos unos segundos debajo del encabezado (sin bajar del composer) y se pliegan al chip.
            RadarLayer(
                radar = radar,
                expanded = expanded,
                onExpandedChange = { expanded = it },
                maxHeight = maxHeight * 0.75f - RADAR_TOP_OFFSET,
                floor = floor,
                onOpen = { id ->
                    shell.find(id)?.let { fire ->
                        expanded = false
                        navigator.navigate(FiresKey)
                        fireDestination(fire)?.let(navigator::navigate)
                    }
                },
                onHide = shell::hide,
                modifier = Modifier
                    .align(Alignment.TopCenter)
                    .windowInsetsPadding(WindowInsets.statusBars)
                    .padding(top = RADAR_TOP_OFFSET),
            )
            navigator.returnTarget()?.let { session ->
                ReturnButton(session, shell, onClick = { navigator.navigate(InboxKey) }, modifier = Modifier.align(Alignment.BottomCenter))
            }
        }
    }
}

@Composable
private fun tabIcon(tab: Tab, selected: Boolean): ImageVector =
    OperatorIcons.named(if (selected) tab.iconSelected ?: tab.icon else tab.icon) ?: OperatorIcons.named("apps")!!

/**
 * «Volver con …» al chat que el operador dejó para atender un incendio. Lee la bandeja aquí (no en MainShell): así
 * cada mensaje que llega recompone solo este botón.
 */
@Composable
private fun ReturnButton(session: SessionId, shell: ShellViewModel, onClick: () -> Unit, modifier: Modifier = Modifier) {
    val inbox by shell.inbox.collectAsStateWithLifecycle()
    val who = inbox.firstOrNull { it.sessionId == session }
        ?.let { it.customerName ?: it.phone.takeLast(4).let { digits -> "…$digits" } } ?: "el chat"
    // Con contenido propio y no `icon =`/`text =`: esa variante expone el botón a accesibilidad SIN texto (TalkBack
    // decía solo «botón» y el arnés E2E no lo encontraba).
    ExtendedFloatingActionButton(
        onClick = onClick,
        // Con riel lateral o en horizontal, el botón no queda debajo de la barra de gestos ni de los 3 botones.
        modifier = modifier
            .windowInsetsPadding(WindowInsets.safeDrawing.only(WindowInsetsSides.Bottom + WindowInsetsSides.Horizontal))
            .padding(bottom = 16.dp),
    ) {
        Icon(OperatorIcons.ArrowBack, contentDescription = null)
        Spacer(Modifier.width(12.dp))
        Text("Volver con $who")
    }
}
