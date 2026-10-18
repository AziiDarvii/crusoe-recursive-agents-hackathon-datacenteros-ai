// Records a captioned demo video of the live app (real clicks, real agent run) via CDP screencast + ffmpeg.
// Usage (backend on :8000, `npm run dev` on :5173):  node e2e/record.mjs  → docs/video/datacenteros-ai-demo.mp4
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { BASE, launch, resetScenario, sleep } from './cdp.mjs'

const OUT_DIR = resolve(import.meta.dirname, '../../docs/video')
const OUT = join(OUT_DIR, 'datacenteros-ai-demo.mp4')
const FRAMES = mkdtempSync(join(tmpdir(), 'dcos-frames-'))
const VIDEO_PACING_S = 0.9
const RUN_TIMEOUT_MS = 60000
const DONE = `!!document.querySelector('.fsm-node.current.VERIFIED, .fsm-node.current.ESCALATED')`

const putConfig = async (cfg) => fetch(`${BASE}/api/config`, {
  method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cfg),
})

mkdirSync(OUT_DIR, { recursive: true })
await resetScenario('normal')
const originalConfig = await (await fetch(`${BASE}/api/config`)).json()
await putConfig({ ...originalConfig, pacing_s: VIDEO_PACING_S })

const b = await launch({ port: 9334 })
const frames = []
b.on('Page.screencastFrame', ({ data, metadata, sessionId }) => {
  const file = join(FRAMES, `f${String(frames.length).padStart(5, '0')}.jpg`)
  writeFileSync(file, Buffer.from(data, 'base64'))
  frames.push({ file, t: metadata.timestamp })
  b.send('Page.screencastFrameAck', { sessionId }).catch(() => {})
})

const OVERLAY_CSS = `
  #dcos-cap{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);z-index:9999;max-width:1100px;
    background:rgba(6,10,15,.92);border:1px solid rgba(76,201,240,.55);color:#e6f1fa;padding:14px 22px;border-radius:12px;
    font:600 20px/1.35 Inter,system-ui,sans-serif;box-shadow:0 10px 40px rgba(0,0,0,.6);text-align:center}
  #dcos-cap small{display:block;font-weight:400;font-size:15px;color:#9fb3c8;margin-top:4px}
  #dcos-card{position:fixed;inset:0;z-index:10000;display:grid;place-items:center;text-align:center;
    background:radial-gradient(1200px 600px at 50% 40%,#10263a,#05080c);color:#e6f1fa;font-family:Inter,system-ui,sans-serif}
  #dcos-card h1{font-size:56px;margin:0 0 12px;letter-spacing:-.02em}
  #dcos-card p{font-size:22px;color:#9fb3c8;margin:6px 0}
  #dcos-card .tag{display:inline-block;margin-top:22px;padding:6px 14px;border-radius:999px;font-size:16px;
    color:#fbbf24;border:1px solid rgba(251,191,36,.4);background:rgba(251,191,36,.1)}`

const ensureCss = () => b.evaluate(`(() => { if (!document.getElementById('dcos-style')) {
  const s = document.createElement('style'); s.id = 'dcos-style'; s.textContent = ${JSON.stringify(OVERLAY_CSS)};
  document.head.appendChild(s) } })()`)
const caption = async (title, sub = '') => {
  await ensureCss()
  await b.evaluate(`(() => { let c = document.getElementById('dcos-cap'); if (!c) { c = document.createElement('div');
    c.id = 'dcos-cap'; document.body.appendChild(c) } c.innerHTML = ${JSON.stringify(title)} +
    (${JSON.stringify(sub)} ? '<small>' + ${JSON.stringify(sub)} + '</small>' : '') })()`)
}
const card = async (html, ms) => {
  await ensureCss()
  await b.evaluate(`(() => { const c = document.createElement('div'); c.id = 'dcos-card';
    c.innerHTML = ${JSON.stringify(html)}; document.body.appendChild(c) })()`)
  await sleep(ms)
  await b.evaluate(`document.getElementById('dcos-card')?.remove()`)
}
const scroll = async (to, ms = 1500) => {
  await b.evaluate(`window.scrollTo({ top: ${to}, behavior: 'smooth' })`)
  await sleep(ms)
}
const tab = async (name) => { await b.clickButton(name); await scroll(0, 300); await sleep(500) }

try {
  await b.send('Page.navigate', { url: `${BASE}/#overview` })
  await b.waitFor(`!!document.querySelector('.kpi')`)
  await sleep(800)
  await b.send('Page.startScreencast', { format: 'jpeg', quality: 82, maxWidth: 1440, maxHeight: 900 })

  await card(`<div><h1>DataCenterOS AI</h1><p>Recursive AI agents that optimize GPU data centers</p>
    <p>and recover from operational failures</p><span class="tag">Crusoe Recursive Agents Hackathon · simulated digital twin</span></div>`, 5000)

  await caption('Operations overview: 8 racks · 64 GPUs · all constraints satisfied',
    'Facility power, energy, cooling, temperatures and the workload queue are computed by a deterministic simulator')
  await sleep(4500)
  await scroll(520, 2500)
  await sleep(1500)
  await tab('Digital Twin')
  await caption('Digital twin: every rack has its own power budget, cooling capacity and GPU memory',
    'GPU tiles show job placement · meters show IT power and cooling load · sparklines show outlet temperature over 6 h')
  await sleep(5500)

  await tab('Overview')
  await caption('Now: inject a cooling failure', 'Rack 04 loses 30% of its CRAH cooling capacity')
  await sleep(2500)
  await b.clickButton('Inject Cooling Failure')
  await b.waitFor(`!!document.querySelector('.fsm-node.current')`)
  await caption('The Coordinator agent takes over', 'It reads the twin, delegates to Thermal, Power and Scheduling agents, and records every step')
  await b.waitFor(`document.body.innerText.includes('Safety verdict: REJECT')`, RUN_TIMEOUT_MS)
  await caption('Plan 1 REJECTED by the independent Safety agent',
    'Moving job-06 to the coolest rack would exceed rack-07\'s 6 kW PDU and its GPUs lack 120 GB memory')
  await sleep(1500)
  await b.waitFor(DONE, RUN_TIMEOUT_MS)
  await caption('Recursive retry → VERIFIED', 'The failure was stored, constraints were learned, and plan 2 (job-06 → rack-06) passed every hard check')
  await sleep(3500)
  await scroll(380, 2000)
  await caption('Plan attempts and delegation tree', 'Rejected plans become tabu · learned constraint families: rack_power, memory')
  await sleep(4500)

  await tab('Results')
  await caption('Before / after — computed from the simulator, not hard-coded',
    'Violations 2 → 0 · max outlet 47.3 → 41.9 °C · energy 341.2 → 339.5 kWh · no deadline misses')
  await sleep(5500)
  await tab('Digital Twin')
  await caption('The twin after recovery', 'job-06 now runs on rack-06; rack-04 is cool again even with degraded cooling')
  await sleep(4500)
  await tab('Memory')
  await caption('SQLite memory', 'Every run, plan, verdict and rejection reason is persisted and can be replayed')
  await sleep(4000)
  await tab('Benchmarks')
  await caption('Reproducible benchmark suite', 'Normal · cooling failure · power constraint · deadline · infeasible')
  await b.clickButton('Run benchmarks')
  await b.waitFor(`document.body.innerText.includes('Download JSON')`, RUN_TIMEOUT_MS)
  await sleep(4500)

  await resetScenario('infeasible')
  await sleep(1800)
  await caption('When no safe plan exists, the agents stop and escalate', 'Infeasible scenario: pinned jobs on a rack that lost 60% of its cooling')
  await b.clickButton('Run agents')
  await b.waitFor(DONE, RUN_TIMEOUT_MS)
  await caption('ESCALATED to a human after 4 bounded attempts', 'No unsafe change is ever applied to the simulated data center')
  await sleep(4500)

  await card(`<div><h1>DataCenterOS AI</h1><p>Plan → verify → learn → replan, with hard safety checks in code</p>
    <p>LLM via Crusoe Managed Inference (mock mode shown when no API key is set)</p>
    <span class="tag">Simulation only · synthetic data · no real infrastructure controlled</span></div>`, 5000)
  await b.send('Page.stopScreencast')
} finally {
  b.close()
  await putConfig(originalConfig)
  await resetScenario('normal')
}

if (frames.length < 2) throw new Error('no frames captured')
const endT = frames[frames.length - 1].t + 1.5
const list = frames.map((f, i) => `file '${f.file}'\nduration ${((frames[i + 1]?.t ?? endT) - f.t).toFixed(3)}`).join('\n')
const listFile = join(FRAMES, 'list.txt')
writeFileSync(listFile, `${list}\nfile '${frames[frames.length - 1].file}'\n`)
execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', listFile,
  '-vf', 'fps=30,scale=1440:900:force_original_aspect_ratio=decrease,pad=1440:900:(ow-iw)/2:(oh-ih)/2,format=yuv420p',
  '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-color_range', 'tv', '-preset', 'medium', '-crf', '22',
  '-movflags', '+faststart', OUT])
console.log(`Recorded ${frames.length} frames over ${(endT - frames[0].t).toFixed(1)}s → ${OUT}`)
