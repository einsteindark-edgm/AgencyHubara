# Relleno del doble check para lo que salió antes del deploy

El PR #437 pone en el panel y en la App Operador lo que WhatsApp dijo de cada mensaje que enviamos:
✓ enviado, ✓✓ entregado, ✓✓ leído o «No llegó». Funciona con lo que sale **después** del deploy. Lo
de antes dice «sin confirmación»: el texto no guardó el id de WhatsApp de sus burbujas (`wamids`) y
lo que Meta informó quedó fuera de la conversación.

`hubara_agency/scripts/backfill_delivery_checks.py` une las dos cosas para lo viejo. Se corre una
sola vez, en la caja. Puedes repetirlo sin riesgo: lo que ya está no se vuelve a escribir.

## De dónde saca los datos (solo de Meta)

| Qué | De dónde |
|---|---|
| Los ids de las burbujas de un texto del bot | Las trazas de turno v2 (`<conversación>/evals/turn_traces.jsonl`), unidas al mensaje por su texto y su hora (±2 min). |
| Los ids de las burbujas de un texto del operador (o del bot sin traza) | `metadata.outbound_text_index` (las últimas 60 burbujas). Solo si el texto aparece una vez en el chat y una vez en el índice. |
| Tarjetas, plantillas, fotos y documentos | Ya traían su id. No se tocan. |
| Lo que dijo WhatsApp | `_orphan_delivery_statuses.jsonl` (los estados que no encontraron su mensaje, como los del 15-oct) y los `delivery_status` de `_analytics/<día>.jsonl` (también los que llegaron con el episodio abierto). |

Escribe:

- `<conversación>/delivery_statuses.jsonl`: una línea por estado, con el mismo formato que el
  webhook en vivo, **sin hora** (`"at_ms": null`). Ni Meta ni esos registros guardaron la hora del
  estado, y no se inventa: el panel dice «Leído por WhatsApp», sin la hora.
- `<conversación>/sessions/<conversación>.jsonl`: agrega `wamids` a los textos viejos que pudo unir.
  Antes deja una copia: `<conversación>.jsonl.bak-delivery-backfill`. La escritura es atómica y no
  pierde lo que el bot o el cliente escriban mientras tanto.

No toca `metadata.json`, ni los costos, ni las conversaciones de prueba (`wa_golden_*`, etc.).

## Antes de empezar

1. El PR #437 tiene que estar desplegado (merge a `main` = deploy). Sin él, el script falla al
   arrancar con `ModuleNotFoundError: … delivery_status` y no escribe nada.
2. Ten a mano el número de las tres conversaciones de la noche del 15-oct (caída de DeepSeek). El
   informe las muestra así: `···1234` (los últimos 4 dígitos).

## Paso a paso

Siempre primero sin `--apply`: solo dice qué haría.

### 1. Las tres conversaciones del 15-oct

Desde la raíz del repo en tu equipo, con el ayudante de SSM (copia el script al contenedor del API y lo corre ahí; no
necesita que este PR esté desplegado):

```bash
hubara_agency/scripts/ssm_run_in_api.sh hubara_agency/scripts/backfill_delivery_checks.py --vault /app/hubara_vault --session wa_<número-1> --session wa_<número-2> --session wa_<número-3>
```

Mira el informe (abajo dice cómo leerlo). Si está bien, lo mismo con `--apply`:

```bash
hubara_agency/scripts/ssm_run_in_api.sh hubara_agency/scripts/backfill_delivery_checks.py --vault /app/hubara_vault --session wa_<número-1> --session wa_<número-2> --session wa_<número-3> --apply
```

### 2. Revisar en el panel y en la App

Abre las tres conversaciones. Los mensajes de esa noche muestran ✓, ✓✓ o leído, sin hora. La App
toma el cambio al abrir el chat (vuelve a pedir el historial). Lo que siga en «sin confirmación» es
lo que el script no pudo unir con certeza (`unconfirmed_after` en el informe).

### 3. El resto del vault

El ayudante de SSM espera solo un minuto. Para todo el vault, entra a la caja (con este PR
desplegado, el script ya está en la imagen):

```bash
ssh -i ~/.ssh/hubara_ops ec2-user@<ip de la caja>
```

```bash
sudo docker exec -w /app/hubara_agency hubara-prod-api-1 python -m scripts.backfill_delivery_checks --vault /app/hubara_vault
```

Y si el informe está bien:

```bash
sudo docker exec -w /app/hubara_agency hubara-prod-api-1 python -m scripts.backfill_delivery_checks --vault /app/hubara_vault --apply
```

Una segunda corrida con `--apply` debe dar `text_events_linked: 0` y `status_rows: 0`.

## Cómo leer el informe

Una línea JSON, solo con conteos. No trae teléfonos ni textos.

| Campo | Qué es |
|---|---|
| `mode` | `dry_run` (no escribió nada) o `apply`. |
| `sessions_scanned` | Conversaciones de clientes revisadas. |
| `sessions_not_found` | `--session` que no existen en el vault (revisa el número). |
| `text_events_linked` | Textos viejos a los que les pone (o puso) sus `wamids`. |
| `status_rows` | Estados de WhatsApp nuevos para los chats. |
| `statuses_without_message` | Estados que no son de ningún mensaje de las conversaciones revisadas. Con `--session` incluye los de todas las demás: es normal que sea alto. |
| `sessions.···1234.outbound` | Mensajes salientes de esa conversación. |
| `sessions.···1234.unconfirmed_after` | Cuántos seguirán en «sin confirmación» después del relleno. |

## Lo que no puede hacer

- **La hora del estado.** Los registros viejos no la guardaron: el check sale sin hora.
- **El motivo de un «No llegó».** Los registros viejos tampoco guardaron el código de Meta.
- **Textos sin rastro.** Un texto del bot anterior a las trazas v2, o un texto repetido que ya salió
  del índice, queda «sin confirmación». Mejor eso que un check de otro mensaje.

## Si algo sale mal

- **Volver atrás los estados de una conversación.** Las líneas del relleno son las de
  `"at_ms": null` (el webhook en vivo siempre trae la hora):

  ```bash
  sudo docker exec hubara-prod-api-1 sed -i '/"at_ms": null/d' /app/hubara_vault/wa_<número>/delivery_statuses.jsonl
  ```

- **Volver atrás el historial.** La copia `…/sessions/wa_<número>.jsonl.bak-delivery-backfill` es el
  historial antes de la primera corrida. Copiarla encima borra lo que haya entrado después: hazlo
  solo justo después del relleno, o pásale a mano los mensajes nuevos.
- Cuando confirmes que todo se ve bien, puedes borrar las copias `*.bak-delivery-backfill`.
