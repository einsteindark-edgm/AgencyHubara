package com.hubara.operator.core.ui

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat

private tailrec fun Context.findActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.findActivity()
    else -> null
}

/**
 * Pide el permiso de notificaciones en contexto, con el flujo de tres estados del skill
 * android-permissions-security: se explica antes de pedir, y si se negó para siempre se ofrece Ajustes.
 */
@Composable
fun NotificationPermissionBanner(modifier: Modifier = Modifier) {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
    val context = LocalContext.current
    val permission = Manifest.permission.POST_NOTIFICATIONS
    var granted by remember { mutableStateOf(ContextCompat.checkSelfPermission(context, permission) == PackageManager.PERMISSION_GRANTED) }
    var dismissed by rememberSaveable { mutableStateOf(false) }
    var blocked by rememberSaveable { mutableStateOf(false) }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        granted = ok
        // Negado y sin «rationale» = negado para siempre: solo queda Ajustes.
        blocked = !ok && context.findActivity()?.shouldShowRequestPermissionRationale(permission) == false
    }
    if (granted || dismissed) return
    Surface(tonalElevation = 2.dp, modifier = modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp)) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("Activa las notificaciones para enterarte de los incendios graves con la app cerrada.", style = MaterialTheme.typography.bodyMedium)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (blocked) {
                    TextButton(onClick = {
                        context.startActivity(Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName))
                    }) { Text("Abrir ajustes") }
                } else {
                    TextButton(onClick = { launcher.launch(permission) }) { Text("Activar") }
                }
                TextButton(onClick = { dismissed = true }) { Text("Ahora no") }
            }
        }
    }
}
