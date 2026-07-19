# Forge — clonar el motor a un cliente nuevo

Forge vive en **su propio ícono 🔥 "Forge"** en la barra de actividad (la columna
de íconos a la izquierda), **separado** de Acktos Studio a propósito: migrar un
cliente es otro concepto que desarrollar agentes o plugins.

Si no ves el ícono 🔥, está en el menú de desbordamiento **"…"** al final de la
barra de actividad (click derecho sobre la barra → activá **Forge**).

## Qué hace

- **Flota de clientes** — un panel con cada cliente (`forge/clients/<slug>/`), su
  estado de redacción y qué falta para forjar.
- **Forge Console** — `Cmd/Ctrl+Shift+P` → **"Forge: Abrir consola"**: cards por
  cliente, wizard de cliente nuevo, y el stream en vivo de cada paso del CLI.

## La regla de oro

La UI es piel; los CLIs son músculo. Cada botón ejecuta `python3 forge/forge.py …`
o un step de `forge/migrate.py`. Nada de lo que corras acá puede tocar la infra
de Hubara — está garantizado por construcción (guards anti-hubara + los pasos que
tocan AWS solo imprimen comandos apuntando al clon).
