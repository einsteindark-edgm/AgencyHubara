package com.hubara.operator.feature.inbox

import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.adaptive.ExperimentalMaterial3AdaptiveApi
import androidx.compose.material3.adaptive.navigation3.ListDetailSceneStrategy
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import com.hubara.operator.core.navigation.ChatKey
import com.hubara.operator.core.navigation.EntryProviderInstaller
import com.hubara.operator.core.navigation.InboxKey
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.components.ActivityRetainedComponent
import dagger.multibindings.IntoSet
import com.hubara.operator.core.designsystem.OperatorIcons
import com.hubara.operator.core.designsystem.EmptyState
import com.hubara.operator.core.navigation.SceneKeys

@Module
@InstallIn(ActivityRetainedComponent::class)
object InboxNavigation {
    @OptIn(ExperimentalMaterial3AdaptiveApi::class, ExperimentalMaterial3Api::class)
    @Provides @IntoSet
    fun entries(): EntryProviderInstaller = { navigator ->
        // En pantallas anchas, la bandeja queda a la izquierda y el chat a la derecha.
        entry<InboxKey>(
            metadata = ListDetailSceneStrategy.listPane(
                sceneKey = SceneKeys.CHATS,
                detailPlaceholder = { EmptyState(OperatorIcons.Chat, "Elige un chat", "La conversación se abre aquí.") },
            ),
        ) {
            InboxRoute(hiltViewModel(), onOpen = { navigator.navigate(ChatKey(it.sessionId)) })
        }
    }
}
