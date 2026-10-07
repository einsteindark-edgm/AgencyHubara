import org.gradle.api.DefaultTask
import org.gradle.api.file.ConfigurableFileCollection
import org.gradle.api.file.DirectoryProperty
import org.gradle.api.tasks.InputFiles
import org.gradle.api.tasks.OutputDirectory
import org.gradle.api.tasks.PathSensitive
import org.gradle.api.tasks.PathSensitivity
import org.gradle.api.tasks.TaskAction

/**
 * Copia los JSON de las pantallas del servidor (carpeta `android_operator/screens`) a `assets/screens/` del APK: la app
 * las muestra sin red desde la primera vez y las reemplaza por las que publica el deploy.
 *
 * Ojo al documentarla: en Kotlin, una barra seguida de asterisco DENTRO de un comentario abre otro comentario anidado.
 * Escrito en `app/build.gradle.kts`, ese comentario se tragó en silencio el bloque `dependencies {}` de la app (el
 * classpath quedó sin ningún módulo y KSP no encontraba nada). Por eso los globs van sin escribir aquí.
 */
abstract class BundleServerScreens : DefaultTask() {
    @get:InputFiles
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val screens: ConfigurableFileCollection

    @get:OutputDirectory
    abstract val outputDir: DirectoryProperty

    @TaskAction
    fun copy() {
        val target = outputDir.get().dir("screens").asFile
        target.deleteRecursively()
        target.mkdirs()
        screens.files.forEach { it.copyTo(target.resolve(it.name), overwrite = true) }
    }
}
