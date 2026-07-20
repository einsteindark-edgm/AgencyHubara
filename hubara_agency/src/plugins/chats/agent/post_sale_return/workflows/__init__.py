"""Workflows del scheduler post-venta.

Package (no módulo suelto): el gate
`test_manifest_orchestration_consistency` AST-escanea
`agent/<worker>/workflows/*.py` buscando los `@workflow.defn` declarados en
`workflow_classes:` del manifest.
"""
from src.plugins.chats.agent.post_sale_return.workflows.post_sale_return import (
    PostSaleReturnWorkflow as PostSaleReturnWorkflow,
)
