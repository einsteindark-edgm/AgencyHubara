package com.hubara.operator.feature.auth

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hubara.operator.core.data.auth.AuthRepository
import com.hubara.operator.core.data.auth.AuthState
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

data class LoginUiState(
    val needsNewPassword: Boolean = false,
    val busy: Boolean = false,
    val error: String? = null,
)

@HiltViewModel
class LoginViewModel @Inject constructor(private val auth: AuthRepository) : ViewModel() {
    private val busy = MutableStateFlow(false)
    private val error = MutableStateFlow<String?>(null)

    val state: StateFlow<LoginUiState> = combine(auth.state, busy, error) { s, b, e ->
        LoginUiState(needsNewPassword = s is AuthState.NeedsNewPassword, busy = b, error = e)
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), LoginUiState())

    fun login(email: String, password: String) {
        if (email.isBlank() || password.isBlank()) {
            error.value = "Escribe tu email y tu contraseña."
            return
        }
        run { auth.login(email, password) }
    }

    fun setNewPassword(password: String, confirm: String) {
        when {
            password != confirm -> error.value = "Las contraseñas no coinciden."
            password.length < 12 -> error.value = "La contraseña debe tener al menos 12 caracteres."
            else -> run { auth.completeNewPassword(password) }
        }
    }

    private fun run(block: suspend () -> String?) {
        if (busy.value) return
        viewModelScope.launch {
            busy.value = true
            error.value = null
            error.value = block()
            busy.value = false
        }
    }
}
