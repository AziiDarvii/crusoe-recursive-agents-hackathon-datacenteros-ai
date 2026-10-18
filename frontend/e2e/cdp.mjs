// Minimal Chrome DevTools Protocol driver using Node's built-in WebSocket (no npm dependencies).
import { spawn } from 'node:child_process'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

export const BASE = process.env.DCOS_URL ?? 'http://localhost:5173'
const CHROME = process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
export const WIDTH = 1440
export const HEIGHT = 900
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

export async function resetScenario(scenario = 'normal') {
  const res = await fetch(`${BASE}/api/reset`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scenario }),
  })
  if (!res.ok) throw new Error(`reset failed: ${res.status}`)
}

export async function launch({ port = 9333 } = {}) {
  const chrome = spawn(CHROME, ['--headless=new', '--disable-gpu', '--hide-scrollbars', `--remote-debugging-port=${port}`,
    `--user-data-dir=${mkdtempSync(join(tmpdir(), 'dcos-cdp-'))}`, `--window-size=${WIDTH},${HEIGHT}`, 'about:blank'],
  { stdio: 'ignore' })
  let wsUrl
  for (let i = 0; i < 50 && !wsUrl; i++) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json()
      wsUrl = targets.find((t) => t.type === 'page')?.webSocketDebuggerUrl
    } catch { /* chrome still starting */ }
    if (!wsUrl) await sleep(200)
  }
  if (!wsUrl) { chrome.kill(); throw new Error('Chrome DevTools endpoint did not come up') }

  const ws = new WebSocket(wsUrl)
  await new Promise((r) => ws.addEventListener('open', r, { once: true }))
  let nextId = 0
  const pending = new Map()
  const listeners = new Map()
  const errors = []
  ws.addEventListener('message', ({ data }) => {
    const msg = JSON.parse(data)
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id) }
    if (msg.method === 'Runtime.exceptionThrown') errors.push(msg.params.exceptionDetails.text)
    if (msg.method) (listeners.get(msg.method) ?? []).forEach((fn) => fn(msg.params))
  })
  const send = (method, params = {}) => new Promise((res, rej) => {
    const id = ++nextId
    pending.set(id, (m) => (m.error ? rej(new Error(`${method}: ${m.error.message}`)) : res(m.result)))
    ws.send(JSON.stringify({ id, method, params }))
  })
  const on = (method, fn) => listeners.set(method, [...(listeners.get(method) ?? []), fn])
  const evaluate = async (expr) => (await send('Runtime.evaluate', { expression: expr, returnByValue: true })).result.value
  const waitFor = async (expr, timeout = 10000) => {
    const end = Date.now() + timeout
    while (Date.now() < end) { if (await evaluate(expr)) return; await sleep(200) }
    throw new Error(`timed out waiting for: ${expr}`)
  }
  const viewport = (height = HEIGHT) =>
    send('Emulation.setDeviceMetricsOverride', { width: WIDTH, height, deviceScaleFactor: 1, mobile: false })
  const fullShot = async (path) => {
    const { cssContentSize } = await send('Page.getLayoutMetrics')
    await viewport(Math.ceil(cssContentSize.height))
    const { data } = await send('Page.captureScreenshot', { format: 'png' })
    writeFileSync(path, Buffer.from(data, 'base64'))
    await viewport()
  }
  const clickButton = (label) => evaluate(`(() => { const b = [...document.querySelectorAll('button')]
    .find((x) => x.textContent.trim().startsWith(${JSON.stringify(label)})); if (b) b.click(); return !!b })()`)

  await send('Page.enable')
  await send('Runtime.enable')
  await viewport()
  const close = () => { ws.close(); chrome.kill() }
  return { send, on, evaluate, waitFor, fullShot, clickButton, errors, close }
}
