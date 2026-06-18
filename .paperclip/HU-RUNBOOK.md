# Cómo ejecutar una HU con Paperclip (guía paso a paso)

Para vos, que **no manejás Paperclip todavía**. El pod ya está creado; solo necesitás el
server (dashboard) corriendo.

## 0. Levantar el dashboard (si Paperclip no está corriendo)

El dashboard ES el server de Paperclip; vive en **http://localhost:3100**.

**¿Ya está vivo?** Abrí http://localhost:3100, o en terminal:
```bash
curl -s http://localhost:3100/api/health      # {"status":"ok",...} = ya está corriendo
```

**Si NO está corriendo, levantalo** (un comando, desde el clone):
```bash
cd ~/Documents/Projects/paperclip
pnpm paperclipai run        # onboard + doctor + sirve el dashboard en http://localhost:3100
```
Eso queda en primer plano (dejá la terminal abierta). Para que siga vivo aunque cierres la
terminal, corrélo en background:
```bash
cd ~/Documents/Projects/paperclip
nohup pnpm paperclipai run > ~/paperclip-server.log 2>&1 &
tail -f ~/paperclip-server.log              # (opcional) ver el arranque; Ctrl-C corta el tail, no el server
```
Notas: la **primera vez de todas**, si nunca instalaste deps, `pnpm install` antes.
Alternativa con hot-reload para desarrollo del propio Paperclip: `pnpm dev`.

**Verificá:** abrí **http://localhost:3100** → ahí está el board de **Acktos**. El health
dice `local_trusted` (sin login — solo loopback, por eso anda directo).

**Frenar el server:** `lsof -ti :3100 | xargs kill`  (o Ctrl-C en su terminal).

## El modelo en 3 frases

- **Acktos** es tu "empresa". Adentro hay un **proyecto "AgencyHubara"** y un **pod de 3
  agentes**: Architect (planifica + aprueba el merge), Implementer (programa con TDD vía
  hubara-dev), Reviewer (corre los gates + verifica, aprueba o rechaza).
- Una **HU = un issue**. Lo asignás al Implementer; Paperclip lo despacha solo, el agente
  programa en un worktree de AgencyHubara, y al terminar **no se auto-aprueba**: pasa a
  `in_review` y el Reviewer decide.
- El trabajo queda en la branch **`paperclip-hu`**; vos revisás y la mergeás a `main`.

## Tu setup actual (IDs reales — ya creado)

| Qué | ID / valor |
|---|---|
| Company (Acktos) | `b2520631-60d6-4c51-b3a8-f16c587e6e94` |
| Proyecto AgencyHubara | `f6ce058a-9b7f-4a48-b57b-a35f4aa906b8` |
| Architect | `d52b6518-7c23-458c-abeb-694944f8d17c` |
| Implementer | `a1ea9176-c41f-49cc-944e-0f05c0a6e018` |
| Reviewer | `4ed38c43-8f98-4520-ae90-31f4b45f1eca` |
| Worktree (cwd de los agentes) | `…/AgencyHubara/.claude/worktrees/paperclip-hu` (branch `paperclip-hu`) |

> Los 3 agentes están **activos**. Apenas creás un issue `todo` asignado a uno, el
> scheduler lo despacha (gasta tokens). Si querés pausar todo: ver "Frenar" abajo.

---

## Ejecutar una HU — 2 caminos

### Camino A — desde el BOARD (recomendado para vos, es a clicks)

1. En `http://localhost:3100` → entrá a **Acktos** → proyecto **AgencyHubara**.
2. Click **New Issue**. Completá:
   - **Title**: la HU en una línea (ej. *"Agregar columna 'última compra' a la tabla de clientes"*).
   - **Description**: el detalle / criterios de aceptación (qué tiene que pasar, observable).
   - **Assignee**: **AgencyHubara Implementer**.
   - **Reviewer** (campo de revisión / approval): **AgencyHubara Reviewer**  ← esto es lo que
     activa el no-self-review.
3. **Create**. Listo — Paperclip despacha al Implementer solo. Mirá la actividad del issue.

### Camino B — desde la TERMINAL (un solo bloque copy-paste)

Crea el issue **y** le pone la política no-self-review (review → Reviewer) en un paso:

```bash
cd ~/Documents/Projects/paperclip
ACK=b2520631-60d6-4c51-b3a8-f16c587e6e94
IMPL=a1ea9176-c41f-49cc-944e-0f05c0a6e018
REV=4ed38c43-8f98-4520-ae90-31f4b45f1eca
PROJ=f6ce058a-9b7f-4a48-b57b-a35f4aa906b8

# 1) crear el issue (cambiá title/description por tu HU)
ISSUE=$(pnpm paperclipai issue create -C $ACK \
  --title "TU HU ACÁ" \
  --description "Criterios de aceptación, observables." \
  --assignee-agent-id $IMPL --project-id $PROJ --status todo --json \
  | grep -oE '"id": *"[0-9a-f-]{36}"' | head -1 | grep -oE '[0-9a-f-]{36}')
echo "Issue: $ISSUE"

# 2) ponerle el gate no-self-review (review -> Reviewer). Es API (el CLI no tiene flag).
curl -sS -X PATCH "http://localhost:3100/api/issues/$ISSUE" \
  -H "Content-Type: application/json" \
  -d "{\"executionPolicy\":{\"mode\":\"normal\",\"commentRequired\":true,\"stages\":[{\"type\":\"review\",\"approvalsNeeded\":1,\"participants\":[{\"type\":\"agent\",\"agentId\":\"$REV\"}]}]}}" \
  -o /dev/null -w "policy: HTTP %{http_code}\n"
```

(Querés que el Architect lo refine/parta primero? Asigná el issue al **Architect**
(`d52b6518-…`) en vez del Implementer: él lo descompone en child-issues y los reparte.)

---

## Mirar el progreso

- **Board**: la tarjeta del issue se mueve sola **`todo → in_progress → in_review → done`**;
  hacé click para ver el log del agente en vivo + los comentarios.
- **Terminal**:
  ```bash
  pnpm paperclipai run live -C $ACK          # runs en curso (todo el company)
  pnpm paperclipai issue get <ISSUE>          # estado actual del issue
  ```
- ¿No arranca solo? Forzá un latido del Implementer (stream en vivo):
  ```bash
  pnpm paperclipai heartbeat run --agent-id $IMPL --source on_demand --trigger manual --debug
  ```

## Revisar y mergear a main

1. Cuando el issue llega a **`in_review`**, el **Reviewer** se despierta (o lo forzás con
   `heartbeat run --agent-id 4ed38c43-…`), corre `/hubara-gates` + verifica, comenta el
   veredicto y postea la decisión: `approved` → el issue pasa a `done`; `changes_requested`
   → vuelve al Implementer.
2. El agente abre **una branch + PR por HU** en GitHub (ej. ACK-2 → PR **#68**
   `fix/ack-2-system-explorer-build` → main). Revisás y mergeás el PR **en GitHub**:
   ```bash
   gh pr list --state open                       # el PR de la HU
   gh pr view <N> --web                           # revisar el diff
   gh pr merge <N> --squash --delete-branch       # mergear (o el botón Merge del PR)
   ```
3. **Sincronizá tu main local DESPUÉS de mergear** (clave — ver gotcha "Higiene de git"):
   ```bash
   cd /Users/edgm/Documents/Projects/AgencyHubara && git checkout main && git pull --rebase
   ```

## Cosas que tenés que saber (gotchas)

- **Auto-dispatch**: crear un issue `todo` asignado a un agente activo = arranca solo +
  gasta tokens (~$0.5–2 por run, modelo claude-opus-4-8, contra el budget del agente).
- **Frenar todo** (pausar el pod): `for id in d52b6518-… a1ea9176-… 4ed38c43-…; do pnpm paperclipai agent pause $id; done`.
  Reanudar: igual con `agent resume`.
- **Budget**: cada agente tiene tope mensual (Architect $1500 / Implementer $2500 /
  Reviewer $1000). Al 100% se auto-pausa. Ajustás con `pnpm paperclipai budget agent:update <id> --payload-json '{"budgetMonthlyCents":N}'`.
- **Deps en el worktree**: para HUs de código, el worktree `paperclip-hu` necesita deps
  (`cd hubara_agency && uv sync` / `cd frontend_dashboard && npm ci`) o los gates dan rojo
  por entorno. El agente puede instalarlas, o las dejás listas una vez.
- **Una HU a la vez** en este setup (worktree compartido). Para varias en paralelo con
  aislamiento por-HU, hace falta la estrategia `git_worktree` (avanzado) o el plugin bridge.
- **Higiene de git (la causa del "conflicto en github" de ACK-2).** El trabajo de la HU va
  a **origin/main** vía el PR del agente. Si tu `main` LOCAL tiene commits **sin pushear**
  (p.ej. cambios en `.paperclip/`), local y origin **divergen** y el push se rechaza —
  parece "conflicto" aunque no haya choque de líneas (fue exactamente esto: mi `.paperclip/`
  estaba commiteado en main local sin pushear mientras la HU mergeaba en origin). Reglas:
  (1) **pusheá** lo que commitees a main (no dejes config local sin pushear); (2) tras cada
  HU mergeada, `git pull --rebase` en main; (3) el pod basa su worktree en **origin/main**
  (`git fetch && git checkout --detach origin/main`), nunca en un main local desincronizado
  (CLAUDE.md gotcha #9).

## Cheat-sheet

```bash
cd ~/Documents/Projects/paperclip
ACK=b2520631-60d6-4c51-b3a8-f16c587e6e94
ARCH=d52b6518-7c23-458c-abeb-694944f8d17c
IMPL=a1ea9176-c41f-49cc-944e-0f05c0a6e018
REV=4ed38c43-8f98-4520-ae90-31f4b45f1eca
PROJ=f6ce058a-9b7f-4a48-b57b-a35f4aa906b8

pnpm paperclipai issue create -C $ACK --title "..." --assignee-agent-id $IMPL --project-id $PROJ --status todo --json   # crear HU
pnpm paperclipai run live -C $ACK                  # ver runs
pnpm paperclipai issue get <ISSUE>                 # estado
pnpm paperclipai heartbeat run --agent-id $IMPL --debug   # forzar latido
for id in $ARCH $IMPL $REV; do pnpm paperclipai agent pause $id; done   # frenar
```
