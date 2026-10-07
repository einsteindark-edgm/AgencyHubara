package com.hubara.operator.feature.screens

import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.SnackbarHostState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.AnnotatedString
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.compose.LifecycleResumeEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hubara.operator.core.navigation.BottomSheetSceneStrategy
import com.hubara.operator.core.navigation.EntryProviderInstaller
import com.hubara.operator.core.navigation.Navigator
import com.hubara.operator.core.navigation.ScreenKey
import com.hubara.operator.core.navigation.ScreenSheetKey
import androidx.compose.material3.adaptive.ExperimentalMaterial3AdaptiveApi
import androidx.compose.material3.adaptive.navigation3.ListDetailSceneStrategy
import androidx.navigation3.runtime.NavKey
import com.hubara.operator.core.designsystem.EmptyState
import com.hubara.operator.core.designsystem.OperatorIcons
import com.hubara.operator.core.navigation.ActionPaletteKey
import com.hubara.operator.core.navigation.ChatKey
import com.hubara.operator.core.navigation.FiresKey
import com.hubara.operator.core.navigation.InboxKey
import com.hubara.operator.core.navigation.LiveKey
import com.hubara.operator.core.navigation.OrderSheetKey
import com.hubara.operator.core.navigation.OrdersKey
import com.hubara.operator.core.navigation.SceneKeys
import com.hubara.operator.core.navigation.ScreenRoutes
import com.hubara.operator.core.navigation.TemplateSheetKey
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.components.ActivityRetainedComponent
import dagger.multibindings.IntoSet
import kotlinx.coroutines.launch

/**
 * Junta el ViewModel con la pantalla y la navegación: los efectos (abrir otra pantalla, un chat, un pedido, un enlace,
 * copiar, avisar) se ejecutan aquí. Las fuentes con `every` se renuevan solo mientras la pantalla está a la vista.
 */
@Suppress("DEPRECATION") // LocalClipboardManager: el reemplazo (LocalClipboard) es suspend y pide ClipEntry; esto basta para texto.
@Composable
fun ServerScreenRoute(vm: ScreenViewModel, navigator: Navigator, isRoot: Boolean, asSheet: Boolean) {
    val ui by vm.state.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }
    val uri = LocalUriHandler.current
    val clipboard = LocalClipboardManager.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    LaunchedEffect(vm, navigator) {
        vm.effects.collect { effect ->
            when (effect) {
                is ScreenEffect.Open -> navigator.navigate(effect.key)
                ScreenEffect.Back -> navigator.goBack()
                is ScreenEffect.OpenUrl -> runCatching { uri.openUri(effect.url) }
                    .onFailure { scope.launch { snackbar.showSnackbar("No se pudo abrir el enlace.") } }
                is ScreenEffect.Copy -> {
                    clipboard.setText(AnnotatedString(effect.text))
                    scope.launch { snackbar.showSnackbar("Copiado") }
                }
                // En otra corrutina: el aviso dura unos segundos y no debe frenar lo que sigue (p. ej. volver atrás).
                is ScreenEffect.Message -> scope.launch { snackbar.showSnackbar(effect.text) }
            }
        }
    }
    LifecycleResumeEffect(vm) {
        vm.setVisible(true)
        onPauseOrDispose { vm.setVisible(false) }
    }

    ServerScreen(
        ui = ui,
        callbacks = ScreenCallbacks(
            onAction = vm::onAction,
            onBind = vm::onBind,
            onRefresh = vm::refresh,
            onRetry = vm::retry,
            onConfirm = vm::onConfirm,
            onBack = if (isRoot) null else ({ navigator.goBack() }),
            onUpdateApp = { runCatching { uri.openUri("https://play.google.com/store/apps/details?id=${context.packageName}") } },
        ),
        snackbar = snackbar,
        asSheet = asSheet,
    )
}

/** Una entrada de la pila armada con su pantalla del servidor ([ScreenRoutes]). */
@Composable
private fun RouteContent(key: NavKey, navigator: Navigator) {
    val route = ScreenRoutes.of(key) ?: return
    ServerScreenRoute(
        hiltViewModel<ScreenViewModel, ScreenViewModel.Factory>(creationCallback = { it.create(route.screen, route.params) }),
        navigator,
        // La raíz de una pestaña no lleva flecha de atrás.
        isRoot = key in navigator.state.backStacks.keys,
        asSheet = route.sheet,
    )
}

/**
 * TODA la app se arma con pantallas del servidor: cada clave de navegación (las de siempre, que usan los enlaces, el
 * radar y las escenas de tablet) se pinta con su pantalla de `android_operator/screens/`. Las hojas inferiores y las
 * escenas de lista + detalle siguen siendo de cada clave.
 */
@Module
@InstallIn(ActivityRetainedComponent::class)
object ScreensNavigation {
    @OptIn(ExperimentalMaterial3Api::class, ExperimentalMaterial3AdaptiveApi::class)
    @Provides @IntoSet
    fun entries(): EntryProviderInstaller = { navigator ->
        // En pantallas anchas, la lista queda a la izquierda y el detalle a la derecha, sin mezclar pestañas.
        entry<InboxKey>(
            metadata = ListDetailSceneStrategy.listPane(
                sceneKey = SceneKeys.CHATS,
                detailPlaceholder = { EmptyState(OperatorIcons.Chat, "Elige un chat", "La conversación se abre aquí.") },
            ),
        ) { RouteContent(it, navigator) }
        entry<FiresKey>(
            metadata = ListDetailSceneStrategy.listPane(
                sceneKey = SceneKeys.FIRES,
                detailPlaceholder = { EmptyState(OperatorIcons.Fire, "Elige un incendio", "El caso se abre aquí.") },
            ),
        ) { RouteContent(it, navigator) }
        entry<OrdersKey>(
            metadata = ListDetailSceneStrategy.listPane(
                sceneKey = SceneKeys.ORDERS,
                detailPlaceholder = { EmptyState(OperatorIcons.Orders, "Elige una orden", "Su ficha se abre al tocarla.") },
            ),
        ) { RouteContent(it, navigator) }
        // ChatKey es de la pila de Chats; LiveKey, de la de Incendios (cada una con su escena en pantallas anchas).
        entry<ChatKey>(metadata = ListDetailSceneStrategy.detailPane(sceneKey = SceneKeys.CHATS)) { RouteContent(it, navigator) }
        entry<LiveKey>(metadata = ListDetailSceneStrategy.detailPane(sceneKey = SceneKeys.FIRES)) { RouteContent(it, navigator) }
        // Hojas largas abren completas (gotcha 11).
        entry<OrderSheetKey>(metadata = BottomSheetSceneStrategy.bottomSheet(expanded = true)) { RouteContent(it, navigator) }
        entry<TemplateSheetKey>(metadata = BottomSheetSceneStrategy.bottomSheet(expanded = true)) { RouteContent(it, navigator) }
        entry<ActionPaletteKey>(metadata = BottomSheetSceneStrategy.bottomSheet()) { RouteContent(it, navigator) }
        entry<ScreenKey> { RouteContent(it, navigator) }
        entry<ScreenSheetKey>(metadata = BottomSheetSceneStrategy.bottomSheet(expanded = true)) { RouteContent(it, navigator) }
    }
}
