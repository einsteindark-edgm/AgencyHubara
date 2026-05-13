"""Cross-agent tools — lifted from individual agents to platform/.

Tools here are usable by ANY agent (sales_whatsapp, remarketing_whatsapp,
catalog_sync). They satisfy the exoclaw `Tool` Protocol via `ToolBase`. They
must remain agent-agnostic: do not import from `src.sales_whatsapp.*`,
`src.remarketing_whatsapp.*`, or `src.catalog_sync.*` here.
"""
