package com.hubara.operator.core.push

import android.content.Context
import dagger.Binds
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent

@Module
@InstallIn(SingletonComponent::class)
abstract class PushModule {
    @Binds abstract fun transport(impl: FirebasePushTransport): PushTransport

    @Binds abstract fun api(impl: RetrofitPushApi): PushApi

    @Binds abstract fun pass(impl: Vigia): VigiaPass

    companion object {
        @Provides fun later(@ApplicationContext context: Context): VigiaLater = VigiaLater { VigiaWorker.runSoon(context) }

        @Provides fun appVersion(@ApplicationContext context: Context): AppVersion =
            AppVersion { context.packageManager.getPackageInfo(context.packageName, 0).versionName ?: "desconocida" }
    }
}
