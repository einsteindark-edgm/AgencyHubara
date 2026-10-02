package com.hubara.operator

import android.app.Application
import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.widget.hot.HotSalesWidgetReceiver
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

/**
 * Auditoría de seguridad (android-intent-security): el receptor del widget estaba exportado y cualquier app podía
 * despertar el proceso (y con él el vigía) con un broadcast explícito. El sistema le entrega APPWIDGET_UPDATE aunque
 * no esté exportado (así lo declara la guía oficial de Glance).
 */
@RunWith(AndroidJUnit4::class)
@Config(application = Application::class)
class ManifestSecurityTest {
    @Test fun el_receptor_del_widget_no_esta_exportado() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val info = context.packageManager.getReceiverInfo(
            ComponentName(context, HotSalesWidgetReceiver::class.java), PackageManager.ComponentInfoFlags.of(0),
        )
        assertThat(info.exported).isFalse()
    }
}
