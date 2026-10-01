package com.hubara.operator.feature.chat

import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.adaptive.ExperimentalMaterial3AdaptiveApi
import androidx.compose.material3.adaptive.navigation3.ListDetailSceneStrategy
import androidx.compose.runtime.Composable
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.compose.dropUnlessResumed
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.navigation.ActionPaletteKey
import com.hubara.operator.core.navigation.BottomSheetSceneStrategy
import com.hubara.operator.core.navigation.ChatKey
import com.hubara.operator.core.navigation.EntryProviderInstaller
import com.hubara.operator.core.navigation.LiveKey
import com.hubara.operator.core.navigation.Navigator
import com.hubara.operator.core.navigation.OrderSheetKey
import com.hubara.operator.core.navigation.TemplateSheetKey
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.components.ActivityRetainedComponent
import dagger.multibindings.IntoSet
import com.hubara.operator.core.navigation.SceneKeys

@Module
@InstallIn(ActivityRetainedComponent::class)
object ChatNavigation {
    @OptIn(ExperimentalMaterial3AdaptiveApi::class, ExperimentalMaterial3Api::class)
    @Provides @IntoSet
    fun entries(): EntryProviderInstaller = { navigator ->
        // ChatKey es de la pila de Chats; LiveKey, de la de Incendios (cada una con su escena en pantallas anchas).
        entry<ChatKey>(metadata = ListDetailSceneStrategy.detailPane(sceneKey = SceneKeys.CHATS)) { key -> ChatRoute(key.session, navigator) }
        // En vivo es el mismo chat: con el bot en control muestra la etapa y el botón para tomarla.
        entry<LiveKey>(metadata = ListDetailSceneStrategy.detailPane(sceneKey = SceneKeys.FIRES)) { key -> ChatRoute(key.session, navigator) }
        entry<TemplateSheetKey>(metadata = BottomSheetSceneStrategy.bottomSheet(expanded = true)) { key ->
            val vm = hiltViewModel<TemplateSheetViewModel, TemplateSheetViewModel.Factory>(creationCallback = { it.create(key.session.raw) })
            TemplateSheet(vm, onDone = { navigator.goBack() })
        }
        entry<ActionPaletteKey>(metadata = BottomSheetSceneStrategy.bottomSheet()) { key ->
            val vm = hiltViewModel<ActionPaletteViewModel, ActionPaletteViewModel.Factory>(creationCallback = { it.create(key.session.raw) })
            ActionPalette(vm, onDone = { navigator.goBack() })
        }
    }
}

@Composable
private fun ChatRoute(session: SessionId, navigator: Navigator) {
    val vm = hiltViewModel<ChatViewModel, ChatViewModel.Factory>(creationCallback = { it.create(session.raw) })
    ChatScreen(
        vm = vm,
        onBack = dropUnlessResumed { navigator.goBack() },
        onOpenOrder = { navigator.navigate(OrderSheetKey(it)) },
        onOpenPalette = dropUnlessResumed { navigator.navigate(ActionPaletteKey(session)) },
        onReactivate = dropUnlessResumed { navigator.navigate(TemplateSheetKey(session)) },
    )
}
