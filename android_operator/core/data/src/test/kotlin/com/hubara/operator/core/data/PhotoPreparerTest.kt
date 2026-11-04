package com.hubara.operator.core.data

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.net.Uri
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.data.outbox.AndroidPhotoPreparer
import com.hubara.operator.core.data.outbox.photoTargetSize
import java.io.File
import kotlinx.coroutines.test.runTest
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.GraphicsMode

/**
 * La foto que elige el operador sale como la manda WhatsApp: JPEG de lado ≤ 1600 px (una foto de 12 MP pesa 4–8 MB y
 * el backend corta en 5 MB), sin metadatos (la ubicación de la cámara no le llega al cliente) y con fondo blanco si
 * era un PNG transparente (en JPEG lo transparente sale negro).
 */
@RunWith(AndroidJUnit4::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class PhotoPreparerTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private val preparer = AndroidPhotoPreparer(context)

    private fun source(name: String, bitmap: Bitmap, format: Bitmap.CompressFormat): String {
        val file = File(context.cacheDir, name)
        file.outputStream().use { bitmap.compress(format, 100, it) }
        return Uri.fromFile(file).toString()
    }

    @Test fun el_lado_largo_queda_en_1600_y_nunca_se_agranda() {
        assertThat(photoTargetSize(4000, 3000)).isEqualTo(1600 to 1200)
        assertThat(photoTargetSize(1080, 5400)).isEqualTo(320 to 1600)
        assertThat(photoTargetSize(800, 600)).isEqualTo(800 to 600)
        assertThat(photoTargetSize(1600, 1600)).isEqualTo(1600 to 1600)
    }

    @Test fun una_foto_grande_sale_en_jpeg_de_1600() = runTest {
        val big = Bitmap.createBitmap(4000, 3000, Bitmap.Config.ARGB_8888).apply { eraseColor(Color.rgb(200, 120, 40)) }

        val out = preparer.prepare(source("grande.png", big, Bitmap.CompressFormat.PNG))!!

        val bytes = out.readBytes()
        assertThat(bytes.take(3)).containsExactly(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte()).inOrder()
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(out.path, bounds)
        assertThat(bounds.outWidth to bounds.outHeight).isEqualTo(1600 to 1200)
        assertThat(out.parentFile).isEqualTo(File(context.filesDir, AndroidPhotoPreparer.DIR))
    }

    @Test fun un_png_transparente_queda_con_fondo_blanco_y_no_negro() = runTest {
        val clear = Bitmap.createBitmap(200, 200, Bitmap.Config.ARGB_8888).apply { eraseColor(Color.TRANSPARENT) }

        val out = preparer.prepare(source("transparente.png", clear, Bitmap.CompressFormat.PNG))!!

        val pixel = BitmapFactory.decodeFile(out.path).getPixel(100, 100)
        assertThat(Color.red(pixel)).isAtLeast(245)
        assertThat(Color.green(pixel)).isAtLeast(245)
        assertThat(Color.blue(pixel)).isAtLeast(245)
    }

    @Test fun lo_que_no_es_una_imagen_no_se_prepara() = runTest {
        val notes = File(context.cacheDir, "notas.jpg").apply { writeText("esto no es una foto") }

        assertThat(preparer.prepare(Uri.fromFile(notes).toString())).isNull()
        assertThat(preparer.prepare("content://media/external/images/media/999")).isNull()
    }

    @Test fun cerrar_sesion_borra_las_fotos_preparadas() = runTest {
        val img = Bitmap.createBitmap(10, 10, Bitmap.Config.ARGB_8888)
        val out = preparer.prepare(source("chica.png", img, Bitmap.CompressFormat.PNG))!!

        preparer.clear()

        assertThat(out.exists()).isFalse()
    }
}
