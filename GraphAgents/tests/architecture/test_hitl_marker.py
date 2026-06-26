"""El marcador `human_task` del SDK — HITL agnóstico del runtime.

Marca un nodo del grafo como HUMAN task seteando los atributos que el serializer
de AgentSpan lee (`_agentspan_human_task`): al compilar en AgentSpan el nodo se
vuelve una HUMAN task de Conductor (pausa durable). OFFLINE es un marcador INERTE
— el body del nodo (con `interrupt()`) sigue corriendo normal. Así el MISMO nodo
sirve a ambos runtimes sin que la forma offline dependa de agentspan.

Validado en vivo: sin el marcador, AgentSpan corre el nodo como SIMPLE task y el
`interrupt()` revienta ('Called get_config outside of a runnable context').
"""
from sdk.hitl import human_task


def test_human_task_con_prompt_marca_el_nodo() -> None:
    @human_task(prompt="¿Aprobar?")
    def node(state):
        return {}

    assert node._agentspan_human_task is True
    assert node._agentspan_human_prompt == "¿Aprobar?"


def test_human_task_sin_args() -> None:
    @human_task
    def node(state):
        return {}

    assert node._agentspan_human_task is True
    assert node._agentspan_human_prompt == ""


def test_human_task_no_reemplaza_el_body_offline() -> None:
    # OFFLINE el decorador es inerte: el body sigue corriendo (es lo que da el
    # interrupt() en el golden). AgentSpan es el que NO corre el body (HUMAN task).
    @human_task(prompt="x")
    def node(state):
        return {"ran": True}

    assert node({"k": 1}) == {"ran": True}
