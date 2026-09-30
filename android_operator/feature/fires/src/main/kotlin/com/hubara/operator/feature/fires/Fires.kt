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
)

@HiltViewModel
class FiresViewModel @Inject constructor(private val repo: FireRepository) : ViewModel() {
    private val filter = MutableStateFlow(FireFilter.TODO)

    val state: StateFlow<FiresUiState> = combine(repo.observeFeed(), filter) { fires, f ->
        FiresUiState(f, f.apply(fires).toImmutableList(), FireFilter.entries.associateWith { it.apply(fires).size })
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), FiresUiState())

    init { viewModelScope.launch { repo.refresh() } }

    fun setFilter(f: FireFilter) { filter.value = f }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FiresScreen(vm: FiresViewModel, onOpen: (NavKey) -> Unit) {
    val ui by vm.state.collectAsStateWithLifecycle()
    Scaffold(topBar = { TopAppBar(title = { Text("Incendios") }, actions = { RadarIndicator() }) }) { inner ->
        Column(Modifier.fillMaxSize().padding(top = inner.calculateTopPadding())) {
            LazyRow(contentPadding = PaddingValues(horizontal = 12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                items(FireFilter.entries) { f ->
                    FilterChip(selected = ui.filter == f, onClick = { vm.setFilter(f) }, label = { Text("${f.label} ${ui.counts[f] ?: 0}") })
                }
            }
            if (ui.fires.isEmpty()) {
                Text("No hay incendios. Todo va bien.", style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(24.dp))
            }
            LazyColumn(
                contentPadding = PaddingValues(start = 12.dp, end = 12.dp, top = 8.dp, bottom = inner.calculateBottomPadding() + 8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(ui.fires, key = { it.id.raw }) { fire ->
                    FireCard(fire, onClick = { destinationFor(fire)?.let(onOpen) })
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
            FiresScreen(hiltViewModel(), onOpen = { navigator.navigate(it) })
        }
    }
}
