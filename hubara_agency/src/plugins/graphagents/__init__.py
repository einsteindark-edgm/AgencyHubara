"""Plugin `graphagents` — el buzón del bridge async dashboard→GraphAgents.

Dispara runs de análisis en el subsistema externo GraphAgents (AgentSpan/Conductor)
por SSM, pollea su progreso y lo relaya al dashboard por SSE, y reenvía las
decisiones HITL. El agente vive ENTERO en GraphAgents; este plugin es solo el
transporte (ver `ARCHITECTURE_FINAL_fable.md` y el contrato de eventos).
"""
