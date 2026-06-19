"""ConnectorKit — ports a fuentes externas (Meta Marketing API, vault CTWA) con
vendor swappable (live / fixture / warehouse). Una capability NUNCA habla con la
red cruda: declara `consumes: [<port>]` y recibe el vendor inyectado (G-PORT).
"""
