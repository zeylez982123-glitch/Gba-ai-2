// Loads the APK's index.html in Chromium with a mock of the Android bridge (window.Jarvis / window.JarvisVoice).
// Network calls the page makes through Jarvis.http are answered by window.__route(method, url, body) set per test.
const { chromium } = require('playwright');
const path = require('path');

const MOCK = `
(() => {
  const store = {}, calls = [];
  window.__calls = calls; window.__store = store;
  window.__route = () => ({status: 0, body: 'no route'});
  window.__browser = {open: false, url: '', run: s => s.includes('/watch?v=') ? 'https://m.youtube.com/watch?v=abc' : null};
  window.Jarvis = {
    get: k => (k in store ? store[k] : ''),
    set: (k, v) => { calls.push(['set', k, v]); store[k] = v; },
    http: (id, method, url, headers, body, timeout) => {
      calls.push(['http', method, url, headers, body]);
      Promise.resolve().then(() => window.__route(method, url, body ? JSON.parse(body) : null, JSON.parse(headers)))
        .then(r => setTimeout(() => window.__httpDone(id, r.status, typeof r.body === 'string' ? r.body : JSON.stringify(r.body)), 5));
    },
    timer: (sec, label) => { calls.push(['timer', sec, label]); return 'ok'; },
    alarm: (h, m, text) => { calls.push(['alarm', h, m, text]); return 'ok'; },
    volume: p => { calls.push(['volume', p]); return 'ok'; },
    torch: on => { calls.push(['torch', on]); return 'ok'; },
    openApp: name => { calls.push(['openApp', name]); return /^(chrome|spotify|camera)$/.test(name.toLowerCase()) ? 'Opened ' + name + '.' : 'Error: there is no app called ' + name + ' on this phone.'; },
    battery: () => JSON.stringify({level: 76, charging: false}),
    browserOpen: url => { calls.push(['browserOpen', url]); window.__browser.open = true; window.__browser.url = url; return 'ok'; },
    browserClose: () => { calls.push(['browserClose']); window.__browser.open = false; return 'ok'; },
    browserBadge: t => 'ok',
    browserRun: (id, script) => { calls.push(['browserRun', script.slice(0, 60)]);
      setTimeout(() => window.__browserDone(id, window.__browser.open ? window.__browser.run(script) : null), 5); return 'ok'; },
    popup: () => 'no', openLaptop: () => 'ok', vibrate: () => 'ok',
  };
  window.JarvisVoice = {speak: (t) => calls.push(['speak', t]), stop: () => {}, listen: () => {}, voices: () => '[]', useVoice: () => {}, ready: () => true};
})();`;

async function open(opts = {}) {
  const browser = opts.browser || await chromium.launch();
  const ctx = await browser.newContext({viewport: opts.viewport || {width: 412, height: 860}, deviceScaleFactor: 1});
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errors.push(m.text()); });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, r => r.abort());
  if (!opts.noBridge) await page.addInitScript(MOCK);
  if (opts.store) await page.addInitScript(s => { for (const [k, v] of Object.entries(s)) window.__store[k] = v; }, opts.store);
  if (opts.local) await page.addInitScript(s => { for (const [k, v] of Object.entries(s)) localStorage.setItem(k, v); }, opts.local);
  await page.goto('file://' + path.resolve(process.argv[2]));
  await page.waitForFunction(() => /All systems online|Alla system/.test(document.querySelector('#out').textContent), null, {timeout: 3000}).catch(() => {});
  return {browser, ctx, page, errors};
}
module.exports = {open};
