package com.hubara.operator.feature.auth

import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.designsystem.OperatorTheme
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** La política de privacidad también se ve antes de entrar (Google Play la exige dentro de la app). */
@RunWith(AndroidJUnit4::class)
class LoginPrivacyTest {
    @get:Rule val compose = createComposeRule()

    @Test fun la_politica_de_privacidad_se_abre_desde_el_login() {
        var opened = 0
        compose.setContent { OperatorTheme { LoginContent(LoginUiState(), onLogin = { _, _ -> }, onSetNewPassword = { _, _ -> }, onOpenPrivacy = { opened++ }) } }
        compose.onNodeWithText("Política de privacidad").performClick()
        assertThat(opened).isEqualTo(1)
    }
}
