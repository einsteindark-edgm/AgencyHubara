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

@Composable
fun LoginScreen(vm: LoginViewModel = hiltViewModel()) {
    val ui by vm.state.collectAsStateWithLifecycle()
    // Sin Scaffold: el padding de las barras y del teclado va en el contenedor (skill edge-to-edge).
    Column(
        modifier = Modifier
            .fillMaxSize()
            .safeDrawingPadding()
            .imePadding()
            .verticalScroll(rememberScrollState())
            .padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text("App Operador", style = MaterialTheme.typography.headlineMedium)
        if (ui.needsNewPassword) NewPasswordForm(ui, vm::setNewPassword) else CredentialsForm(ui, vm::login)
        ui.error?.let {
            Text(it, color = MaterialTheme.colorScheme.error, modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
        }
    }
}

@Composable
private fun CredentialsForm(ui: LoginUiState, onLogin: (String, String) -> Unit) {
    var email by rememberSaveable { mutableStateOf("") }
    var password by rememberSaveable { mutableStateOf("") }
    OutlinedTextField(
        value = email, onValueChange = { email = it }, label = { Text("Email") }, singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email), modifier = Modifier.fillMaxWidth(),
    )
    OutlinedTextField(
        value = password, onValueChange = { password = it }, label = { Text("Contraseña") }, singleLine = true,
        visualTransformation = PasswordVisualTransformation(),
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password), modifier = Modifier.fillMaxWidth(),
    )
    Button(onClick = { onLogin(email, password) }, enabled = !ui.busy, modifier = Modifier.fillMaxWidth()) {
        if (ui.busy) CircularProgressIndicator(Modifier.padding(2.dp)) else Text("Entrar")
    }
}

@Composable
private fun NewPasswordForm(ui: LoginUiState, onSet: (String, String) -> Unit) {
    var password by rememberSaveable { mutableStateOf("") }
    var confirm by rememberSaveable { mutableStateOf("") }
    Text("Es tu primer ingreso: elige una contraseña nueva.", style = MaterialTheme.typography.bodyMedium)
    OutlinedTextField(
        value = password, onValueChange = { password = it }, label = { Text("Contraseña nueva") }, singleLine = true,
        visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(),
    )
    OutlinedTextField(
        value = confirm, onValueChange = { confirm = it }, label = { Text("Repítela") }, singleLine = true,
        visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(),
    )
    Button(onClick = { onSet(password, confirm) }, enabled = !ui.busy, modifier = Modifier.fillMaxWidth()) { Text("Guardar y entrar") }
}
