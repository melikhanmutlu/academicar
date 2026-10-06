# AcademicAR launch videos

- `academicar_launch.mp4`: 74 s product launch video (1080p30, synthesized soundtrack).
- `academicar_showreel.mp4`: 60 s 120 BPM motion showreel focused on the viewer features.

## Rebuilding (`src/`)

Scenes are deterministic HTML/three.js pages (`index.html` + `app.js`, `reel.html` + `reel.js`)
that expose `window.renderAt(t)`. `render.mjs` drives headless Chromium frame by frame and pipes
JPEG frames to ffmpeg; `music.py` / `music2.py` synthesize the soundtracks with numpy.

```bash
cd src && npm i three@0.169.0 qrcode @fontsource/montserrat @fontsource/inter
mkdir -p assets/fonts   # copy calcaneus.glb, ar-phone-mockup.webp, use-*.png from static/, fonts from @fontsource
node -e "const Q=require('qrcode');const q=Q.create('https://academicar.com/m/7Q2KX9',{errorCorrectionLevel:'M'});require('fs').writeFileSync('assets/qr.json',JSON.stringify({n:q.modules.size,d:Array.from(q.modules.data)}))"
npx http-server -p 8123 -s -c-1 . &
for i in 0 1 2; do node render.mjs $i 3 & done; wait                                # launch video segments
for i in 0 1 2; do DUR=60 PAGE=reel.html PRE=rseg node render.mjs $i 3 & done; wait  # showreel segments
python3 music.py && python3 music2.py
```
Append `?t=12.5` to either page URL to preview a single frame.
