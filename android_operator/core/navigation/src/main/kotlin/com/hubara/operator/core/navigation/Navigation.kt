package com.hubara.operator.core.navigation

import androidx.compose.runtime.Composable
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSerializable
import androidx.compose.runtime.setValue
import androidx.lifecycle.viewmodel.navigation3.rememberViewModelStoreNavEntryDecorator
import androidx.navigation3.runtime.EntryProviderScope
import androidx.navigation3.runtime.NavEntry
import androidx.navigation3.runtime.NavKey
import androidx.navigation3.runtime.rememberDecoratedNavEntries
import androidx.navigation3.runtime.rememberNavBackStack
import androidx.navigation3.runtime.rememberSaveableStateHolderNavEntryDecorator
import androidx.navigation3.runtime.serialization.NavKeySerializer
import androidx.savedstate.compose.serialization.serializers.MutableStateSerializer
import com.hubara.operator.core.model.SessionId

/** Cada módulo aporta sus entradas. Recibe el [Navigator] porque las pilas viven en la composición. */
typealias EntryProviderInstaller = EntryProviderScope<NavKey>.(Navigator) -> Unit

/**
 * Estado de navegación con una pila por destino de primer nivel (receta «multiple back stacks» de
 * Navigation 3). Se sale de la app siempre por la pila de inicio (Chats).
 */
class NavigationState(
    val startRoute: NavKey,
    topLevelRoute: MutableState<NavKey>,
    val backStacks: Map<NavKey, MutableList<NavKey>>,
) {
    var topLevelRoute: NavKey by topLevelRoute

    /** Pilas visibles en orden: la de inicio siempre, y encima la del destino actual si es otro. */
    fun routesInUse(): List<NavKey> =
        if (topLevelRoute == startRoute) listOf(startRoute) else listOf(startRoute, topLevelRoute)

    fun visibleKeys(): List<NavKey> = routesInUse().flatMap { backStacks.getValue(it) }

    fun currentStack(): MutableList<NavKey> = backStacks.getValue(topLevelRoute)

    @Composable
    fun toDecoratedEntries(entryProvider: (NavKey) -> NavEntry<NavKey>): List<NavEntry<NavKey>> {
        val decorated = backStacks.mapValues { (_, stack) ->
            rememberDecoratedNavEntries(
                backStack = stack,
                entryDecorators = listOf(
                    rememberSaveableStateHolderNavEntryDecorator(),
                    rememberViewModelStoreNavEntryDecorator(),
                ),
                entryProvider = entryProvider,
            )
        }
        return routesInUse().flatMap { decorated[it].orEmpty() }
    }
}

@Composable
fun rememberNavigationState(startRoute: NavKey = InboxKey, topLevelRoutes: List<NavKey> = TopLevel.roots): NavigationState {
    val topLevelRoute = rememberSerializable(startRoute, topLevelRoutes, serializer = MutableStateSerializer(NavKeySerializer())) {
        mutableStateOf(startRoute)
    }
    val backStacks = topLevelRoutes.associateWith { rememberNavBackStack(it) }
    return remember(startRoute, topLevelRoutes) { NavigationState(startRoute, topLevelRoute, backStacks) }
}

/** Todas las transiciones de la app pasan por acá. Es lógica pura: se prueba en la JVM. */
class Navigator(val state: NavigationState) {

    fun navigate(key: NavKey) {
        if (key in state.backStacks.keys) {
            state.topLevelRoute = key
            return
        }
        val stack = state.currentStack()
        if (stack.lastOrNull() != key) stack.add(key)
    }

    /** false = estamos en la raíz de la pila de inicio: el sistema cierra la app. */
    fun goBack(): Boolean {
        val stack = state.currentStack()
        return when {
            stack.size > 1 -> {
                stack.removeAt(stack.lastIndex)
                true
            }
            state.topLevelRoute != state.startRoute -> {
                state.topLevelRoute = state.startRoute
                true
            }
            else -> false
        }
    }

    /** Aplica la pila de un deep link: el destino queda con exactamente [SyntheticStack.keys] sobre su raíz. */
    fun apply(stack: SyntheticStack) {
        val target = state.backStacks.getValue(stack.root)
        target.clear()
        target.add(stack.root)
        target.addAll(stack.keys)
        state.topLevelRoute = stack.root
    }

    /**
     * El chat al que vuelve la píldora «Volver con …»: el que quedó arriba de la pila de Chats mientras
     * el operador está en otro destino. null si no hay a dónde volver.
     */
    fun returnTarget(): SessionId? {
        if (state.topLevelRoute == state.startRoute) return null
        return when (val top = state.backStacks.getValue(state.startRoute).lastOrNull()) {
            is ChatKey -> top.session
            is LiveKey -> top.session
            else -> null
        }
    }
}
