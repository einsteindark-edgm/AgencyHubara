import asyncio
import json
import time
from pathlib import Path

from exoclaw.agent.tools.registry import ToolRegistry
from temporalio.client import Client

from exoclaw_temporal.config import LLMConfig, TurnInput, WorkspaceConfig
from exoclaw_temporal.turn_based.workflows.agent_turn import AgentTurnWorkflow


async def main():
    # 1. Conexión al motor de Temporal
    client = await Client.connect("localhost:7233")
    session_id = f"sales-test-{int(time.time())}"

    print("🚀 Configurando cerebro y memoria para Hubara...")

    # 2. Instanciamos el Cerebro (LLMConfig)
    # Usamos los nombres de campos exactos que encontramos
    llm_conf = LLMConfig(
        model="openai/deepseek-v4-pro",
        api_base="http://localhost:4000",
        api_key="sk-1234",
        temperature=0.1,
        max_tokens=4096  # Ajustado para respuestas rápidas
    )

    # 3. Instanciamos la Memoria (WorkspaceConfig) y Registramos Tools
    vault_path = Path(f"./hubara_vault/{session_id}")
    vault_path.mkdir(parents=True, exist_ok=True)

    workspace_conf = WorkspaceConfig(
        path=str(vault_path)
    )

    # Requerido por la arquitectura para serializar sin objetos vivos
    registry = ToolRegistry()
    tools_json = json.dumps(registry.get_definitions())

    # 4. Empaquetamos todo en el TurnInput asegurando serialización estricta
    workflow_input = TurnInput(
        session_id=session_id,
        message="Hola, ¿qué opciones de velas tienes disponibles?",
        channel="cli-test",
        chat_id=session_id,
        llm=llm_conf,
        workspace=workspace_conf,
        tool_definitions_json=tools_json
    )

    print("🎬 Iniciando Workflow en Temporal...")

    try:
        handle = await client.start_workflow(
            AgentTurnWorkflow.run,
            args=[workflow_input],
            id=f"workflow-{session_id}",
            task_queue="exoclaw-turn-based",
        )

        print("✅ Workflow en ejecución. Esperando respuesta del agente...")

        # Obtenemos el resultado final de la ejecución
        result = await handle.result()
        print("\n🤖 RESPUESTA DEL AGENTE:")
        print("-" * 30)
        print(result)

    except Exception as e:
        print(f"❌ Error durante el vuelo: {e}")

if __name__ == "__main__":
    asyncio.run(main())
