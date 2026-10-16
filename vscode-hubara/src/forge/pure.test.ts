// Lógica pura de la Forge Console (premortem 2026-10-09). `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { checkDestPath, commercePending, mainRepoRoot, parseMigrationStatus, redactSecrets, SLUG_RE } from "./pure";

test("el nombre corto cabe en los nombres de AWS (buckets S3 y dominio de Cognito: sin _ y cortos)", () => {
  for (const ok of ["aurora", "cafeaurora2", "ab"]) assert.ok(SLUG_RE.test(ok), ok);
  for (const bad of ["cafe_aurora", "a", "Aurora", "1abc", "cafe-aurora", "x".repeat(21)]) {
    assert.ok(!SLUG_RE.test(bad), bad);
  }
});

test("un secreto impreso por un paso no llega al log ni al panel", () => {
  const token = "aws ssm put-parameter --name /aurora/aurora/MEDUSA_ADMIN_TOKEN --type SecureString --overwrite --value 'sk_live_123'";
  assert.equal(
    redactSecrets(token),
    "aws ssm put-parameter --name /aurora/aurora/MEDUSA_ADMIN_TOKEN --type SecureString --overwrite --value '••••'",
  );
  const url = "aws ssm put-parameter --name /aurora/aurora/MEDUSA_BASE_URL --type SecureString --overwrite --value 'https://x.up.railway.app'";
  assert.equal(redactSecrets(url), url, "lo que no es secreto se ve tal cual");
  assert.equal(
    redactSecrets("DATABASE_URL=postgresql://postgres:s3cr3t@db.abc.supabase.co:5432/postgres"),
    "DATABASE_URL=postgresql://postgres:••••@db.abc.supabase.co:5432/postgres",
  );
});

test("la carpeta del clon es una ruta completa sin espacios ni caracteres de la terminal", () => {
  assert.equal(checkDestPath("/Users/op/Clientes/AgencyAurora"), null);
  assert.equal(checkDestPath("/Users/op/Clientes/Café/AgencyAurora"), null);
  for (const bad of ["Clientes/AgencyAurora", "/Users/op/My Clients/AgencyAurora", "/tmp/a;rm -rf", "/tmp/$(x)", "/tmp/a'b", ""]) {
    assert.notEqual(checkDestPath(bad), null, bad);
  }
});

test("la política comercial pendiente no deja la ficha «lista para forjar»", () => {
  assert.deepEqual(
    commercePending({ commerce: { shipping_local_city: "TODO", shipping_rate_local_cop: 9000, sku_prefix: "TODO-x" } }),
    ["commerce.shipping_local_city", "commerce.sku_prefix"],
  );
  assert.deepEqual(commercePending({ commerce: { shipping_rate_local_cop: 9000, catalog_collections: [] } }), []);
  assert.equal(commercePending({}).length, 1, "sin el bloque commerce, forge no forja");
});

test("el estado de la migración recuerda la carpeta del clon", () => {
  const raw = JSON.stringify({ slug: "aurora", company: "Café Aurora", dest: "/Users/op/AgencyAurora", steps: [] });
  assert.equal(parseMigrationStatus(raw).dest, "/Users/op/AgencyAurora");
  const old = JSON.stringify({ slug: "aurora", company: "Café Aurora", steps: [] });
  assert.equal(parseMigrationStatus(old).dest, null);
});

test("los bundles se buscan donde los guarda el CLI: el checkout principal, no el worktree", () => {
  assert.equal(mainRepoRoot("/Users/op/AgencyHubara/.claude/worktrees/x"), "/Users/op/AgencyHubara");
  assert.equal(mainRepoRoot("/Users/op/AgencyHubara"), "/Users/op/AgencyHubara");
});
