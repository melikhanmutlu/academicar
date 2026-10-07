import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs';
const ts = process.argv.slice(2).map(Number);
const b = await chromium.launch({ args: ['--use-angle=swiftshader','--enable-unsafe-swiftshader','--ignore-gpu-blocklist'] });
const p = await b.newPage({ viewport: { width: 1920, height: 1080 } });
p.on('console', m => { if (m.type()==='error'||m.type()==='warning') console.log('console:', m.text()); });
p.on('pageerror', e => console.log('pageerror:', e.message));
await p.goto('http://127.0.0.1:8123/index.html');
await p.evaluate(() => window.ready);
for (const t of ts) { const s=Date.now(); await p.evaluate(t => window.renderAt(t), t); await p.screenshot({ path: `shots/t${t}.jpg`, type:'jpeg', quality:80 }); console.log(t, Date.now()-s,'ms'); }
await b.close();
