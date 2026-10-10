const { chromium } = require('playwright-core');
const path = require('path');

(async () => {
  const exe = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
  const browser = await chromium.launch({ executablePath: exe, args: ['--no-sandbox'] });
  const page = await browser.newPage({ viewport: { width: 1280, height: 820 }, deviceScaleFactor: 1 });
  const file = process.argv[2];
  await page.goto('file://' + file, { waitUntil: 'networkidle' });
  await page.waitForTimeout(400);
  const out = process.argv[3] || 'shot.png';
  await page.screenshot({ path: out, fullPage: false });
  console.log('shot saved:', out);
  await browser.close();
})();
