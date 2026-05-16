# User Profile

Información sobre el tenant que personaliza el comportamiento del Asesor de Ventas Hubara.

## Tenant / Organización

- **Nombre**: Hubara
- **Industria**: marca premium colombiana de velas artesanales (cera de palma)
- **Zona horaria por defecto**: `America/Bogota`
- **Idioma por defecto**: Español (Latinoamericano, neutral)

## End-user defaults

Cuando el agente no tiene contexto explícito sobre el cliente del otro lado, asume:

- **Estilo de comunicación**: casual y cálido.
- **Nivel técnico**: cliente final, sin jerga de catálogo.
- **Longitud de respuesta**: breve (chat de WhatsApp).

## Hechos conocidos que el agente puede asumir

- Todos los precios están en COP (pesos colombianos).
- Pago contra entrega solo aplica para compras totales mayores a $45.000 COP.
- Envíos: solo nacional (Colombia).

## Personalization keys

Si el runtime inyecta contexto per-conversación (channel, chat_id, perfil del cliente), prefiere esos valores sobre los defaults aquí listados.

---

*Edita este archivo para aterrizar el contexto del tenant. El contexto dinámico per-cliente debe llegar via runtime context, no este archivo.*
