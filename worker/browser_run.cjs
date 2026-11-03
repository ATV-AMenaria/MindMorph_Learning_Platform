// Trusted real-browser observer. Learner code runs only inside Chromium.
const fs = require('node:fs');
const { chromium } = require('playwright-core');
let browser;
const main = async () => {
  const raw = fs.readFileSync(0, 'utf8');
  const task = raw.trim() ? JSON.parse(raw) : { actions: [], queries: [{ selector: '#root', read: 'text' }] };
  const actions = task.actions || [], queries = task.queries || [], mocks = task.mocks || {};
  if (!Array.isArray(actions) || actions.length > 12 || !Array.isArray(queries) || queries.length > 20 || JSON.stringify(mocks).length > 65536) throw Error('Invalid browser input');
  try {
    browser = await chromium.launch({ executablePath: '/usr/bin/chromium', headless: true, chromiumSandbox: true,
      timeout: 10000, args: ['--disable-dev-shm-usage', '--disable-gpu'] });
  } catch (error) { process.stderr.write('The sandboxed browser could not start: ' + String(error.message).slice(0, 3000)); process.exit(70); }
  const context = await browser.newContext({ viewport: { width: 800, height: 600 }, serviceWorkers: 'block', acceptDownloads: false });
  const page = await context.newPage();
  page.setDefaultTimeout(1800);
  const errors = [];
  page.on('pageerror', error => errors.push(String(error.message).slice(0, 2000)));
  await context.route('**/*', async route => {
    const url = route.request().url();
    if (url === 'http://mindmorph.invalid/') return route.fulfill({ contentType: 'text/html', body: '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Practice</title></head><body><div id="root"></div><script src="/bundle.js"></script></body></html>' });
    if (url === 'http://mindmorph.invalid/bundle.js') return route.fulfill({ contentType: 'application/javascript', body: fs.readFileSync('/work/.mm-bundle.js') });
    const path = url.startsWith('http://mindmorph.invalid/api/') ? new URL(url).pathname : null;
    if (path && Object.hasOwn(mocks, path)) return route.fulfill({ json: mocks[path] });
    return route.abort('blockedbyclient');
  });
  await page.goto('http://mindmorph.invalid/', { waitUntil: 'load', timeout: 8000 });
  await page.locator('#root > *').first().waitFor({ state: 'attached' });
  const locate = item => {
    if (typeof item.role === 'string' && typeof item.name === 'string') return page.getByRole(item.role, { name: item.name, exact: true });
    if (typeof item.selector !== 'string' || item.selector.length > 300) throw Error('A bounded selector or role/name is required');
    return page.locator(item.selector);
  };
  for (const action of actions) {
    if (action.type === 'wait') {
      if (!Number.isInteger(action.ms) || action.ms < 0 || action.ms > 1000) throw Error('Invalid wait');
      await page.waitForTimeout(action.ms); continue;
    }
    const locator = locate(action);
    if (action.type === 'click') await locator.click();
    else if (action.type === 'fill' && typeof action.value === 'string' && action.value.length <= 2000) await locator.fill(action.value);
    else if (action.type === 'check') await locator.check();
    else if (action.type === 'press' && ['Enter', 'Space', 'Tab', 'ArrowDown', 'ArrowUp', 'Escape'].includes(action.key)) await locator.press(action.key);
    else throw Error('Unsupported action');
  }
  const results = [];
  for (const query of queries) {
    const locator = locate(query);
    if (query.read === 'count') results.push(await locator.count());
    else if (query.read === 'visible') results.push(await locator.isVisible());
    else if (query.read === 'value') results.push(await locator.inputValue());
    else if (query.read === 'attribute' && typeof query.attribute === 'string' && /^[a-zA-Z][a-zA-Z0-9-]{0,80}$/.test(query.attribute)) results.push(await locator.getAttribute(query.attribute));
    else if (query.read === 'aria') results.push(await locator.ariaSnapshot());
    else if (query.read === 'text' || !query.read) results.push(await locator.innerText());
    else throw Error('Unsupported query');
  }
  // Allow queued rejected promises and React commit errors to reach the observer.
  await page.waitForTimeout(30);
  if (errors.length) throw Error('Browser runtime error: ' + errors[0]);
  await browser.close(); browser = null;
  process.stdout.write(JSON.stringify(results) + '\n');
};
main().catch(async error => { if (browser) await browser.close().catch(() => {}); process.stderr.write(String(error.message).slice(0, 60000)); process.exit(1); });
