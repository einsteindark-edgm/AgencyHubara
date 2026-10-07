"""App Operador (Android): lo que decide Jev en la app, sobre las reglas de `mobile_rules`.

`decisions/` es el paquete de decisión `operador` (qué burbuja va primero,
cómo se clasifica un incendio): el YAML certificado y sus builtins, PUROS. Lo
corre el motor de decisiones oficial desde la API móvil
(`chats/api/mobile_decisions.py`: `BundledCapability` + `decide_for_session`).
"""
