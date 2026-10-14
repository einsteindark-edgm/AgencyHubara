package com.hubara.operator.feature.chat

import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.assertHasClickAction
import androidx.compose.ui.test.assertHasNoClickAction
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performSemanticsAction
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.hubara.operator.core.designsystem.OperatorTheme
import com.hubara.operator.core.model.Author
import com.hubara.operator.core.model.CardButton
import com.hubara.operator.core.model.CardIcon
import com.hubara.operator.core.model.CardImage
import com.hubara.operator.core.model.CardItem
import com.hubara.operator.core.model.CardLink
import com.hubara.operator.core.model.Message
import com.hubara.operator.core.model.MessageCard
import java.time.ZoneId
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Lo que el bot le mandó al cliente y no es texto (catálogo, botones, formulario, resumen del pedido, enlace, foto)
 * se ve como el mensaje que fue: qué es, el texto ENTERO que leyó el cliente y lo que traía. Caso 2026-10-13: el
 * saludo iba dentro del texto del catálogo y la app solo decía, en gris, «El bot envió el catálogo con 2 productos».
 */
@RunWith(AndroidJUnit4::class)
class BotCardBubbleTest {
    @get:Rule val compose = createComposeRule()

    private val bogota = ZoneId.of("America/Bogota")

    private fun sent(card: MessageCard, author: Author = Author.BOT, note: String = "🛍️ El bot envió el catálogo con 2 productos") =
        Message(key = "w1", author = author, text = note, imageUrl = null, timestampMs = 1_791_924_068_000L, card = card)

    private val catalog = MessageCard(
        component = "products_list", label = "Catálogo", icon = CardIcon.CATALOG,
        text = "Buenas tardes, bienvenido a *Hubara*.\n\nPara la Noche de Velitas tenemos estas velas:",
        items = listOf(CardItem("Velas de deseos onduladas", "\$22.500 COP"), CardItem("Velas de deseos cuadradas", "\$25.000 COP")),
    )

    private fun show(message: Message) = compose.setContent { OperatorTheme { MessageBubble(message, zone = bogota) } }

    @Test fun el_catalogo_dice_que_es_y_muestra_el_saludo_que_leyo_el_cliente() {
        show(sent(catalog))

        compose.onNodeWithText("Catálogo").assertExists()
        // Como en WhatsApp: «*Hubara*» en negrita, sin los asteriscos.
        compose.onNodeWithText("Buenas tardes, bienvenido a Hubara.", substring = true).assertExists()
        compose.onNodeWithText("*Hubara*", substring = true).assertDoesNotExist()
        compose.onNodeWithText("El bot envió el catálogo", substring = true).assertDoesNotExist()
    }

    @Test fun y_los_productos_que_traia_con_su_precio() {
        show(sent(catalog))

        compose.onNodeWithText("Velas de deseos onduladas").assertExists()
        compose.onNodeWithText("\$22.500 COP").assertExists()
        compose.onNodeWithText("Velas de deseos cuadradas").assertExists()
    }

    @Test fun una_lista_larga_se_resume_y_se_abre_entera() {
        val many = catalog.copy(items = (1..10).map { CardItem("Vela $it", "\$1.000 COP") })
        show(sent(many))

        compose.onNodeWithText("Vela 6").assertExists()
        compose.onNodeWithText("Vela 7").assertDoesNotExist()
        // Por semántica: en el lienzo de prueba (320 px) la fila queda fuera de pantalla y un toque no llega (gotcha 24).
        compose.onNodeWithText("Ver los 10").assertHasClickAction().performSemanticsAction(SemanticsActions.OnClick)
        compose.onNodeWithText("Vela 10").assertExists()
    }

    @Test fun el_catalogo_de_una_categoria_la_nombra() {
        show(sent(catalog.copy(title = "Religiosas")))

        compose.onNodeWithText("Catálogo · Religiosas").assertExists()
    }

    @Test fun el_resumen_muestra_sus_botones_y_marca_el_que_toco_el_cliente() {
        show(
            sent(
                MessageCard(
                    component = "order_confirmation", label = "Resumen para confirmar", icon = CardIcon.ORDER,
                    text = "*Resumen de tu pedido*\n• 2× Velón Koala — \$35.000\nTotal: \$82.000 COP",
                    buttons = listOf(CardButton("✅ Confirmar", touched = true), CardButton("✏️ Modificar"), CardButton("❌ Cancelar")),
                ),
            ),
        )

        compose.onNodeWithText("Resumen para confirmar").assertExists()
        compose.onNodeWithText("✏️ Modificar").assertExists().assertHasNoClickAction()
        compose.onNodeWithText("❌ Cancelar").assertExists()
        compose.onNodeWithText("Tocó").assertExists()
        compose.onNodeWithContentDescription("✅ Confirmar, el cliente tocó este botón").assertExists()
    }

    @Test fun el_formulario_muestra_su_mensaje_y_el_boton_que_vio_el_cliente_sin_ser_tocable() {
        show(
            sent(
                MessageCard(
                    component = "shipping_flow", label = "Formulario de envío", icon = CardIcon.FORM,
                    text = "Para enviarte tu pedido necesito unos datos", action = "Completar datos",
                ),
            ),
        )

        compose.onNodeWithText("Formulario de envío").assertExists()
        compose.onNodeWithText("Para enviarte tu pedido necesito unos datos").assertExists()
        compose.onNodeWithText("Completar datos").assertExists().assertHasNoClickAction()
    }

    @Test fun el_boton_con_enlace_se_puede_abrir() {
        show(
            sent(
                MessageCard(
                    component = "cta_url", label = "Botón con enlace", icon = CardIcon.LINK,
                    text = "Aquí puedes pagar tu pedido", link = CardLink("Pagar ahora", "https://pago.test/x"),
                ),
            ),
        )

        compose.onNodeWithText("Pagar ahora").assertHasClickAction()
    }

    @Test fun la_foto_del_producto_lleva_su_texto() {
        show(
            sent(
                MessageCard(
                    component = "product_detail", label = "Foto del producto", icon = CardIcon.PHOTO,
                    images = listOf(CardImage("https://cdn.test/cruz.jpg", "Vela Cruz de Vida")),
                ),
            ),
        )

        compose.onNodeWithText("Foto del producto").assertExists()
        compose.onNodeWithText("Vela Cruz de Vida").assertExists()
        compose.onNodeWithContentDescription("Foto enviada: Vela Cruz de Vida").assertExists()
    }

    @Test fun lo_que_mando_el_operador_desde_la_app_dice_operador() {
        show(sent(catalog, author = Author.HUMAN))

        compose.onNodeWithText("Operador").assertExists()
        compose.onNodeWithText("Catálogo").assertExists()
    }

    // Las fotos del CDN de productos (https) se ven tal cual; una ruta del backend va por mediaUrl (con su token);
    // lo demás (http, otro esquema) no se carga ni se abre.
    @Test fun solo_se_cargan_fotos_y_enlaces_seguros() {
        val card = MessageCard(
            component = "product_gallery", label = "Fotos del producto",
            images = listOf(CardImage("https://cdn.test/a.jpg"), CardImage("/api/dashboard/media/x.jpg"), CardImage("http://otro/b.jpg")),
            link = CardLink("Abrir", "javascript:alert(1)"),
        )
        val safe = card.reachable { path -> path?.takeIf { it.startsWith("/api/") }?.let { "https://api.test$it" } }

        org.junit.Assert.assertEquals(
            listOf("https://cdn.test/a.jpg", "https://api.test/api/dashboard/media/x.jpg"), safe.images.map { it.url },
        )
        org.junit.Assert.assertNull(safe.link)
    }

    @Test fun el_toque_del_cliente_se_lee_como_toque_no_como_codigo() {
        show(
            Message(
                key = "t1", author = Author.CUSTOMER, text = "[el cliente tocó el botón: Ver catálogo]",
                imageUrl = null, timestampMs = 1_791_924_100_000L, tap = "Ver catálogo",
            ),
        )

        compose.onNodeWithText("Tocó «Ver catálogo»").assertExists()
        compose.onNodeWithText("[el cliente tocó el botón", substring = true).assertDoesNotExist()
    }
}
