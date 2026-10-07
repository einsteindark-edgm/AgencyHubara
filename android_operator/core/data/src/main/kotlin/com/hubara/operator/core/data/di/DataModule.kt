package com.hubara.operator.core.data.di

import com.hubara.operator.core.data.ApplicationScope
import com.hubara.operator.core.data.Clock
import com.hubara.operator.core.data.auth.AuthRepository
import com.hubara.operator.core.data.auth.TinkTokenStore
import com.hubara.operator.core.data.auth.TokenStore
import com.hubara.operator.core.data.outbox.OutboxScheduler
import com.hubara.operator.core.data.outbox.WorkManagerOutboxScheduler
import com.hubara.operator.core.network.di.AccessTokenProvider
import dagger.Binds
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob

@Module
@InstallIn(SingletonComponent::class)
abstract class DataBindings {
    @Binds abstract fun tokenStore(impl: TinkTokenStore): TokenStore
    @Binds abstract fun accessTokens(impl: AuthRepository): AccessTokenProvider
    @Binds abstract fun outboxScheduler(impl: WorkManagerOutboxScheduler): OutboxScheduler
}

@Module
@InstallIn(SingletonComponent::class)
object DataModule {
    @Provides fun clock(): Clock = Clock.SYSTEM

    @Provides @Singleton @ApplicationScope
    fun applicationScope(): CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
}
