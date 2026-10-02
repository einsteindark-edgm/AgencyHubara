package com.hubara.operator.core.push

import android.content.Context
import androidx.core.app.NotificationManagerCompat
import com.hubara.operator.core.data.auth.AuthRepository
import com.hubara.operator.core.data.repo.SeenRepository
import com.hubara.operator.core.data.screens.ScreenDataCache
import com.hubara.operator.core.database.OperatorDatabase
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** «Cerrar sesión»: revoca la sesión y borra del teléfono todo lo que tenga datos de clientes. */
class SignOut @Inject constructor(
    @ApplicationContext private val context: Context,
    private val auth: AuthRepository,
    private val db: OperatorDatabase,
    private val seen: SeenRepository,
    private val ambient: AmbientStore,
    private val widget: HotWidgetUpdater,
    private val screenData: ScreenDataCache,
) {
    suspend operator fun invoke() {
        auth.signOut()
        withContext(Dispatchers.IO) { db.clearAllTables() }   // bandeja, mensajes, outbox, borradores, incendios
        seen.clear()
        withContext(Dispatchers.IO) { screenData.clear() }    // lo último que mostró cada pantalla del servidor
        ambient.clear()                                       // ventas calientes del widget y avisados
        NotificationManagerCompat.from(context).cancelAll()
        widget.update(context)                                // la pantalla de inicio deja de mostrar nombres
    }
}
