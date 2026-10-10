const { chromium } = require('playwright-core');

(async () => {
  const exe = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
  const browser = await chromium.launch({ executablePath: exe, args: ['--no-sandbox'] });
  const page = await browser.newPage({ viewport: { width: 1280, height: 820 }, deviceScaleFactor: 1 });
  const file = process.argv[2];
  await page.goto('file://' + file, { waitUntil: 'networkidle' });
  await page.waitForTimeout(300);
  // select the 3rd workflow (SeedVR2 · 标准 1080p) to open the detail panel
  await page.selectOption('#wfsel', 'sv_std');
  await page.waitForTimeout(400);
  // scroll stage to workflow card for capture
  await page.evaluate(() => document.querySelector('#secWorkflow').scrollIntoView({ block: 'start' }));
  await page.waitForTimeout(300);
  await page.screenshot({ path: process.argv[3], fullPage: false });
  console.log('shot saved:', process.argv[3]);
  await browser.close();
})();
