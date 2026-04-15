# 🔥 CheatSheet Hubara: Operaciones del Proyecto Asíncrono

Guarda esta guía sagrada. Aquí tienes el paso a paso corporativo para controlar tu infraestructura completa (Docker, Base de Datos, UV Workspace y Workers).

## Fase 1: Control de la Infraestructura en la Nube (Docker)

El `docker-compose.yml` sigue existiendo adentro de la carpeta reservada del framework original (`exoclaw-temporal`). Desde la raíz principal de tu proyecto (`/AgencyAgentic`), controlas Docker así:

### Para TUMBAR y Limpiar Todo
Si quieres destruir el clúster por completo ("Apagarlo y borrar la base de datos de Temporal" para matar Workflows fantasmas antiguos):
```bash
docker compose -f exoclaw-temporal/docker-compose.yml down -v
```
*(El `-v` es la magia que destruye el volumen de Postgres para que nazca limpio).*

### Para LEVANTAR Todo (Temporal + UI + LiteLLM)
```bash
docker compose -f exoclaw-temporal/docker-compose.yml up -d
```
> **Tip:** Accede a la interfaz visual de Temporal en http://localhost:8233 para ver a tu Agente operando en vivo.

---

## Fase 2: Control del "Cerebro" de la Agencia (Python / UV)

La regla de oro de la Nueva Arquitectura: **Jamás lanzes comandos `.py` fuera de tu carpeta de agencia.** Toda tu terminal debe vivir aquí:
```bash
cd hubara_agency/
```

### 1. Sincroniza la Matriz
Esto te asegura de que si tocaste algo en las librerías, UV se asegure de inyectarlo en milisegundos:
```bash
uv sync
```

### 2. Arranca a tu Interfaz Pública (FastAPI)
Terminal 1: Tu API Web publicable. Escuchará todas las peticiones POST de Meta/WhatsApp.
```bash
uv run python run_api.py
```

### 3. Arranca el Músculo (Worker de Ventas)
Terminal 2: Tu Agente Especialista. Se conectará a Temporal e interceptará LiteLLM.
```bash
uv run python -m src.domains.sales_whatsapp.worker
```

### OPCIONAL: Purga de Memoria Local
Si tumbaste a Temporal con Docker y necesitas que el Agente "olvide los archivos físicos" que generó en tu Mac, ejecuta esto para limpiar la bóveda antes de lanzar una prueba:
```bash
rm -rf hubara_vault/*
```

---

## Fase 3: Pruebas (Hello World Rápido)
Terminal 3: Tu cliente infiltrado simulando ser un celular real escribiendo desde Meta.
```bash
uv run python -m src.tests.simulate_whatsapp
```
