# Control del bot nuevo: solo por comando

Desde el 2026-10-06 el bot nuevo de producción se cambia **solo por comando**.
Es una decisión del operador: «que los botones de la UI no sirvan y todo se
haga por comandos, para evitar que alguien jugando dañe producción».

- El panel «Bot nuevo» y el de «Motor de decisiones» (Agents) solo muestran
  qué corre, qué falta para subir y el comando.
- Los `PUT /api/chats/perception/{rollout,capabilities,workflow}` responden
  403 `by_command`, y el pase de Agents ya no los tiene.
- Código: `hubara_agency/src/plugins/chats/agent/sales/decisions/control.py`.

## Cómo se corre

Desde una máquina del equipo con credenciales de AWS. El script lo corre
dentro del contenedor de la API por SSM:

```bash
infra/scripts/bot_control.sh estado
```

Para cambiar algo, el comando lleva `--por <quien>`, que queda firmado como
`comando:<quien>`:

| Qué | Comando |
|---|---|
| Ver qué corre | `estado` |
| Agregar o quitar un número de prueba | `--por ana numeros agregar wa_57…` / `quitar` |
| Porcentaje del canary | `--por ana porcentaje 10` |
| Percepción (capas del turno con Jev) | `--por ana percepcion off\|shadow\|canary\|on` |
| Una capacidad (o `todas`) | `--por ana capacidad baja shadow` |
| Versión del workflow de ventas | `--por ana workflow off\|canary\|on` |
| Los números de prueba deciden con Jev | `--por ana prueba-jev si\|no` |

Dentro de la caja es lo mismo, sin el script:

```bash
docker compose exec -w /app/hubara_agency api python -m src.plugins.chats.agent.sales.decisions.control estado
```

## Las garantías (las mismas que tenían los PUT)

- **Techos de Terraform.** Nada pasa los techos (`SALES_PERCEPTION_MODE_CEILING`,
  `SALES_CAPABILITIES_CEILING`, `SALES_WORKFLOW_V2_CEILING`).
- **Apagar y bajar siempre pasan.** Es el interruptor de emergencia.
- **Subir exige la vara.** Sin ella el comando no aplica nada, termina con
  código 2 y lista qué falta. Una subida lenta no pisa un apagado que llegó
  mientras tanto.
- **Todo queda firmado y en el log.** Los eventos son
  `perception.rollout_changed`, `decisions.capability_changed`,
  `decisions.workflow_changed` y `decisions.test_numbers_jev_changed`.

## Los números de prueba deciden con Jev

Con `prueba-jev si`, los números de prueba corren todas las capacidades y las
capas con Jev, en modo canary y dentro de los techos, **sin esperar la vara**.
Nadie más cambia: los clientes siguen con su modo y no les llega la espera de
la sombra de las capacidades.

Para prenderlo hace falta:
- al menos un número de prueba;
- el techo de las capacidades en `canary` o más;
- la llave de Jev cargada.

Apagarlo siempre pasa. Para que esos números corran además el workflow V2,
`workflow canary`.
