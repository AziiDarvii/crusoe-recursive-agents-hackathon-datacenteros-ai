// End-to-end demo check: reset → click "Inject Cooling Failure" → wait for VERIFIED → assert timeline →
// screenshot every page, then run the infeasible scenario and capture the escalation.
// Usage (backend on :8000, `npm run dev` on :5173):  node e2e/demo.mjs
import { mkdirSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { BASE, launch, resetScenario, sleep } from './cdp.mjs'

const OUT = resolve(import.meta.dirname, '../../docs/screenshots')
const RUN_TIMEOUT_MS = 30000
const DONE = `!!document.querySelector('.fsm-node.current.VERIFIED, .fsm-node.current.ESCALATED')`
const fail = (msg) => { console.error(`FAIL: ${msg}`); process.exitCode = 1 }

mkdirSync(OUT, { recursive: true })
await resetScenario('normal')
const b = await launch()
const shot = (name) => b.fullShot(join(OUT, `${name}.png`))

try {
  await b.send('Page.navigate', { url: `${BASE}/#overview` })
  await b.waitFor(`!!document.querySelector('.kpi')`)
  await sleep(500)
  await shot('01-overview-nominal')

  if (!await b.clickButton('Inject Cooling Failure')) throw new Error('Inject button not found')
  await b.waitFor(`!!document.querySelector('.fsm-node.current')`)
  await sleep(2500)
  await shot('02-agent-run-live')

  await b.waitFor(DONE, RUN_TIMEOUT_MS)
  await sleep(800)
  const text = await b.evaluate('document.body.innerText')
  for (const needle of ['Safety verdict: REJECT', 'Safety verdict: PASS', 'RACK_POWER_LIMIT', 'MEMORY_INCOMPATIBLE']) {
    if (!text.includes(needle)) fail(`timeline missing "${needle}"`)
  }
  await shot('03-agent-run-verified')

  for (const [tab, file] of [['Results', '04-results'], ['Digital Twin', '05-digital-twin'],
    ['Overview', '06-overview-recovered'], ['Memory', '07-memory'], ['Settings', '08-settings']]) {
    await b.clickButton(tab)
    await sleep(900)
    await shot(file)
  }
  const after = await (await fetch(`${BASE}/api/state`)).json()
  if (after.violations.length) fail(`violations remain after recovery: ${JSON.stringify(after.violations)}`)

  await b.clickButton('Benchmarks')
  await sleep(400)
  await b.clickButton('Run benchmarks')
  await b.waitFor(`document.body.innerText.includes('Download JSON')`, RUN_TIMEOUT_MS)
  await shot('09-benchmarks')

  await resetScenario('infeasible')
  await sleep(1800)
  await b.clickButton('Run agents')
  await b.waitFor(DONE, RUN_TIMEOUT_MS)
  await sleep(800)
  if (!(await b.evaluate('document.body.innerText')).includes('Escalated to human operator')) fail('expected escalation')
  await shot('10-agent-run-escalated')

  if (b.errors.length) fail(`browser exceptions: ${b.errors.join(' | ')}`)
  console.log(process.exitCode ? 'E2E finished with failures' : `E2E PASS — screenshots in ${OUT}`)
} finally {
  b.close()
  await resetScenario('normal')
}
