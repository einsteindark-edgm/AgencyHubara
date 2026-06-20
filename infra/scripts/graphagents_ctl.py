#!/usr/bin/env python3
"""
graphagents_ctl.py — prendé/apagá la caja GraphAgents on-demand (pay-per-use).

La caja vive APAGADA (solo pagás ~$3/mo de disco EBS). La prendés cuando vas a
correr un análisis de ads, y se auto-apaga sola al quedar idle (~20 min). Esto te
da el control manual + te imprime las URLs.

  python3 graphagents_ctl.py start    # prende, espera, imprime explorer/agentspan
  python3 graphagents_ctl.py stop     # apaga ahora (no esperes al auto-stop)
  python3 graphagents_ctl.py status   # estado + IP actual

Usa TUS credenciales AWS (aws configure). Cero deps de pip.
"""
import argparse
import json
import subprocess
import sys
import time

TAG = "Role=graphagents"


def aws(args, region, endpoint, capture=True):
    cmd = ["aws", "--region", region]
    if endpoint:
        cmd += ["--endpoint-url", endpoint]
    cmd += args
    res = subprocess.run(cmd, capture_output=capture, text=True)
    if res.returncode != 0:
        sys.exit(f"✗ {' '.join(cmd)}\n{res.stderr}")
    return res.stdout


def find_instance(region, endpoint):
    out = aws([
        "ec2", "describe-instances",
        "--filters", "Name=tag:Role,Values=graphagents",
        "Name=instance-state-name,Values=pending,running,stopping,stopped",
        "--query", "Reservations[].Instances[].[InstanceId,State.Name,PublicIpAddress]",
        "--output", "json",
    ], region, endpoint)
    rows = json.loads(out)
    if not rows:
        sys.exit("✗ no encontré la caja graphagents (¿aplicaste el root compute?)")
    iid, state, ip = rows[0]
    return iid, state, ip


def cmd_status(a):
    iid, state, ip = find_instance(a.region, a.endpoint_url)
    print(f"instance : {iid}")
    print(f"estado   : {state}")
    print(f"IP       : {ip or '(apagada)'}")


def cmd_start(a):
    iid, state, ip = find_instance(a.region, a.endpoint_url)
    if state != "running":
        print(f"Prendiendo {iid}… (estaba {state})", file=sys.stderr)
        aws(["ec2", "start-instances", "--instance-ids", iid], a.region, a.endpoint_url)
        print("Esperando que esté lista (status checks)…", file=sys.stderr)
        aws(["ec2", "wait", "instance-status-ok", "--instance-ids", iid], a.region, a.endpoint_url, capture=False)
        time.sleep(5)
    _, _, ip = find_instance(a.region, a.endpoint_url)
    print("✅ GraphAgents arriba:")
    print(f"   Explorer  : http://{ip}:8900   (solo desde tu IP / túnel SSH)")
    print(f"   AgentSpan : http://{ip}:6767")
    print("   Se auto-apaga sola al quedar idle. O: python3 graphagents_ctl.py stop")


def cmd_stop(a):
    iid, state, _ = find_instance(a.region, a.endpoint_url)
    if state == "stopped":
        print("Ya estaba apagada.")
        return
    print(f"Apagando {iid}…", file=sys.stderr)
    aws(["ec2", "stop-instances", "--instance-ids", iid], a.region, a.endpoint_url)
    print("✅ Apagada. Solo pagás el disco EBS hasta que la vuelvas a prender.")


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--region", default="us-east-1")
    common.add_argument("--endpoint-url", default="", help="http://localhost:4566 para robotocore")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("start", parents=[common]).set_defaults(func=cmd_start)
    sub.add_parser("stop", parents=[common]).set_defaults(func=cmd_stop)
    sub.add_parser("status", parents=[common]).set_defaults(func=cmd_status)
    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
