package com.hubara.operator.core.data.screens

import android.content.Context
import com.hubara.operator.core.data.config.ServerConfigStore
import com.hubara.operator.core.data.sync.SyncEngine
import dagger.Binds
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import dagger.multibindings.IntoMap
import dagger.multibindings.StringKey
import com.hubara.operator.core.data.screens.sources.AppInfoSource
import com.hubara.operator.core.data.screens.sources.ChatSource
import com.hubara.operator.core.data.screens.sources.ConversationsSource
import com.hubara.operator.core.data.screens.sources.FiresSource
import com.hubara.operator.core.data.screens.sources.RadarSource
import com.hubara.operator.core.data.screens.sources.TemplatesSource

/** Las pantallas del servidor: de dónde bajan, lo que trae el APK, cómo se piden sus datos y los avisos del SSE. */
@Module
@InstallIn(SingletonComponent::class)
abstract class ScreensModule {
    @Binds abstract fun screenDocs(impl: ScreenStore): ScreenDocs
    @Binds abstract fun screenData(impl: ScreenDataClient): ScreenData
    @Binds abstract fun screenDataCache(impl: FileScreenDataCache): ScreenDataCache
    @Binds abstract fun serverChanges(impl: SyncEngine): ServerChanges

    // Fuentes del teléfono, por el nombre que usan los archivos de pantalla (`"app": "conversations"`).
    @Binds @IntoMap @StringKey("conversations") abstract fun conversations(impl: ConversationsSource): AppSource
    @Binds @IntoMap @StringKey("fires") abstract fun fires(impl: FiresSource): AppSource
    @Binds @IntoMap @StringKey("radar") abstract fun radar(impl: RadarSource): AppSource
    @Binds @IntoMap @StringKey("chat") abstract fun chat(impl: ChatSource): AppSource
    @Binds @IntoMap @StringKey("templates") abstract fun templates(impl: TemplatesSource): AppSource
    @Binds @IntoMap @StringKey("app") abstract fun appInfo(impl: AppInfoSource): AppSource

    companion object {
        /** Las pantallas bajan de donde diga la configuración del servidor (por defecto, junto a config.json). */
        @Provides fun screensBase(config: ServerConfigStore): ScreensBase = ScreensBase { config.screensBase() }

        /** Las pantallas del repo que el build copia a `assets/screens/`: sirven sin red desde la primera vez. */
        @Provides fun bundledScreens(@ApplicationContext context: Context): BundledScreens = BundledScreens { file ->
            runCatching { context.assets.open("screens/$file").bufferedReader().use { it.readText() } }.getOrNull()
        }
    }
}
