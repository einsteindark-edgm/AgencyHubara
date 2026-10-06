"""Laboratorio de conversaciones: almacén (S3 / disco) y lanzador de la caja.

Plan del laboratorio, §3.4 y §3.7. La API y el worker `sales_eval` de
producción exportan el banco, dejan la orden y prenden la caja; la caja corre
los bots y deja los resultados. Todo por S3 (datos) y SSM (órdenes), sin red
entre cajas. Los plugins lo usan por `src.sdk.labkit`.
"""
