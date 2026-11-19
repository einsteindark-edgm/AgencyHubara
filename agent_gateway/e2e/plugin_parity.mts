/**
 * E2E con el cliente REAL del plugin: `McpAdsSource` (@modelcontextprotocol/sdk 1.32, protocolo
 * 2025-11-25, HTTP real) contra el gateway, comparado con `FixtureAdsSource` — la misma prueba de
 * `tests/sources-parity.spec.ts` del plugin, pero sin el gateway falso en memoria.
 *
 *   PLUGIN_DIR=… GATEWAY_URL=http://localhost:8765/mcp TOKEN=… node e2e/plugin_parity.mts
 *
 * Solo importa el código del plugin (no lo modifica). Sale con 1 si alguna vista difiere.
 */
import { pathToFileURL } from 'node:url'
import { join } from 'node:path'

const plugin = process.env.PLUGIN_DIR!
const imp = (p: string) => import(pathToFileURL(join(plugin, p)).href)
const { AdsService } = await imp('src/host/service.ts')
const { FixtureAdsSource } = await imp('src/host/sources/fixture.ts')
const { McpAdsSource } = await imp('src/host/sources/mcp.ts')
// Mismas constantes que `tests/helpers/fixtures.ts` del plugin (que depende de __dirname de vitest).
const FIXTURES_DIR = join(plugin, '..', '..', 'fixtures', 'ads')
const PROD = 'prod-2026-10-09'
const MAIN_CAMPAIGN = '120247398115530317'

const signal = () => new AbortController().signal
const token = process.env.TOKEN
const viaMcp = new AdsService(new McpAdsSource({ url: process.env.GATEWAY_URL!, token: () => token }), { maskPii: true })
const viaFixture = new AdsService(new FixtureAdsSource({ dir: FIXTURES_DIR, dataset: PROD }), { maskPii: true })
const strip = (v: unknown) => JSON.parse(JSON.stringify(v, (k, val) => (k === 'source' ? undefined : val)))

type S = InstanceType<typeof AdsService>
const cases: [string, (s: S) => Promise<unknown>][] = [
  ['overview 7d', s => s.overview('7d', signal())],
  ['overview 30d', s => s.overview('30d', signal())],
  ['overview 90d', s => s.overview('90d', signal())],
  ['overview total', s => s.overview('total', signal())],
  ['overview custom', s => s.overview('2026-09-01..2026-09-30', signal())],
  ['campaign', s => s.campaign({ campaign_id: MAIN_CAMPAIGN }, '30d', signal())],
  ['adset', s => s.campaign({ campaign_id: MAIN_CAMPAIGN, adset_id: '120247398115600317' }, '7d', signal())],
  ['daily 30d', s => s.daily({ campaign_id: MAIN_CAMPAIGN }, '30d', signal())],
  ['daily adset 7d', s => s.daily({ campaign_id: MAIN_CAMPAIGN, adset_id: '120247398115600317' }, '7d', signal())],
  ['breakdown adsets', s => s.breakdown({ campaign_id: MAIN_CAMPAIGN, level: 'adsets' }, '30d', signal())],
  ['conversations', s => s.conversations({ campaign_id: MAIN_CAMPAIGN, limit: 50 }, '30d', signal())],
  ['meta', s => s.metaStatus(signal())],
  ['daily 90d (404 → unavailable)', s => s.daily({ campaign_id: MAIN_CAMPAIGN }, '90d', signal())],
]

let failed = 0
for (const [label, run] of cases) {
  const [a, b] = await Promise.all([run(viaFixture), run(viaMcp)])
  // Un 404 se compara como en el test del plugin: {kind, status} (el `reason` lo redacta cada fuente).
  const shape = (v: any) => (v?.kind === 'unavailable' ? { kind: v.kind, status: v.status } : strip(v))
  const same = JSON.stringify(shape(a)) === JSON.stringify(shape(b))
  if (!same) failed++
  console.log(`${same ? 'OK  ' : 'DIFF'} ${label}${same ? '' : `\n  fixture: ${JSON.stringify(strip(a)).slice(0, 300)}\n  mcp:     ${JSON.stringify(strip(b)).slice(0, 300)}`}`)
}
const kind = ((await viaMcp.overview('30d', signal())) as any).source?.kind
console.log(`source.kind = ${kind}`)
await viaMcp.source.close?.()
process.exit(failed || kind !== 'mcp' ? 1 : 0)
