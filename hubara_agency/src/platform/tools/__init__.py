"""Cross-agent tools — lifted from individual agents to platform/.

Tools here are usable by ANY agent (chats sales, chats remarketing,
catalog_sync, …). They satisfy the exoclaw `Tool` Protocol via `ToolBase`.
They must remain agent-agnostic: do not import from
`src.plugins.chats.agent.*`, `src.plugins.catalog.agent.*`, or any other
plugin's agent code here.
"""
