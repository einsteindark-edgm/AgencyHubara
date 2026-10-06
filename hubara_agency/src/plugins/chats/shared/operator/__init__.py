"""App Operador (Android): lo que decide Jev en la app, sobre las reglas de `mobile_rules`.

`decisions/` es el paquete de decisión `operador` (qué burbuja va primero,
cómo se clasifica un incendio) y `reading.py` arma las preguntas y aplica la
respuesta. PURO: la llamada a Jev, la caché y el registro viven en
`chats/api/mobile_jev.py`.
"""
