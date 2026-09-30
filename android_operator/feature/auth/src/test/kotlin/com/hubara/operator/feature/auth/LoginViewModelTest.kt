package com.hubara.operator.feature.auth

import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.data.auth.AuthRepository
import com.hubara.operator.core.data.auth.InMemoryTokenStore
import com.hubara.operator.core.network.auth.CognitoClient
import com.hubara.operator.core.network.di.ApiConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import org.junit.After
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class LoginViewModelTest {
    private val url = "http://localhost:1/".toHttpUrl()

    /** El Main de prueba comparte el scheduler de runTest: así el estado combinado se ve al instante. */
    private fun TestScope.viewModel(): LoginViewModel {
        Dispatchers.setMain(UnconfinedTestDispatcher(testScheduler))
        val auth = AuthRepository(ApiConfig(url, "client-test", "us-east-1"), CognitoClient(OkHttpClient(), url, "client-test"), InMemoryTokenStore()) { 0L }
        return LoginViewModel(auth).also { vm -> backgroundScope.launch(UnconfinedTestDispatcher(testScheduler)) { vm.state.collect {} } }
    }

    @After fun tearDown() = Dispatchers.resetMain()

    @Test fun sin_email_o_contrasena_no_llama_a_cognito() = runTest {
        val vm = viewModel()
        vm.login(" ", "")
        assertThat(vm.state.value.error).isEqualTo("Escribe tu email y tu contraseña.")
    }

    @Test fun la_contrasena_nueva_se_valida_antes_de_enviarse() = runTest {
        val vm = viewModel()
        vm.setNewPassword("Una-Clave-Larga-1", "otra")
        assertThat(vm.state.value.error).isEqualTo("Las contraseñas no coinciden.")
        vm.setNewPassword("corta", "corta")
        assertThat(vm.state.value.error).isEqualTo("La contraseña debe tener al menos 12 caracteres.")
    }
}
