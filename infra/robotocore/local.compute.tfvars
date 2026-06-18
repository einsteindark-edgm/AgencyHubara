# Apunta el root compute/ a robotocore. El AMI data source no resuelve en el
# emulador → pasamos un id dummy. El cloud-init no ejecuta (no hay VM real); lo
# que se valida es el GRAFO (instancias, SGs, EIPs, instance profiles, políticas).
aws_endpoint = "http://localhost:4566"
region       = "us-east-1"
ami_id       = "ami-12c6146b" # AMI canned de robotocore/Moto (en real = data source AL2023)
# Sin key pair en local: el formato OpenSSH lo valida el emulador y no aporta al test.
ssh_public_key = ""
