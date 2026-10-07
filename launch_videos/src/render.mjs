import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs';
import { spawn } from 'node:child_process';
const [w, W] = process.argv.slice(2).map(Number);
const FPS = 30, DUR = +(process.env.DUR||74), N = FPS * DUR;
let a = Math.floor(N * w / W), b = Math.floor(N * (w + 1) / W);
if (process.env.RANGE) [a, b] = process.env.RANGE.split('-').map(Number);
const ff = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(FPS), '-c:v', 'mjpeg', '-i', '-',
  '-c:v', 'libx264', '-preset', 'slow', '-crf', '16', '-pix_fmt', 'yuv420p', '-r', String(FPS), `${process.env.PRE||'seg'}${w}.mp4`], { stdio: ['pipe', 'inherit', 'inherit'] });
const br = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
const p = await br.newPage({ viewport: { width: 1920, height: 1080 } });
p.on('pageerror', (e) => console.log('pageerror', e.message));
await p.goto('http://127.0.0.1:8123/'+(process.env.PAGE||'index.html')+'');
await p.evaluate(() => window.ready);
const t0 = Date.now();
for (let f = a; f < b; f++) {
  await p.evaluate((t) => window.renderAt(t), f / FPS);
  const buf = await p.screenshot({ type: 'jpeg', quality: 96 });
  if (!ff.stdin.write(buf)) await new Promise((r) => ff.stdin.once('drain', r));
  if ((f - a) % 60 === 0) console.log(`w${w} ${f - a}/${b - a} ${((Date.now() - t0) / 1000).toFixed(0)}s`);
}
ff.stdin.end(); await new Promise((r) => ff.on('close', r)); await br.close();
console.log(`w${w} done`);
