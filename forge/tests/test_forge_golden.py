"""Golden test de forge: forjar un cliente fake `acme` desde un mini-repo sintético.

El fixture replica los paths y strings REALES del repo madre (tabla §3 de
VINCENZO_SPLIT_PLAN.md). Si el manifest pierde una regla o una regla corrompe
un archivo, este test lo ve. Corre con `python3 -m pytest forge/tests -q`
(sin uv, igual que GraphAgents).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

FORGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FORGE_DIR))

import forge  # noqa: E402


# ── Fixture: mini-repo madre con los patrones reales ─────────────────────────

SALES_WS = "hubara_agency/src/plugins/chats/agent/sales/workspace"
RMKT_WS = "hubara_agency/src/plugins/chats/agent/remarketing/workspace"

FIXTURE_FILES = {
    "infra/terraform/platform/tenants.auto.tfvars": (
        'tenants = {\n  hubara = {\n    api_url = "https://98-88-237-207.sslip.io"\n'
        '    callback_urls = ["https://d1hvhzkh01tri0.cloudfront.net/auth/callback"]\n  }\n'
        '  vincenzo = {\n    api_url = "https://api.vincenzo.example"\n  }\n}\n'
    ),
    "infra/terraform/platform/variables.tf": (
        'variable "github_repo" {\n  default = "einsteindark-edgm/AgencyHubara"\n}\n'
    ),
    "infra/terraform/compute/tenants.auto.tfvars": (
        'ssh_public_key = "ssh-ed25519 AAAA hubara-ops"\nami_id = "ami-07ab13a91f7d7a8af"\n'
        'tenants = {\n  hubara = {\n    instance_type = "t3.medium"\n'
        '    domain = "98-88-237-207.sslip.io"\n'
        '    enabled_plugins = "ads,agents_admin,catalog,chats,eta,order_sentinel,orders,reengagement,system_map"\n'
        "  }\n}\n"
    ),
    "infra/terraform/compute/backup.tf": (
        'resource "aws_iam_role" "dlm" {\n  name = "agencyhubara-dlm-backup"\n}\n'
        'resource "aws_dlm_lifecycle_policy" "daily_backup" {\n'
        '  description = "AgencyHubara - snapshot diario"\n'
        '  policy_details {\n    target_tags = { Backup = "daily" }\n'
        '    tags_to_add = { SnapshotCreator = "dlm-agencyhubara" }\n  }\n}\n'
        "# restore: /var/lib/docker/volumes/hubara-prod_hubara-vault\n"
    ),
    "infra/terraform/compute/modules/app-instance/main.tf": (
        'resource "aws_security_group" "app" {\n  name = "agencyhubara-${var.tenant}-app"\n}\n'
        'data "aws_iam_policy_document" "ssm_read" {\n'
        '  statement {\n    resources = [\n'
        '      "arn:aws:ssm:*:*:parameter/hubara/${var.tenant}",\n'
        '      "arn:aws:ssm:*:*:parameter/hubara/${var.tenant}/*",\n    ]\n  }\n}\n'
        'resource "aws_instance" "app" {\n'
        '  volume_tags = {\n    Name   = "agencyhubara-${var.tenant}-app-root"\n'
        '    Backup = "daily"\n  }\n}\n'
    ),
    "infra/terraform/compute/modules/app-instance/cloud-init.yaml.tftpl": (
        "write_files:\n  - path: /opt/hubara/box.env\nruncmd:\n  - mkdir -p /opt/hubara\n"
    ),
    # ── GraphAgents: viaja al clon con tag/SSM/imagen PROPIOS (colisión de
    # cuenta: tag Role=graphagents + /graphagents/* + imagen GHCR son únicos) ──
    "infra/terraform/compute/modules/graphagents-instance/main.tf": (
        'resource "aws_instance" "graphagents" {\n'
        '  tags = {\n    Role = "graphagents"\n  }\n}\n'
        'data "aws_iam_policy_document" "self_stop" {\n'
        '  statement {\n    condition {\n      values   = ["graphagents"]\n    }\n'
        '    resources = [\n'
        '      "arn:aws:ssm:*:*:parameter/graphagents",\n'
        '      "arn:aws:ssm:*:*:parameter/graphagents/*",\n    ]\n  }\n}\n'
    ),
    "infra/terraform/platform/modules/graphagents-secrets/main.tf": (
        'locals {\n  prefix = "/graphagents"\n}\n'
    ),
    "infra/terraform/compute/variables.tf": (
        'variable "graphagents" {\n'
        '  # imagen de la app (≠ la de la app principal)\n'
        '  image_repo = optional(string, "ghcr.io/einsteindark-edgm/graphagents")\n'
        "}\n"
    ),
    ".github/workflows/graphagents-deploy.yml": (
        "jobs:\n  build:\n    steps:\n      - run: |\n"
        '          echo "image=ghcr.io/${OWNER}/graphagents:${{ github.sha }}"\n'
        "  deploy:\n    steps:\n      - run: |\n"
        '          --filters "Name=tag:Role,Values=graphagents"\n'
        "          sudo mkdir -p /opt/graphagents\n"
        "          cp /tmp/ga-deploy/infra/compose/graphagents/docker-compose.prod.yml /opt/graphagents/docker-compose.yml\n"
    ),
    "infra/compose/graphagents/render-env-from-ssm.sh": (
        "#!/usr/bin/env bash\n"
        "# instance profile con ssm:GetParametersByPath sobre /graphagents/*\n"
        "BOX_ENV=/opt/graphagents/box.env\n"
        'aws ssm get-parameters-by-path --path "/graphagents" --with-decryption\n'
    ),
    "infra/scripts/graphagents_ctl.py": (
        'TAG = "Role=graphagents"\n'
        'FILTERS = ["Name=tag:Role,Values=graphagents"]\n'
    ),
    "hubara_agency/src/platform/config.py": (
        'GRAPHAGENTS_INSTANCE_TAG = os.getenv("GRAPHAGENTS_INSTANCE_TAG", "graphagents")\n'
    ),
    "infra/compose/render-env-from-ssm.sh": (
        "#!/usr/bin/env bash\nsource /opt/hubara/box.env\n"
        'aws ssm get-parameters-by-path --path "/hubara/${TENANT}" --with-decryption\n'
        "echo WORKSPACE_VAULT_DIR=/app/hubara_vault >> /opt/hubara/.env\n"
    ),
    "infra/compose/docker-compose.prod.yml": (
        "name: hubara-prod\nservices:\n  api:\n    image: ${HUBARA_IMAGE}\n"
        "    volumes:\n      - hubara-vault:/app/hubara_vault\n"
        "  worker-order-sentinel-cycle:\n    environment:\n"
        "      HUBARA_API_BASE_URL: http://api:8000\n"
        "volumes:\n  hubara-vault:\n"
    ),
    ".github/workflows/backend-deploy.yml": (
        "on:\n  push:\n    paths:\n      - \"hubara_agency/**\"\njobs:\n  build:\n"
        "    steps:\n      - run: |\n"
        '          echo "image=ghcr.io/${OWNER}/agencyhubara:${{ github.sha }}"\n'
        "  deploy:\n    strategy:\n      matrix:\n"
        "        tenant: [hubara]   # sumá vincenzo cuando tenga su caja + secretos\n"
        "    steps:\n      - run: |\n"
        '          target: "/tmp/hubara-deploy"\n'
        "          sudo mkdir -p /opt/hubara\n"
        "          cp /tmp/hubara-deploy/infra/compose/docker-compose.prod.yml /opt/hubara/docker-compose.yml\n"
    ),
    ".github/workflows/frontend-deploy.yml": (
        "jobs:\n  deploy:\n    strategy:\n      matrix:\n        tenant: [hubara, vincenzo]\n"
    ),
    "infra/scripts/aws_bootstrap.py": (
        'STATE_BUCKET = "agencyhubara-tfstate"\nLOCK_TABLE = "agencyhubara-tflock"\n'
        'DEFAULT_REPO = "einsteindark-edgm/AgencyHubara"\nDEFAULT_PREFIX = "/hubara"\n'
        'VERIFY_PARAM = "/hubara/hubara/scheduler/ORDER_RECONCILE_INTERVAL_MINUTES"\n'
    ),
    "infra/robotocore/test-local.sh": (
        "aws cognito-idp list-user-pools | grep agencyhubara-hubara\n"
        "aws ssm get-parameter --name /hubara/hubara/scheduler/X\n"
        "aws s3 ls s3://agencyhubara-hubara-frontend\n"
    ),
    "infra/whatsapp-provisioning/whatsapp_provision.py": (
        'tenant = (cfg.get("TENANT") or "hubara").strip() or "hubara"\n'
        'print(f"aws ssm put-parameter --name /hubara/{tenant}/meta/oauth ...")\n'
    ),
    "infra/whatsapp-provisioning/README.md": (
        "# Provisioning\npython3 whatsapp_provision.py plan --config tenants/hubara.env\n"
        "Hubara usa este toolkit para su WABA.\n"
    ),
    "infra/whatsapp-provisioning/tenants/hubara.env.example": (
        "BUSINESS_ID=1873134773439557\nWABA_ID=3018148735027036\n"
        "CATALOG_ID=868785339159351\nCALLBACK_URL=https://98-88-237-207.sslip.io/api/chats/webhook\n"
    ),
    "infra/whatsapp-provisioning/definitions/flows.json": (
        '{"name": "Hubara — Datos de envío v1", '
        '"json": "hubara_agency/docs/whatsapp_flows/shipping_v1.json"}\n'
    ),
    "infra/INFRASTRUCTURE.md": "# Infra de Hubara\nEIP 98.88.237.207, cuenta 525237381234.\n",
    "hubara_agency/hubara_vault/wa_573001234567/metadata.json": '{"phone": "wa_573001234567"}\n',
    "hubara_agency/scripts/inject_snapshot_products.py": (
        'PRODUCTS_TO_INJECT = [{"meta_id": "868785339159351"}]\n'
    ),
    "hubara_agency/src/plugins/chats/agent/sales/tools/ui_intents.py": (
        'reference_id = f"HUB-hubara-{ctx.session_key}-{int(time.time())}"\n'
        'ALLOWED = ["https://hubara.com.co/", "https://www.hubara.com.co/"]\n'
    ),
    "hubara_agency/src/plugins/chats/agent/sales/tools/skills.py": (
        'CATALOG_SKILL = "hubara_catalog"\n'
    ),
    # Nombre de workflow del MOTOR referenciado también FUERA del scope de la
    # regla marca-en-agente (dispatcher, workers, tests): renombrarlo a medias
    # rompe el dispatch del clon — debe PRESERVARSE aunque contenga "Hubara".
    "hubara_agency/src/plugins/chats/agent/sales/workflows/sales_session.py": (
        '@workflow.defn(name="HubaraSalesSessionWorkflow")\n'
        'class SalesSession:\n'
        '    GREETING = "Bienvenido a Hubara"\n'
    ),
    "hubara_agency/src/platform/temporal/dispatcher.py": (
        'WORKFLOW = "HubaraSalesSessionWorkflow"  # fuera del scope de marca\n'
    ),
    f"{SALES_WS}/IDENTITY.md": "Eres el Asesor Exclusivo de Ventas de Hubara.\n",
    f"{SALES_WS}/SOUL.md": "# Soul — Hubara\nUsá el skill hubara_catalog.\n",
    f"{SALES_WS}/TOOLS.md": "# Tools\nhubara_catalog: catálogo.\n",
    f"{SALES_WS}/memory/MEMORY.md": "# Memoria del asesor Hubara\n",
    f"{SALES_WS}/skills/etapa_descubrimiento/SKILL.md": "Bienvenido a Hubara, velas artesanales.\n",
    f"{SALES_WS}/skills/sales_script/SKILL.md": "# Guion Hubara\n",
    f"{SALES_WS}/skills/hubara_catalog/SKILL.md": "# Catálogo Hubara\n",
    f"{RMKT_WS}/IDENTITY.md": "Asesor de remarketing de Hubara.\n",
    f"{RMKT_WS}/SOUL.md": "# Soul remarketing Hubara\n",
    f"{RMKT_WS}/TOOLS.md": "# Tools remarketing\n",
    f"{RMKT_WS}/memory/MEMORY.md": "# Memoria remarketing\n",
    f"{RMKT_WS}/skills/hubara_catalog/SKILL.md": "# Catálogo Hubara (remarketing)\n",
    "docs/cartagena/plan.md": "# Vertical hotelero de otro cliente\n",
    "MULTI_TENANT_COMMERCE_ARCHITECTURE.md": "# Diseño viejo\n",
    "VINCENZO_SPLIT_PLAN.md": "# Plan del split\n",
    "CLAUDE.md": "# Engine\nEl backend vive en hubara_agency/. Ver hubara-dev harness.\n",
    "forge/forge.py": "# forge no viaja al clon\n",
}


@pytest.fixture()
def mini_repo(tmp_path: Path) -> Path:
    src = tmp_path / "madre"
    for rel, content in FIXTURE_FILES.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=src, check=True)
    subprocess.run(["git", "add", "-A"], cwd=src, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "genesis"],
        cwd=src,
        check=True,
    )
    return src


ACME_CLIENT = {
    "slug": "acme",
    "company": "Acme",
    "repo": "einsteindark-edgm/AgencyAcme",
    "aws": {"region": "us-east-1", "resource_prefix": "agencyacme", "ssm_prefix": "/acme"},
    "business": {
        "country": "CO",
        "currency": "COP",
        "product_description": "cafés de origen",
        "domains": ["acme.example.com"],
        "instagram": "",
    },
}


def _overlay_file(bundle: Path, rel: str, text: str) -> None:
    p = bundle / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture()
def acme_bundle(tmp_path: Path) -> Path:
    """Bundle clients/acme/ con client.yaml + overlay de workspace completo."""
    import yaml

    bundle = tmp_path / "clients" / "acme"
    bundle.mkdir(parents=True)
    (bundle / "client.yaml").write_text(yaml.safe_dump(ACME_CLIENT), encoding="utf-8")
    for agent in ("sales", "remarketing"):
        _overlay_file(bundle, f"workspace/{agent}/IDENTITY.md", f"Asesor {agent} de Acme.\n")
        _overlay_file(bundle, f"workspace/{agent}/SOUL.md", f"# Soul {agent} — Acme, cafés de origen\n")
        _overlay_file(bundle, f"workspace/{agent}/TOOLS.md", "# Tools\nacme_catalog: catálogo.\n")
        _overlay_file(bundle, f"workspace/{agent}/memory/MEMORY.md", "# Memoria Acme\n")
        _overlay_file(bundle, f"workspace/{agent}/skills/catalog/SKILL.md", "# Catálogo Acme\n")
    _overlay_file(
        bundle, "workspace/sales/skills/etapa_descubrimiento/SKILL.md", "Bienvenido a Acme.\n"
    )
    _overlay_file(bundle, "workspace/sales/skills/sales_script/SKILL.md", "# Guion Acme\n")
    return bundle


def _apply(mini_repo: Path, acme_bundle: Path, tmp_path: Path):
    dest = tmp_path / "AgencyAcme"
    report = forge.run_apply(
        src=mini_repo,
        dest=dest,
        client_dir=acme_bundle,
        manifest=forge.load_manifest(),
    )
    return dest, report


# ── El golden ─────────────────────────────────────────────────────────────────


def test_apply_renombra_infra_y_deja_clon_limpio(mini_repo, acme_bundle, tmp_path):
    dest, report = _apply(mini_repo, acme_bundle, tmp_path)

    # Terraform: prefijos + SSM + DLM + verify hardcodeado
    backup = (dest / "infra/terraform/compute/backup.tf").read_text()
    assert "agencyacme-dlm-backup" in backup
    assert "dlm-agencyacme" in backup
    assert 'Backup = "daily-acme"' in backup
    app_tf = (dest / "infra/terraform/compute/modules/app-instance/main.tf").read_text()
    assert "parameter/acme/${var.tenant}" in app_tf
    assert 'Backup = "daily-acme"' in app_tf
    boot = (dest / "infra/scripts/aws_bootstrap.py").read_text()
    assert "agencyacme-tfstate" in boot
    assert "/acme/acme/scheduler" in boot
    assert "einsteindark-edgm/AgencyAcme" in boot
    robo = (dest / "infra/robotocore/test-local.sh").read_text()
    assert "agencyacme-acme" in robo and "/acme/acme/" in robo

    # tfvars regenerados por template: un solo tenant, sin datos de hubara
    plat = (dest / "infra/terraform/platform/tenants.auto.tfvars").read_text()
    assert "acme" in plat and "vincenzo" not in plat and "98-88-237-207" not in plat
    comp = (dest / "infra/terraform/compute/tenants.auto.tfvars").read_text()
    assert "acme = {" in comp
    proj = (dest / "infra/terraform/platform/project.auto.tfvars").read_text()
    assert "create_github_oidc_provider = false" in proj

    # CI + compose + paths de caja
    be = (dest / ".github/workflows/backend-deploy.yml").read_text()
    assert "tenant: [acme]" in be
    assert "/opt/acme" in be and "/tmp/acme-deploy" in be
    assert "ghcr.io/${OWNER}/agencyacme:" in be
    assert 'paths:\n      - "hubara_agency/**"' in be  # nombre del motor, intacto
    fe = (dest / ".github/workflows/frontend-deploy.yml").read_text()
    assert "tenant: [acme]" in fe and "vincenzo" not in fe
    compose = (dest / "infra/compose/docker-compose.prod.yml").read_text()
    assert "name: acme-prod" in compose
    assert "acme-vault:/app/hubara_vault" in compose  # volumen renombrado, mount del motor intacto
    render = (dest / "infra/compose/render-env-from-ssm.sh").read_text()
    assert '"/acme/${TENANT}"' in render and "/opt/acme/box.env" in render

    # Identidad del agente: overlay reemplaza el workspace completo
    soul = (dest / f"{SALES_WS}/SOUL.md").read_text()
    assert "Acme" in soul and "Hubara" not in soul
    assert (dest / f"{SALES_WS}/skills/acme_catalog/SKILL.md").exists()
    assert not (dest / f"{SALES_WS}/skills/hubara_catalog").exists()
    assert (dest / f"{RMKT_WS}/skills/acme_catalog/SKILL.md").exists()
    ui = (dest / "hubara_agency/src/plugins/chats/agent/sales/tools/ui_intents.py").read_text()
    assert "ACM-acme-" in ui and "HUB-hubara-" not in ui
    assert "acme.example.com" in ui and "hubara.com.co" not in ui
    skills_py = (dest / "hubara_agency/src/plugins/chats/agent/sales/tools/skills.py").read_text()
    assert 'CATALOG_SKILL = "acme_catalog"' in skills_py

    # preserve_tokens: el nombre del workflow del motor queda INTACTO aunque la
    # marca del mismo archivo sí se reemplaza — renombrarlo a medias rompería
    # el dispatch (dispatcher/workers/tests lo referencian fuera del scope).
    wf = (
        dest / "hubara_agency/src/plugins/chats/agent/sales/workflows/sales_session.py"
    ).read_text()
    assert 'name="HubaraSalesSessionWorkflow"' in wf
    assert "AcmeSalesSessionWorkflow" not in wf
    assert 'GREETING = "Bienvenido a Acme"' in wf

    # GraphAgents: viaja al clon con identidad propia (tag/SSM/imagen), sin
    # tocar los nombres de MÓDULO tf ni los paths /opt de la caja ni los paths
    # del REPO (infra/compose/graphagents/ es carpeta, no se renombra)
    ga_tf = (
        dest / "infra/terraform/compute/modules/graphagents-instance/main.tf"
    ).read_text()
    assert 'Role = "graphagents-acme"' in ga_tf
    assert 'values   = ["graphagents-acme"]' in ga_tf
    assert "parameter/acme-graphagents" in ga_tf and "parameter/graphagents" not in ga_tf
    assert 'resource "aws_instance" "graphagents" {' in ga_tf  # nombre tf intacto
    ga_sec = (
        dest / "infra/terraform/platform/modules/graphagents-secrets/main.tf"
    ).read_text()
    assert 'prefix = "/acme-graphagents"' in ga_sec
    ga_vars = (dest / "infra/terraform/compute/variables.tf").read_text()
    assert "ghcr.io/einsteindark-edgm/acme-graphagents" in ga_vars
    ga_wf = (dest / ".github/workflows/graphagents-deploy.yml").read_text()
    assert "Values=graphagents-acme" in ga_wf
    assert "ghcr.io/${OWNER}/acme-graphagents:" in ga_wf
    assert "/opt/graphagents" in ga_wf  # path de la caja intacto
    assert "infra/compose/graphagents/docker-compose.prod.yml" in ga_wf  # path del repo intacto
    ga_render = (dest / "infra/compose/graphagents/render-env-from-ssm.sh").read_text()
    assert '--path "/acme-graphagents"' in ga_render
    assert "BOX_ENV=/opt/graphagents/box.env" in ga_render  # /opt intacto
    ga_ctl = (dest / "infra/scripts/graphagents_ctl.py").read_text()
    assert 'TAG = "Role=graphagents-acme"' in ga_ctl
    assert "Values=graphagents-acme" in ga_ctl
    cfg = (dest / "hubara_agency/src/platform/config.py").read_text()
    assert '"GRAPHAGENTS_INSTANCE_TAG", "graphagents-acme"' in cfg

    # WhatsApp provisioning
    wp = (dest / "infra/whatsapp-provisioning/whatsapp_provision.py").read_text()
    assert '/acme/{tenant}/meta/oauth' in wp and 'or "acme"' in wp
    flows = (dest / "infra/whatsapp-provisioning/definitions/flows.json").read_text()
    assert "Acme — Datos de envío" in flows
    assert (dest / "infra/whatsapp-provisioning/tenants/acme.env.example").exists()

    # Scrub: datos de Hubara y docs de otros clientes NO viajan
    # el vault se scrubbea (las sesiones del cliente Hubara no viajan) pero el
    # dir queda vacío con .gitkeep — el boot dev lo necesita presente
    vault = dest / "hubara_agency/hubara_vault"
    assert sorted(p.name for p in vault.iterdir()) == [".gitkeep"]
    for gone in [
        "infra/whatsapp-provisioning/tenants/hubara.env.example",
        "hubara_agency/scripts/inject_snapshot_products.py",
        "docs/cartagena",
        "MULTI_TENANT_COMMERCE_ARCHITECTURE.md",
        "VINCENZO_SPLIT_PLAN.md",
        "infra/INFRASTRUCTURE.md",
        "forge",
    ]:
        assert not (dest / gone).exists(), f"{gone} debía ser eliminado del clon"

    # Génesis git + runbook
    assert (dest / ".git").exists()
    log = subprocess.run(
        ["git", "log", "-1", "--format=%s"], cwd=dest, capture_output=True, text=True
    ).stdout
    assert "Acme" in log and "hubara engine" in log
    assert (dest / "NEXT_STEPS.md").exists()

    # Scanner: clon limpio
    assert report["scan"]["forbidden"] == []
    assert report["scan"]["critical"] == []


def test_scanner_bloquea_ids_reales_de_hubara(mini_repo, acme_bundle, tmp_path):
    dest, _ = _apply(mini_repo, acme_bundle, tmp_path)
    (dest / "infra" / "leak.txt").write_text("WABA_ID=3018148735027036\n", encoding="utf-8")
    scan = forge.scan_residuals(dest, forge.load_manifest(), forge.render_vars(ACME_CLIENT))
    assert any("3018148735027036" in str(v) for v in scan["forbidden"])


def test_scanner_bloquea_hubara_sin_clasificar_en_scope_critico(mini_repo, acme_bundle, tmp_path):
    dest, _ = _apply(mini_repo, acme_bundle, tmp_path)
    (dest / "infra" / "terraform" / "leak.tf").write_text(
        'name = "hubara-things"\n', encoding="utf-8"
    )
    scan = forge.scan_residuals(dest, forge.load_manifest(), forge.render_vars(ACME_CLIENT))
    assert any("leak.tf" in str(v) for v in scan["critical"])
    # …pero el nombre del motor (hubara_agency, HUBARA_*) NO es violación:
    assert not any("backend-deploy" in str(v) for v in scan["critical"])
    assert not any("docker-compose.prod" in str(v) for v in scan["critical"])


def test_overlay_incompleto_falla_con_lista(mini_repo, acme_bundle, tmp_path):
    (acme_bundle / "workspace/sales/SOUL.md").unlink()
    with pytest.raises(forge.ForgeError, match="SOUL.md"):
        _apply(mini_repo, acme_bundle, tmp_path)


def test_init_siembra_bundle_preservando_tokens_del_motor(mini_repo, tmp_path):
    """run_init reemplaza la marca pero NO los identificadores del motor:
    el TOOLS.md sembrado debe seguir diciendo HubaraSalesSessionWorkflow."""
    (mini_repo / SALES_WS / "TOOLS.md").write_text(
        "# Tools\nEl cierre dispara HubaraSalesSessionWorkflow para Hubara.\n",
        encoding="utf-8",
    )
    clients_dir = tmp_path / "clients"
    bundle = forge.run_init("acme", forge.load_manifest(), src=mini_repo, clients_dir=clients_dir)
    tools = (bundle / "workspace" / "sales" / "TOOLS.md").read_text()
    assert "HubaraSalesSessionWorkflow" in tools
    assert "AcmeSalesSessionWorkflow" not in tools
    assert "para Acme" in tools
    assert "TODO-BRAND" in tools


def test_todo_brand_bloquea_salvo_allow_todos(mini_repo, acme_bundle, tmp_path):
    (acme_bundle / "workspace/sales/SOUL.md").write_text(
        "# Soul Acme\nTODO-BRAND: describir el producto\n", encoding="utf-8"
    )
    with pytest.raises(forge.ForgeError, match="TODO-BRAND"):
        _apply(mini_repo, acme_bundle, tmp_path)
    dest = tmp_path / "AgencyAcme2"
    report = forge.run_apply(
        src=mini_repo,
        dest=dest,
        client_dir=acme_bundle,
        manifest=forge.load_manifest(),
        allow_todos=True,
    )
    assert report["scan"]["forbidden"] == []
