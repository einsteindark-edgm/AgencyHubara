@file:OptIn(androidx.compose.material3.ExperimentalMaterial3ExpressiveApi::class)

package com.hubara.operator.feature.auth

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hubara.operator.core.designsystem.Spacing
import com.hubara.operator.core.designsystem.OperatorIcons
import com.hubara.operator.core.designsystem.IconTile
import androidx.compose.runtime.remember
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.heightIn
import androidx.compose.material3.ButtonDefaults
import com.hubara.operator.core.ui.LocalSessionActions
import androidx.compose.material3.TextButton

@Composable
fun LoginScreen(vm: LoginViewModel = hiltViewModel()) {
    val ui by vm.state.collectAsStateWithLifecycle()
    LoginContent(ui, onLogin = vm::login, onSetNewPassword = vm::setNewPassword, onOpenPrivacy = LocalSessionActions.current?.openPrivacy)
}

/** El login sin ViewModel. */
@Composable
fun LoginContent(
    ui: LoginUiState,
    onLogin: (String, String) -> Unit,
    onSetNewPassword: (String, String) -> Unit,
    onOpenPrivacy: (() -> Unit)? = null,
) {
    // Sin Scaffold: el padding de las barras y del teclado va en el contenedor (skill edge-to-edge).
    Column(
        modifier = Modifier
            .fillMaxSize()
            .safeDrawingPadding()
            .imePadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Spacing.xl, vertical = Spacing.xxl),
        verticalArrangement = Arrangement.spacedBy(Spacing.lg),
    ) {
        IconTile(
            OperatorIcons.Storefront, MaterialTheme.colorScheme.primaryContainer, MaterialTheme.colorScheme.onPrimaryContainer,
            size = 72.dp, shape = MaterialTheme.shapes.extraLargeIncreased,
        )
        Column(verticalArrangement = Arrangement.spacedBy(Spacing.xs)) {
            Text("App Operador", style = MaterialTheme.typography.headlineMediumEmphasized)
            Text("Atiende los chats, los incendios y los pedidos de la tienda.",
                style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (ui.needsNewPassword) NewPasswordForm(ui, onSetNewPassword) else CredentialsForm(ui, onLogin)
        ui.error?.let {
            Text(it, color = MaterialTheme.colorScheme.error, modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
        }
        onOpenPrivacy?.let { open -> TextButton(onClick = open) { Text("Política de privacidad") } }
    }
}

private val BigButton = Modifier.fillMaxWidth().heightIn(min = 56.dp)

@Composable
private fun CredentialsForm(ui: LoginUiState, onLogin: (String, String) -> Unit) {
    var email by rememberSaveable { mutableStateOf("") }
    // La contraseña no va al estado guardado de la actividad (remember, no rememberSaveable).
    var password by remember { mutableStateOf("") }
    OutlinedTextField(
        value = email, onValueChange = { email = it }, label = { Text("Email") }, singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email), modifier = Modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.large,
    )
    OutlinedTextField(
        value = password, onValueChange = { password = it }, label = { Text("Contraseña") }, singleLine = true,
        visualTransformation = PasswordVisualTransformation(),
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password), modifier = Modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.large,
    )
    Button(onClick = { onLogin(email, password) }, shapes = ButtonDefaults.shapes(), enabled = !ui.busy, modifier = BigButton) {
        if (ui.busy) CircularProgressIndicator(Modifier.size(24.dp), strokeWidth = 3.dp) else Text("Entrar", style = MaterialTheme.typography.titleSmall)
    }
}

@Composable
private fun NewPasswordForm(ui: LoginUiState, onSet: (String, String) -> Unit) {
    var password by remember { mutableStateOf("") }
    var confirm by remember { mutableStateOf("") }
    Text("Es tu primer ingreso: elige una contraseña nueva.", style = MaterialTheme.typography.bodyMedium)
    OutlinedTextField(
        value = password, onValueChange = { password = it }, label = { Text("Contraseña nueva") }, singleLine = true,
        visualTransformation = PasswordVisualTransformation(),
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password), modifier = Modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.large,
    )
    OutlinedTextField(
        value = confirm, onValueChange = { confirm = it }, label = { Text("Repítela") }, singleLine = true,
        visualTransformation = PasswordVisualTransformation(),
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password), modifier = Modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.large,
    )
    Button(onClick = { onSet(password, confirm) }, shapes = ButtonDefaults.shapes(), enabled = !ui.busy, modifier = BigButton) {
        Text("Guardar y entrar", style = MaterialTheme.typography.titleSmall)
    }
}
