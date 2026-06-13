---
name: webapp-testing
description: Toolkit for interacting with and testing local web applications using Playwright. Supports verifying frontend functionality, debugging UI behavior, capturing browser screenshots, and viewing browser logs.
license: Complete terms in LICENSE.txt
---

# Web Application Testing

To test local web applications, write native Python Playwright scripts.

**Helper Scripts Available**:
- `scripts/with_server.py` - Manages server lifecycle (supports multiple servers)

**Always run scripts with `--help` first** to see usage. DO NOT read the source until you try running the script first and find that a customized solution is abslutely necessary. These scripts can be very large and thus pollute your context window. They exist to be called directly as black-box scripts rather than ingested into your context window.

## Decision Tree: Choosing Your Approach

```
User task → Is it static HTML?
    ├─ Yes → Read HTML file directly to identify selectors
    │         ├─ Success → Write Playwright script using selectors
    │         └─ Fails/Incomplete → Treat as dynamic (below)
    │
    └─ No (dynamic webapp) → Is the server already running?
        ├─ No → Run: python scripts/with_server.py --help
        │        Then use the helper + write simplified Playwright script
        │
        └─ Yes → Reconnaissance-then-action:
            1. Navigate and wait for networkidle
            2. Take screenshot or inspect DOM
            3. Identify selectors from rendered state
            4. Execute actions with discovered selectors
```

## Example: Using with_server.py

To start a server, run `--help` first, then use the helper:

**Single server:**
```bash
python scripts/with_server.py --server "npm run dev" --port 5173 -- python your_automation.py
```

**Multiple servers (e.g., backend + frontend):**
```bash
python scripts/with_server.py \
  --server "cd backend && python server.py" --port 3000 \
  --server "cd frontend && npm run dev" --port 5173 \
  -- python your_automation.py
```

To create an automation script, include only Playwright logic (servers are managed automatically):
```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True) # Always launch chromium in headless mode
    page = browser.new_page()
    page.goto('http://localhost:5173') # Server already running and ready
    page.wait_for_load_state('networkidle') # CRITICAL: Wait for JS to execute
    # ... your automation logic
    browser.close()
```

## Reconnaissance-Then-Action Pattern

1. **Inspect rendered DOM**:
   ```python
   page.screenshot(path='/tmp/inspect.png', full_page=True)
   content = page.content()
   page.locator('button').all()
   ```

2. **Identify selectors** from inspection results

3. **Execute actions** using discovered selectors

## Common Pitfall

❌ **Don't** inspect the DOM before waiting for `networkidle` on dynamic apps
✅ **Do** wait for `page.wait_for_load_state('networkidle')` before inspection

## Best Practices

- **Use bundled scripts as black boxes** - To accomplish a task, consider whether one of the scripts available in `scripts/` can help. These scripts handle common, complex workflows reliably without cluttering the context window. Use `--help` to see usage, then invoke directly. 
- Use `sync_playwright()` for synchronous scripts
- Always close the browser when done
- Use descriptive selectors: `text=`, `role=`, CSS selectors, or IDs
- Add appropriate waits: `page.wait_for_selector()` or `page.wait_for_timeout()`

## Reference Files

- **examples/** - Examples showing common patterns:
  - `element_discovery.py` - Discovering buttons, links, and inputs on a page
  - `static_html_automation.py` - Using file:// URLs for local HTML
  - `console_logging.py` - Capturing console logs during automation

## AcademicAR project notes

Specifics for testing/screenshotting *this* Flask app:

1. **Preview data + server.** Seed a throwaway SQLite DB with sample papers/models, then launch the app pointed at it. Keep scratch scripts under `tests_runtime/` (gitignored). `config.py` reads these env vars: `DATABASE_URL` (e.g. `sqlite:///.../preview.db`), `CONVERTED_FOLDER`, `QR_FOLDER`, `UPLOAD_FOLDER`, `PDF_FOLDER`, `BLOG_IMAGE_FOLDER`, plus `SECRET_KEY`, `RATELIMIT_STORAGE_URI=memory://`, `PORT`. `python app.py` then serves on `$PORT`.
2. **Auth.** `/dashboard` and `/papers/<slug>` require login: open `/auth/login`, fill `input[name="email"]` + `input[name="password"]`, submit. Submitting the rendered form makes Playwright carry the CSRF token automatically; reuse the same `BrowserContext` so the session cookie persists for later pages.
3. **Browser on this managed container.** Playwright's own browser download may be blocked, but a Chromium is preinstalled. Launch with `executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome", args=["--no-sandbox"]` (adjust the rev to whatever is under `/opt/pw-browsers`).
4. **Scroll-reveal caveat.** The landing page and several others reveal content on scroll (IntersectionObserver). A `full_page=True` screenshot at `scrollTo(0,0)` shows below-the-fold sections **blank** — scroll through in ~600px steps with short waits before capturing, or you will misread revealed content as a broken/empty layout.
5. **Viewports.** Capture both desktop (`1440×900`) and mobile (`390×844`, `is_mobile=True`) to catch responsive issues.