"""Lógica agéntica del plugin `chats`.

Subpaquetes:
- ``sales/``: HubaraSalesSessionWorkflow + tools + activities + use_cases
  + state + contracts + prompts + parsers + workspace canónico.
- ``remarketing/``: RemarketingSessionWorkflow + activities + contracts
  + prompts + workspace canónico.

PR3 expondrá desde aquí ``WORKFLOWS``, ``ACTIVITIES``, ``TOOL_FACTORIES``
para que el meta-launcher los descubra. Hoy (PR2), cada worker en
``workers/<sub>.py`` los importa directamente y registra a mano.
"""
