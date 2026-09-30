package com.hubara.operator.core.database

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Database(
    entities = [
        ConversationEntity::class, MessageEntity::class, OutboxEntity::class, DraftEntity::class,
        SuggestionSetEntity::class, FireEntity::class, HiddenFireEntity::class,
    ],
    version = 1,
    exportSchema = true,
)
abstract class OperatorDatabase : RoomDatabase() {
    abstract fun conversations(): ConversationDao
    abstract fun messages(): MessageDao
    abstract fun outbox(): OutboxDao
    abstract fun drafts(): DraftDao
    abstract fun suggestions(): SuggestionDao
    abstract fun fires(): FireDao
}

@Module
@InstallIn(SingletonComponent::class)
object DatabaseModule {
    @Provides @Singleton
    fun database(@ApplicationContext context: Context): OperatorDatabase =
        Room.databaseBuilder(context, OperatorDatabase::class.java, "operator.db").build()

    @Provides fun conversations(db: OperatorDatabase) = db.conversations()
    @Provides fun messages(db: OperatorDatabase) = db.messages()
    @Provides fun outbox(db: OperatorDatabase) = db.outbox()
    @Provides fun drafts(db: OperatorDatabase) = db.drafts()
    @Provides fun suggestions(db: OperatorDatabase) = db.suggestions()
    @Provides fun fires(db: OperatorDatabase) = db.fires()
}
