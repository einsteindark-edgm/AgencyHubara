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
MBA_WS = "hubara_agency/src/plugins/mba/agents/sales"
BUNDLES = "hubara_agency/src/plugins/chats/shared/decisions/bundles"
ETA_WS = "hubara_agency/src/plugins/eta/agent/eta/workspace"
ETA_FILES = ("IDENTITY.md", "SOUL.md", "AGENTS.md", "TOOLS.md", "USER.md")
ACME_DOMAIN = (
    "store_name: Acme\n"
    'farewell_order_registered: "Listo, tu pedido quedó registrado. Gracias por elegir a Acme."\n'
    "vocabulary:\n  product_example: \"'Café Huila'\"\n"
)

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
        'statement {\n  sid = "SendCommandToGraphAgentsBoxOnly"\n  condition {\n'
        '    variable = "ssm:resourceTag/Role"\n    values   = ["graphagents"]\n  }\n}\n'
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
    f"{SALES_WS}/skills/etapa_descubrimiento/SKILL.md": "Bienvenido a Hubara, velas artesanales.\n",
    f"{SALES_WS}/skills/sales_script/SKILL.md": "# Guion Hubara\n",
    f"{SALES_WS}/skills/hubara_catalog/SKILL.md": "# Catálogo Hubara\n",
    f"{RMKT_WS}/IDENTITY.md": "Asesor de remarketing de Hubara.\n",
    f"{RMKT_WS}/SOUL.md": "# Soul remarketing Hubara\n",
    f"{RMKT_WS}/TOOLS.md": "# Tools remarketing\n",
    f"{RMKT_WS}/skills/hubara_catalog/SKILL.md": "# Catálogo Hubara (remarketing)\n",
    # Meta Business Agent: el agente autorado (agent.yaml + skills/*.md) es voz del cliente
    f"{MBA_WS}/agent.yaml": (
        "id: sales\ndisplay_name: Asesor de Ventas\nskills: [persona-y-tono]\n"
        "business_info:\n  business_description: Hubara vende velas artesanales.\n"
        "connector:\n  name: hubara-commerce\n  description: API de Hubara.\n"
    ),
    f"{MBA_WS}/skills/persona-y-tono.md": (
        "---\ntitle: persona-y-tono\ndescription: Siempre.\n---\n\nEres el asesor de Hubara.\n"
    ),
    # Paquetes de decisión: `ventas` (el del código) + las versiones de la
    # tienda madre; `ventas-4` es el que corre hoy (manifest decision_bundles).
    **{
        f"{BUNDLES}/{b}/{f}": text
        for b in ("ventas", "ventas-2", "ventas-3", "ventas-4")
        for f, text in (
            ("bundle.yaml", f"# Paquete de decisión de Hubara (velas)\nid: {b}\n"),
            ("domain.yaml", "store_name: Hubara\nproduct_example: \"'Luz Serena'\"\n"),
            ("capabilities/portavelas.yaml", "expect: \"Gracias por elegir a Hubara.\"\n"),
        )
    },
    f"{BUNDLES}/builtins.yaml": "domain:\n  store_name: string\n",
    # App Operador nativa (Android): el applicationId es único en Google Play
    "android_operator/app/build.gradle.kts": (
        'android {\n    namespace = "com.hubara.operator"\n'
        '    defaultConfig {\n        applicationId = "com.acktos.operator"\n    }\n}\n'
    ),
    "android_operator/e2e/run_suite.py": 'PACKAGE = "com.acktos.operator"\n',
    "android_operator/release/ficha-play-store.md": "# Hubara Operador\nFicha de Hubara Operador.\n",
    "hubara_agency/src/platform/push/composition.py": 'DEFAULT_ANDROID_PACKAGE = "com.acktos.operator"\n',
    "docs/mobile-native/activar-avisos-push.html": (
        "<p>Proyecto Firebase hubara-operador</p>\n"
        "<code>python3 infra/scripts/aws_bootstrap.py secrets --tenant hubara</code>\n"
        "<code>pool agencyhubara-hubara</code>\n"
        "<a href=\"https://github.com/einsteindark-edgm/AgencyHubara/pull/385\">PR</a>\n"
    ),
    # Datos de pago REALES de Hubara: el Nequi no puede viajar como default
    "hubara_agency/src/plugins/chats/agent/sales/config/payments.py": (
        '# 2026-08-31 ("Pago anticipado por Nequi o mi llave 3229041190").\n'
        'PAYMENT_NEQUI_NUMBER_DEFAULT = "3229041190"\n'
    ),
    "hubara_agency/tests/plugins/orders/test_phone_resolution.py": (
        'assert _phone_match_key("+57 312-567-1604") == "3125671604"\n'
        'assert key("573125671604") == key("wa_573125671604")\n'
    ),
    # Agente ETA (avisos de pedidos): voz del cliente → overlay
    **{f"{ETA_WS}/{f}": f"# {f} — Asistente de Seguimiento de Hubara\n" for f in ETA_FILES},
    "hubara_agency/src/plugins/eta/agent/eta/prompts.py": (
        'TEXT = f"{_hola(name)} Soy tu asistente de seguimiento de Hubara. "\n'
        '@workflow.defn(name="HubaraEtaSessionWorkflow")\n'
    ),
    "hubara_agency/src/plugins/ads/meta/settings.py": (
        '        return f"/hubara/{self.tenant}/meta/oauth"\n'
        '        tenant=os.getenv("META_ADS_TENANT", "hubara"),\n'
    ),
    "hubara_agency/src/platform/lab/launcher.py": 'LAB_INSTANCE_TAG = "lab"\n',
    "infra/terraform/compute/modules/lab-instance/main.tf": (
        'tags = { Role = "lab" }\ncondition {\n  values   = ["lab"]\n}\n'
        'resources = ["arn:aws:ssm:*:*:parameter/hubara-lab/*"]\n'
    ),
    "infra/compose/lab/docker-compose.lab.yml": (
        "name: hubara-lab\nservices:\n  temporal:\n    command: [--namespace, hubara-lab]\n"
    ),
    "hubara_agency/src/plugins/chats/agent/sales_lab/guard.py": 'LAB_NAMESPACE = "hubara-lab"\n',
    "hubara_agency/src/plugins/chats/agent/sales/tools/links.py": (
        'ALLOWED = ["https://instagram.com/hubara.com.co", "https://hubara.com.co/"]\n'
    ),
    "hubara_agency/tests/plugins/mba/conftest.py": 'PHONE_NUMBER_ID = "1234091093112024"\n',
    ".github/workflows/forge-gates.yml": "name: forge-gates\n",
    "hubara_agency/src/plugins/chats/agent/sales/config/store_codes.py": (
        'class StoreCodes:\n    sku_prefix: str = "HUB-"\n    web_domain: str = "hubara"\n'
    ),
    # Aislamiento IAM entre proyectos de la cuenta: los árboles SSM y el tag de
    # la caja GraphAgents que permiten las políticas son los DEL PROYECTO
    "infra/terraform/platform/modules/github-oidc/main.tf": (
        'not_resources = [\n  "arn:aws:ssm:*:*:parameter/hubara/*",\n'
        '  "arn:aws:ssm:*:*:parameter/graphagents/*",\n  "arn:aws:ssm:*:*:parameter/hubara-lab/*",\n]\n'
    ),
    "docs/cartagena/plan.md": "# Vertical hotelero de otro cliente\n",
    # App móvil Tauri: viaja al clon, pero la EIP de hubara en el CSP no
    "frontend_dashboard/src-tauri/tauri.conf.json": (
        '{"csp": "connect-src https://98-88-237-207.sslip.io https://*.amazonaws.com"}\n'
    ),
    "frontend_dashboard/MOBILE_ANDROID_RUNBOOK.md": (
        "VITE_API_URL=https://98-88-237-207.sslip.io npm run tauri:android:build\n"
    ),
    "MOBILE_CHATS_PLAN_fable.md": "# Plan móvil de hubara (EIP 98-88-237-207)\n",
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
    "api_url": "https://api.acme.example.com",
    "aws": {"region": "us-east-1", "resource_prefix": "agencyacme", "ssm_prefix": "/acme"},
    "business": {
        "country": "CO",
        "currency": "COP",
        "product_description": "cafés de origen",
        "domains": ["acme.example.com"],
        "instagram": "acme.cafe",
    },
    "commerce": {
        "payment_nequi_number": "3001234567",
        "payment_link_surcharge_local": "2%",
        "payment_link_surcharge_other": "3,1%",
        "shipping_local_zone": "Medellín y el área metropolitana",
        "shipping_local_city": "Medellín",
        "shipping_rate_local_cop": 9000,
        "shipping_rate_national_cop": 18500,
        "cash_on_delivery_min_cop": 60000,
        "sku_prefix": "ACM-",
        "catalog_collections": ["vitrina"],
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
        _overlay_file(bundle, f"workspace/{agent}/skills/catalog/SKILL.md", "# Catálogo Acme\n")
    _overlay_file(
        bundle, "workspace/sales/skills/etapa_descubrimiento/SKILL.md", "Bienvenido a Acme.\n"
    )
    _overlay_file(bundle, "workspace/sales/skills/sales_script/SKILL.md", "# Guion Acme\n")
    _overlay_file(
        bundle,
        "workspace/mba_sales/agent.yaml",
        "id: sales\ndisplay_name: Asesor Acme\nskills: [persona-y-tono]\n"
        "business_info:\n  business_description: Acme vende cafés de origen.\n",
    )
    _overlay_file(
        bundle,
        "workspace/mba_sales/skills/persona-y-tono.md",
        "---\ntitle: persona-y-tono\ndescription: Siempre.\n---\n\nEres el asesor de Acme.\n",
    )
    for f in ETA_FILES:
        _overlay_file(bundle, f"workspace/eta/{f}", f"# {f} — seguimiento de pedidos de Acme\n")
    # El concepto de la tienda para el motor de decisiones (domain.yaml)
    _overlay_file(bundle, "domain.yaml", ACME_DOMAIN)
    return bundle


def _apply(mini_repo: Path, acme_bundle: Path, tmp_path: Path, allow_todos: bool = False):
    dest = tmp_path / "AgencyAcme"
    report = forge.run_apply(
        src=mini_repo,
        dest=dest,
        client_dir=acme_bundle,
        manifest=forge.load_manifest(),
        allow_todos=allow_todos,
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

    # App móvil + platform + webhook: la EIP de hubara se reemplaza por la URL
    # REAL del cliente cuando client.yaml define api_url (placeholder si no)
    # la app Tauri (congelada, identidad com.hubara.dashboard) no viaja: la
    # app del clon es la nativa (android_operator/) con applicationId propio
    assert not (dest / "frontend_dashboard/src-tauri").exists()
    assert not (dest / "frontend_dashboard/MOBILE_ANDROID_RUNBOOK.md").exists()
    assert 'api_url = "https://api.acme.example.com"' in plat
    wa_env = (dest / "infra/whatsapp-provisioning/tenants/acme.env.example").read_text()
    assert "CALLBACK_URL=https://api.acme.example.com/api/chats/webhook" in wa_env

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
        "MOBILE_CHATS_PLAN_fable.md",
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


def test_sin_api_url_todo_queda_en_placeholder(mini_repo, acme_bundle, tmp_path):
    """Sin api_url en client.yaml (la EIP aún no existe), el clon sale con el
    placeholder TODO-EIP — nunca con la URL de hubara."""
    import yaml as _yaml

    client = dict(ACME_CLIENT)
    client.pop("api_url")
    (acme_bundle / "client.yaml").write_text(_yaml.safe_dump(client), encoding="utf-8")
    dest = tmp_path / "SinApi"
    forge.run_apply(src=mini_repo, dest=dest, client_dir=acme_bundle, manifest=forge.load_manifest())
    plat = (dest / "infra/terraform/platform/tenants.auto.tfvars").read_text()
    assert "TODO-EIP.sslip.io" in plat


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


# ── Meta Business Agent viaja al clon ────────────────────────────────────────


def test_mba_viaja_al_clon_con_overlay_propio(mini_repo, acme_bundle, tmp_path):
    """El agente MBA (agent.yaml + skills) es voz del cliente: el bundle lo
    reemplaza completo, y el clon nace con `mba` habilitado."""
    dest, _ = _apply(mini_repo, acme_bundle, tmp_path)
    agent = (dest / f"{MBA_WS}/agent.yaml").read_text()
    assert "Acme" in agent and "Hubara" not in agent
    skill = (dest / f"{MBA_WS}/skills/persona-y-tono.md").read_text()
    assert "asesor de Acme" in skill
    comp = (dest / "infra/terraform/compute/tenants.auto.tfvars").read_text()
    assert "mba" in comp.split("enabled_plugins")[1].splitlines()[0]
    nxt = (dest / "NEXT_STEPS.md").read_text()
    assert "HUBARA_MBA_API_KEY" in nxt


def test_overlay_sin_bundle_mba_falla_nombrando_el_agente(mini_repo, acme_bundle, tmp_path):
    import shutil

    shutil.rmtree(acme_bundle / "workspace/mba_sales")
    with pytest.raises(forge.ForgeError, match="mba_sales"):
        _apply(mini_repo, acme_bundle, tmp_path)


def test_scanner_trata_el_agente_mba_como_scope_critico(mini_repo, acme_bundle, tmp_path):
    dest, _ = _apply(mini_repo, acme_bundle, tmp_path)
    (dest / MBA_WS / "skills" / "leak.md").write_text("Bienvenido a Hubara\n", encoding="utf-8")
    scan = forge.scan_residuals(dest, forge.load_manifest(), forge.render_vars(ACME_CLIENT))
    assert any("leak.md" in str(v) for v in scan["critical"])


def test_init_siembra_el_agente_mba_con_yaml_valido(mini_repo, tmp_path):
    import yaml as _yaml

    clients_dir = tmp_path / "clients"
    bundle = forge.run_init("acme", forge.load_manifest(), src=mini_repo, clients_dir=clients_dir)
    agent_path = bundle / "workspace" / "mba_sales" / "agent.yaml"
    text = agent_path.read_text()
    assert "Acme vende" in text and "Hubara" not in text
    assert "TODO-BRAND" in text
    # la marca de redacción pendiente NO puede romper el YAML del agente
    spec = _yaml.safe_load(text)
    assert spec["display_name"] == "Asesor de Ventas"
    skill = (bundle / "workspace" / "mba_sales" / "skills" / "persona-y-tono.md").read_text()
    assert "asesor de Acme" in skill and "TODO-BRAND" in skill


def test_init_mas_apply_dejan_el_clon_mba_sin_residuales(mini_repo, tmp_path):
    """Lo que corre el gate `forge-gates` de CI: init → apply --allow-todos debe
    dejar cero residuales. El nombre del connector (`hubara-commerce`) es del
    cliente, no del motor: se siembra como `<slug>-commerce`."""
    clients_dir = tmp_path / "clients"
    bundle = forge.run_init("acme", forge.load_manifest(), src=mini_repo, clients_dir=clients_dir)
    agent = (bundle / "workspace" / "mba_sales" / "agent.yaml").read_text()
    assert "acme-commerce" in agent and "hubara-commerce" not in agent
    dest = tmp_path / "AgencyAcmeInit"
    report = forge.run_apply(
        src=mini_repo, dest=dest, client_dir=bundle, manifest=forge.load_manifest(), allow_todos=True
    )
    assert report["scan"]["forbidden"] == []
    assert report["scan"]["critical"] == [], report["scan"]["critical"]


# ── Paquete de decisión: el clon arranca con el que corre la tienda madre ─────


def test_clon_viaja_con_el_paquete_de_la_tienda_y_el_del_codigo(mini_repo, acme_bundle, tmp_path):
    """La inteligencia del motor (preguntas a Jev, umbrales, política del
    turno) es del MOTOR: el clon corre la versión que corre hoy la tienda
    madre (`decision_bundles.store`), no la v1. Las versiones intermedias son
    experimentos de la tienda madre y no viajan."""
    dest, _ = _apply(mini_repo, acme_bundle, tmp_path)
    store = forge.load_manifest()["decision_bundles"]["store"]
    kept = sorted(p.name for p in (dest / BUNDLES).iterdir() if p.is_dir())
    assert kept == sorted({"ventas", store})
    assert (dest / BUNDLES / "builtins.yaml").exists()  # catálogo del motor, intacto


def test_el_dominio_de_cada_paquete_es_el_concepto_del_cliente(mini_repo, acme_bundle, tmp_path):
    """`domain.yaml` (nombre, despedida, ejemplos que ve el agente) es el
    concepto de la tienda: sale del bundle del cliente a TODOS los paquetes
    que viajan — ningún paquete del clon habla de velas ni de Hubara."""
    dest, _ = _apply(mini_repo, acme_bundle, tmp_path)
    for b in (p for p in (dest / BUNDLES).iterdir() if p.is_dir()):
        assert (b / "domain.yaml").read_text() == ACME_DOMAIN, b.name
        assert "Hubara" not in (b / "capabilities/portavelas.yaml").read_text()


def test_terraform_del_clon_nombra_el_paquete_de_la_tienda(mini_repo, acme_bundle, tmp_path):
    dest, _ = _apply(mini_repo, acme_bundle, tmp_path)
    store = forge.load_manifest()["decision_bundles"]["store"]
    plat = (dest / "infra/terraform/platform/tenants.auto.tfvars").read_text()
    import re

    assert re.search(rf'decisions_bundle\s+= "{store}"', plat)


def test_sin_domain_yaml_el_apply_falla_pidiendo_el_concepto(mini_repo, acme_bundle, tmp_path):
    (acme_bundle / "domain.yaml").unlink()
    with pytest.raises(forge.ForgeError, match="domain.yaml"):
        _apply(mini_repo, acme_bundle, tmp_path)


def test_init_siembra_el_dominio_neutral_con_todo_brand(mini_repo, tmp_path):
    import yaml as _yaml

    bundle = forge.run_init("acme", forge.load_manifest(), src=mini_repo, clients_dir=tmp_path / "c")
    text = (bundle / "domain.yaml").read_text()
    assert "TODO-BRAND" in text and "Hubara" not in text and "velas" not in text.lower()
    dom = _yaml.safe_load(text)
    assert dom["store_name"] == "Acme"
    assert dom["farewell_order_registered"].endswith("Gracias por elegir a Acme.")


# ── App Operador + datos reales de Hubara ─────────────────────────────────────


def test_app_operador_tiene_identidad_propia(mini_repo, acme_bundle, tmp_path):
    """El applicationId es único en Google Play/Firebase: el clon publica su
    propia app. El namespace Kotlin (`com.hubara.operator`) es del motor."""
    dest, _ = _apply(mini_repo, acme_bundle, tmp_path)
    gradle = (dest / "android_operator/app/build.gradle.kts").read_text()
    assert 'applicationId = "com.acktos.acme"' in gradle
    assert 'namespace = "com.hubara.operator"' in gradle
    assert 'PACKAGE = "com.acktos.acme"' in (dest / "android_operator/e2e/run_suite.py").read_text()
    push = (dest / "hubara_agency/src/platform/push/composition.py").read_text()
    assert '"com.acktos.acme"' in push
    ficha = (dest / "android_operator/release/ficha-play-store.md").read_text()
    assert "Acme Operador" in ficha and "Hubara" not in ficha
    doc = (dest / "docs/mobile-native/activar-avisos-push.html").read_text()
    for gone in ("hubara-operador", "--tenant hubara", "agencyhubara", "AgencyHubara"):
        assert gone not in doc, gone
    assert "acme-operador" in doc and "--tenant acme" in doc
    nxt = (dest / "NEXT_STEPS.md").read_text()
    assert "com.acktos.acme" in nxt and "FCM_SERVICE_ACCOUNT_JSON" in nxt


def test_app_id_de_hubara_rechazado():
    client = dict(ACME_CLIENT, android_app_id="com.acktos.operator")
    with pytest.raises(forge.ForgeError, match="com.acktos.operator"):
        forge.render_vars(client)


def test_el_nequi_de_hubara_no_viaja_y_los_telefonos_reales_se_normalizan(
    mini_repo, acme_bundle, tmp_path
):
    """El Nequi del clon nace vacío (fail-closed: el bot no ofrece pago
    anticipado hasta que la tienda ponga PAYMENT_NEQUI_NUMBER en SSM), y los
    teléfonos reales de Hubara en cualquier formato pasan a sintéticos."""
    dest, report = _apply(mini_repo, acme_bundle, tmp_path)
    pay = (dest / "hubara_agency/src/plugins/chats/agent/sales/config/payments.py").read_text()
    assert 'PAYMENT_NEQUI_NUMBER_DEFAULT = ""' in pay
    assert "3229041190" not in pay
    phones = (dest / "hubara_agency/tests/plugins/orders/test_phone_resolution.py").read_text()
    assert "3125671604" not in phones and "312-567-1604" not in phones
    # el test sigue siendo coherente: el mismo número en sus tres formatos
    assert '"+57 300-000-0000") == "3000000000"' in phones
    # la llave de la tienda nace en Terraform (tenants.<t>.store), no a mano
    assert "store = { payment_nequi_number" in (dest / "NEXT_STEPS.md").read_text()
    plat = (dest / "infra/terraform/platform/tenants.auto.tfvars").read_text()
    assert "payment_nequi_number" in plat


def test_scanner_bloquea_los_telefonos_reales_sin_prefijo(mini_repo, acme_bundle, tmp_path):
    dest, _ = _apply(mini_repo, acme_bundle, tmp_path)
    (dest / "leak.md").write_text("Nequi 3229041190 · +57 312 567 1604\n", encoding="utf-8")
    scan = forge.scan_residuals(dest, forge.load_manifest(), forge.render_vars(ACME_CLIENT))
    assert any("3229041190" in v for v in scan["forbidden"])
    assert any("312 567 1604" in v for v in scan["forbidden"])


# ── Agentes y config del motor que nombran a la tienda ────────────────────────


def test_agente_eta_habla_como_el_cliente(mini_repo, acme_bundle, tmp_path):
    dest, _ = _apply(mini_repo, acme_bundle, tmp_path)
    for f in ETA_FILES:
        assert "Acme" in (dest / ETA_WS / f).read_text()
    prompts = (dest / "hubara_agency/src/plugins/eta/agent/eta/prompts.py").read_text()
    assert "seguimiento de Acme" in prompts
    assert 'name="HubaraEtaSessionWorkflow"' in prompts  # nombre del motor intacto


def test_ads_lee_el_token_del_clon_no_el_de_hubara(mini_repo, acme_bundle, tmp_path):
    dest, _ = _apply(mini_repo, acme_bundle, tmp_path)
    st = (dest / "hubara_agency/src/plugins/ads/meta/settings.py").read_text()
    assert 'f"/acme/{self.tenant}/meta/oauth"' in st
    assert '"META_ADS_TENANT", "acme"' in st


def test_caja_del_laboratorio_con_identidad_propia(mini_repo, acme_bundle, tmp_path):
    """Tag y SSM únicos a nivel cuenta: renombrados. Namespace/proyecto dentro
    de la caja: del motor, intactos (el guard del worker los exige iguales)."""
    dest, _ = _apply(mini_repo, acme_bundle, tmp_path)
    tf = (dest / "infra/terraform/compute/modules/lab-instance/main.tf").read_text()
    assert 'Role = "lab-acme"' in tf and 'values   = ["lab-acme"]' in tf
    assert "parameter/acme-lab/*" in tf
    assert 'LAB_INSTANCE_TAG = "lab-acme"' in (dest / "hubara_agency/src/platform/lab/launcher.py").read_text()
    compose = (dest / "infra/compose/lab/docker-compose.lab.yml").read_text()
    guard = (dest / "hubara_agency/src/plugins/chats/agent/sales_lab/guard.py").read_text()
    assert "--namespace, hubara-lab" in compose and '"hubara-lab"' in guard


def test_instagram_y_dominio_del_cliente(mini_repo, acme_bundle, tmp_path):
    dest, _ = _apply(mini_repo, acme_bundle, tmp_path)
    links = (dest / "hubara_agency/src/plugins/chats/agent/sales/tools/links.py").read_text()
    assert "https://instagram.com/acme.cafe" in links and "https://acme.example.com/" in links


def test_ids_reales_de_meta_pasan_a_sinteticos(mini_repo, acme_bundle, tmp_path):
    dest, report = _apply(mini_repo, acme_bundle, tmp_path)
    assert "1234091093112024" not in (dest / "hubara_agency/tests/plugins/mba/conftest.py").read_text()
    assert report["scan"]["forbidden"] == []


def test_el_gate_de_forge_no_viaja_al_clon(mini_repo, acme_bundle, tmp_path):
    """forge/ no viaja; su workflow de CI fallaría en cada PR del clon."""
    dest, _ = _apply(mini_repo, acme_bundle, tmp_path)
    assert not (dest / ".github/workflows/forge-gates.yml").exists()


def test_init_deja_el_frontmatter_de_las_skills_al_principio(mini_repo, tmp_path):
    """Una skill (MBA o del workspace) empieza con su frontmatter `---`: si el
    banner TODO-BRAND va antes, el cargador no lee su `description` y la skill
    queda rota. El banner va DESPUÉS del frontmatter."""
    bundle = forge.run_init("acme", forge.load_manifest(), src=mini_repo, clients_dir=tmp_path / "c")
    skill = (bundle / "workspace" / "mba_sales" / "skills" / "persona-y-tono.md").read_text()
    assert skill.startswith("---\ntitle: persona-y-tono\ndescription: Siempre.\n---\n")
    assert "TODO-BRAND" in skill


# ── Política comercial: client.yaml → commerce → Terraform del clon ───────────


def test_la_politica_comercial_del_cliente_llega_a_su_terraform(mini_repo, acme_bundle, tmp_path):
    """Tarifas, contra entrega, pagos y códigos del catálogo son del cliente:
    viajan de client.yaml al bloque `store` del tfvars (→ SSM → .env)."""
    dest, _ = _apply(mini_repo, acme_bundle, tmp_path)
    plat = (dest / "infra/terraform/platform/tenants.auto.tfvars").read_text()
    for line in (
        'payment_nequi_number = "3001234567"',
        'payment_link_surcharge_local = "2%"',
        'shipping_local_zone = "Medellín y el área metropolitana"',
        "shipping_rate_local_cop = 9000",
        "shipping_rate_national_cop = 18500",
        "cash_on_delivery_min_cop = 60000",
        'sku_prefix = "ACM-"',
        'web_domain = "acme.example.com"',  # sale de business.domains
        'catalog_collections = ["vitrina"]',
    ):
        assert line in " ".join(plat.split()), line


def test_sin_politica_comercial_el_apply_real_falla_y_dice_que_falta(mini_repo, acme_bundle, tmp_path):
    import yaml as _yaml

    client = dict(ACME_CLIENT, commerce={"sku_prefix": "ACM-", "shipping_rate_local_cop": "TODO"})
    (acme_bundle / "client.yaml").write_text(_yaml.safe_dump(client), encoding="utf-8")
    with pytest.raises(forge.ForgeError, match="shipping_rate_local_cop"):
        _apply(mini_repo, acme_bundle, tmp_path)
    # clon de prueba: lo pendiente queda fuera (manda el default del motor)
    dest = tmp_path / "Prueba"
    forge.run_apply(src=mini_repo, dest=dest, client_dir=acme_bundle, manifest=forge.load_manifest(), allow_todos=True)
    plat = (dest / "infra/terraform/platform/tenants.auto.tfvars").read_text()
    assert 'sku_prefix = "ACM-"' in " ".join(plat.split()) and "shipping_rate_local_cop" not in plat


def test_una_politica_mal_escrita_falla_antes_de_forjar(mini_repo, acme_bundle, tmp_path):
    import yaml as _yaml

    bad = dict(ACME_CLIENT["commerce"], shipping_rate_local_cop="7.900")
    (acme_bundle / "client.yaml").write_text(_yaml.safe_dump(dict(ACME_CLIENT, commerce=bad)), encoding="utf-8")
    with pytest.raises(forge.ForgeError, match="shipping_rate_local_cop"):
        _apply(mini_repo, acme_bundle, tmp_path, allow_todos=True)


def test_init_siembra_la_politica_comercial_pendiente(mini_repo, tmp_path):
    import yaml as _yaml

    bundle = forge.run_init("acme", forge.load_manifest(), src=mini_repo, clients_dir=tmp_path / "c")
    commerce = _yaml.safe_load((bundle / "client.yaml").read_text())["commerce"]
    assert set(commerce) >= {"shipping_rate_local_cop", "cash_on_delivery_min_cop", "sku_prefix", "payment_nequi_number"}
    assert commerce["shipping_rate_local_cop"] == "TODO" and commerce["payment_nequi_number"] == ""


def test_el_dominio_por_defecto_del_clon_es_el_del_cliente(mini_repo, acme_bundle, tmp_path):
    """Sin STORE_WEB_DOMAIN (dev, tests) un enlace de SU tienda en una foto
    sigue siendo suyo: el default del clon es su dominio, no `hubara`."""
    dest, _ = _apply(mini_repo, acme_bundle, tmp_path)
    codes = (dest / "hubara_agency/src/plugins/chats/agent/sales/config/store_codes.py").read_text()
    assert 'web_domain: str = "acme.example.com".lower()' in codes


def test_el_aislamiento_iam_del_clon_usa_sus_propios_arboles_y_tags(mini_repo, acme_bundle, tmp_path):
    """El CI del clon solo lee SUS parámetros y su caja de app solo le manda
    comandos a SU GraphAgents: los nombres de Hubara no sobreviven."""
    dest, _ = _apply(mini_repo, acme_bundle, tmp_path)
    oidc = (dest / "infra/terraform/platform/modules/github-oidc/main.tf").read_text()
    for tree in ("parameter/acme/*", "parameter/acme-graphagents/*", "parameter/acme-lab/*"):
        assert tree in oidc, tree
    assert "parameter/hubara" not in oidc and "parameter/graphagents" not in oidc
    app = (dest / "infra/terraform/compute/modules/app-instance/main.tf").read_text()
    assert 'values   = ["graphagents-acme"]' in app
