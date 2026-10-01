package com.hubara.operator.feature.fires

import com.hubara.operator.core.ui.RadarIndicator
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.adaptive.ExperimentalMaterial3AdaptiveApi
import androidx.compose.material3.adaptive.navigation3.ListDetailSceneStrategy
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import androidx.navigation3.runtime.NavKey
import com.hubara.operator.core.data.repo.FireRepository
import com.hubara.operator.core.model.Fire
import com.hubara.operator.core.model.FireSubject
import com.hubara.operator.core.model.Severity
import com.hubara.operator.core.navigation.ChatKey
import com.hubara.operator.core.navigation.EntryProviderInstaller
import com.hubara.operator.core.navigation.FiresKey
import com.hubara.operator.core.navigation.LiveKey
import com.hubara.operator.core.navigation.OrderSheetKey
import com.hubara.operator.core.ui.FireCard
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.components.ActivityRetainedComponent
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.multibindings.IntoSet
import javax.inject.Inject
import kotlinx.collections.immutable.ImmutableList
import kotlinx.collections.immutable.persistentListOf
import kotlinx.collections.immutable.toImmutableList
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import com.hubara.operator.core.ui.listContent
import com.hubara.operator.core.ui.ListContent
import com.hubara.operator.core.designsystem.Spacing
import com.hubara.operator.core.designsystem.OperatorTheme
import com.hubara.operator.core.designsystem.OperatorIcons
import com.hubara.operator.core.designsystem.EmptyState
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.Alignment
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.Box

enum class FireFilter(val label: String) {
    TODO("Todo"), GRAVES("Graves"), CHATS("Chats"), ORDENES("Órdenes");

    fun apply(fires: List<Fire>): List<Fire> = when (this) {
        TODO -> fires
        GRAVES -> fires.filter { it.severity == Severity.GRAVE }
        CHATS -> fires.filter { it.subject is FireSubject.Chat }
        ORDENES -> fires.filter { it.subject is FireSubject.Order }
    }
}

/** A dónde lleva la acción principal de un incendio. null si no hay a dónde ir. */
fun destinationFor(fire: Fire): NavKey? {
    val session = fire.subject.sessionId
    val order = (fire.subject as? FireSubject.Order)?.orderId
    return when (fire.primaryAction.name) {
        "open_order" -> order?.let(::OrderSheetKey) ?: session?.let(::ChatKey)
        "open_live" -> session?.let(::LiveKey)
        else -> session?.let(::ChatKey)
    }
}

data class FiresUiState(
    val filter: FireFilter = FireFilter.TODO,
    val fires: ImmutableList<Fire> = persistentListOf(),
    val counts: Map<FireFilter, Int> = emptyMap(),
    val content: ListContent = ListContent.LOADING,
)

@HiltViewModel
class FiresViewModel @Inject constructor(private val repo: FireRepository) : ViewModel() {
    private val filter = MutableStateFlow(FireFilter.TODO)
    /** null = la primera recarga no ha vuelto; si no, si falló. */
    private val refresh = MutableStateFlow<Boolean?>(null)

    val state: StateFlow<FiresUiState> = combine(repo.observeFeed(), filter, refresh) { fires, f, failed ->
        val shown = f.apply(fires)
        FiresUiState(
            f, shown.toImmutableList(), FireFilter.entries.associateWith { it.apply(fires).size },
            listContent(isEmpty = shown.isEmpty(), refreshed = failed != null, failed = failed == true),
        )
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), FiresUiState())

    init { viewModelScope.launch { refresh.value = repo.refresh().isFailure } }

    fun setFilter(f: FireFilter) { filter.value = f }
}

@Composable
fun FiresRoute(vm: FiresViewModel, onOpen: (NavKey) -> Unit) {
    val ui by vm.state.collectAsStateWithLifecycle()
    FiresScreen(ui, onFilter = vm::setFilter, onOpen = onOpen)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FiresScreen(ui: FiresUiState, onFilter: (FireFilter) -> Unit, onOpen: (NavKey) -> Unit) {
    val scroll = TopAppBarDefaults.pinnedScrollBehavior()
    Scaffold(
        modifier = Modifier.nestedScroll(scroll.nestedScrollConnection),
        topBar = {
            TopAppBar(title = { Text("Incendios", style = OperatorTheme.emphasized.titleLarge) }, actions = { RadarIndicator() }, scrollBehavior = scroll)
        },
    ) { inner ->
        val start = inner.calculateLeftPadding(LayoutDirection.Ltr)
        val end = inner.calculateRightPadding(LayoutDirection.Ltr)
        Column(Modifier.fillMaxSize().padding(top = inner.calculateTopPadding(), start = start, end = end)) {
            LazyRow(contentPadding = PaddingValues(horizontal = Spacing.margin), horizontalArrangement = Arrangement.spacedBy(Spacing.sm)) {
                items(FireFilter.entries) { f ->
                    val selected = ui.filter == f
                    FilterChip(
                        selected = selected, onClick = { onFilter(f) }, label = { Text("${f.label} ${ui.counts[f] ?: 0}") },
                        leadingIcon = if (selected) {
                            { Icon(OperatorIcons.Check, contentDescription = null, modifier = Modifier.size(FilterChipDefaults.IconSize)) }
                        } else null,
                        shape = CircleShape,
                    )
                }
            }
            when (ui.content) {
                ListContent.LOADING -> Box(Modifier.fillMaxWidth().padding(Spacing.xxl), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
                ListContent.EMPTY -> EmptyState(OperatorIcons.Fire, "No hay incendios", "Todo va bien.")
                ListContent.ERROR -> EmptyState(OperatorIcons.Error, "No se pudieron cargar", "Revisa la conexión; se reintenta solo.")
                ListContent.LIST -> LazyColumn(
                    contentPadding = PaddingValues(
                        start = Spacing.margin, end = Spacing.margin, top = Spacing.md, bottom = inner.calculateBottomPadding() + Spacing.sm,
                    ),
                    verticalArrangement = Arrangement.spacedBy(Spacing.sm),
                ) {
                    items(ui.fires, key = { it.id.raw }) { fire ->
                        FireCard(fire, onClick = { destinationFor(fire)?.let(onOpen) }, modifier = Modifier.animateItem())
                    }
                }
            }
        }
    }
}

@Module
@InstallIn(ActivityRetainedComponent::class)
object FiresNavigation {
    @OptIn(ExperimentalMaterial3AdaptiveApi::class, ExperimentalMaterial3Api::class)
    @Provides @IntoSet
    fun entries(): EntryProviderInstaller = { navigator ->
        entry<FiresKey>(metadata = ListDetailSceneStrategy.listPane()) {
            FiresRoute(hiltViewModel(), onOpen = { navigator.navigate(it) })
        }
    }
}
