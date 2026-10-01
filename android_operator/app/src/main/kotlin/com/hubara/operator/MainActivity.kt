package com.hubara.operator

import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.hubara.operator.core.data.auth.AuthRepository
import com.hubara.operator.core.data.auth.AuthState
import com.hubara.operator.core.data.sync.SyncEngine
import com.hubara.operator.core.designsystem.OperatorTheme
import com.hubara.operator.core.navigation.DeepLinks
import com.hubara.operator.core.navigation.EntryProviderInstaller
import com.hubara.operator.core.navigation.SyntheticStack
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject
import kotlin.coroutines.cancellation.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

@AndroidEntryPoint
class MainActivity : ComponentActivity() {
    @Inject lateinit var installers: Set<@JvmSuppressWildcards EntryProviderInstaller>
    @Inject lateinit var auth: AuthRepository
    @Inject lateinit var sync: SyncEngine

    /** Deep link pendiente (notificación o widget), ya validado. */
    private val pendingLink = MutableStateFlow<SyntheticStack?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()                                   // antes de setContent (skill edge-to-edge)
        super.onCreate(savedInstanceState)
        // Con NavigationBar al pie, el sistema no agrega su velo translúcido (skill edge-to-edge).
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) window.isNavigationBarContrastEnforced = false
        // Solo en el arranque: al recrear (girar, tema, letra, muerte del proceso) manda la pila restaurada, no el
        // enlace con el que se abrió la app. Abrir desde Recientes reentrega el intent viejo: tampoco cuenta.
        val fromRecents = (intent?.flags ?: 0) and Intent.FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY != 0
        if (savedInstanceState == null && !fromRecents) pendingLink.value = DeepLinks.parse(intent?.dataString)

        lifecycleScope.launch { auth.restore() }
        // El SSE solo corre con la app en primer plano y la sesión abierta. En segundo plano manda el push.
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                auth.state.collectLatest { state ->
                    if (state == AuthState.SignedIn || state == AuthState.DevMode) {
                        try {
                            sync.run()
                        } catch (e: CancellationException) {
                            throw e
                        } catch (e: Exception) {
                            Log.w("Operator", "la sincronización se detuvo", e)
                        }
                    }
                }
            }
        }

        setContent {
            OperatorTheme {
                OperatorApp(installers = installers, auth = auth, pendingLink = pendingLink, onLinkConsumed = { pendingLink.value = null })
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)                                    // skill android-intent-security
        pendingLink.value = DeepLinks.parse(intent.dataString)   // mismas reglas que en onCreate
    }
}
