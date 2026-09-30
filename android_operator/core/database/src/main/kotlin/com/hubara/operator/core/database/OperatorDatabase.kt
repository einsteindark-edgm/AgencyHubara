package com.hubara.operator.core.database

import android.content.Context
import androidx.room.AutoMigration
import androidx.room.Database
import androidx.room.RenameColumn
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.AutoMigrationSpec
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
    version = 3,
    exportSchema = true,
    autoMigrations = [
        AutoMigration(from = 1, to = 2, spec = OperatorDatabase.InboundCount::class),
        // v3: nombre de perfil de WhatsApp y vista previa del último mensaje (columnas nuevas, null por defecto).
        AutoMigration(from = 2, to = 3),
    ],
)
abstract class OperatorDatabase : RoomDatabase() {
    /** v2: la bandeja guarda el TOTAL de mensajes del cliente (#384); los no leídos se calculan en la app. */
    @RenameColumn(tableName = "conversations", fromColumnName = "unansweredCount", toColumnName = "inboundCount")
    class InboundCount : AutoMigrationSpec

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
