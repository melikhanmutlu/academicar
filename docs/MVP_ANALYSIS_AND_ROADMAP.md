# AcademicAR — MVP Analysis & Roadmap

> Comprehensive analysis covering technical readiness, debugging findings, payment
> integration, content (blog/FAQ), marketing topics, market & investor landscape,
> business model, growth, and SEO — plus a prioritized roadmap and the work
> delivered in the first implementation pass.
>
> Status legend: **P0** = launch blocker, **P1** = growth/maturity, **P2** = platform/scale.

---

## 1. Executive Summary

AcademicAR is a Flask SaaS that lets academics upload 3D models (GLB/STL/OBJ/FBX),
auto-converts and optimizes them to web GLB (Draco + compressed textures, USDZ for
iOS AR), generates a **stable QR code**, and serves an interactive 3D + AR viewer
(Google `model-viewer`). The product is ~90% feature-complete (auth, publications,
upload→worker conversion, per-model licensing, public viewer, QR resolver, admin,
audit, backups). The gap to a real public launch is **commercial and
infrastructural**, not feature depth.

**Top 6 launch blockers (P0):**
1. **Durable file storage** — files live only on an ephemeral Railway volume; a
   restart/redeploy can lose them, and multi-instance is impossible. Needs S3/R2.
2. **Real payments** — no gateway today (placeholder `Payment` + dev bypass).
3. **Password reset** — no "forgot password" flow exists.
4. **Email verification & deliverability** — registration has no verification; SMTP
   must be configured in production.
5. **Production config hardening** — `SECRET_KEY`, Redis-backed rate limiting, Sentry.
6. **SEO foundation** — robots/sitemap/canonical/JSON-LD (delivered in pass 1, §11).

**Strategic insight:** the technical core (conversion, QR, viewer) is not durably
defensible (Google model-viewer is free; Sketchfab/Fab exists). Defensibility comes
from **institutional/publisher integration and network effects**. Current pricing
($9.90/3y, $24.90/10y) is effectively one-time, which is weak for ARR and investors;
evolving to **freemium + annual + institutional** is recommended (§3.3, §7).

---

## 2. Technical & Debugging Findings

### 2.1 Architecture (as built)
- Flask 3 factory (`create_app`) + SQLAlchemy + Jinja2 + Tailwind (CDN); web + worker
  in one container (`railway.json`), Redis for rate limits, persistent volume for files.
- 60+ routes registered in `register_routes(app)`; ownership via
  `require_paper_ownership` / `require_model_ownership` (`utils/security.py`).
- Conversion is queued only by the web process (`ConversionJob`); `worker.py` polls and
  runs STL/OBJ/FBX→GLB, optimization (`finalize_converted_glb`), USDZ, QR, poster.
  Atomic replace via `.new.glb` → `os.replace`.
- Licensing (`licensing.py`): `free` ($0/3d/100MB), `academic` ($9.90/3y/200MB),
  `extended_archive` ($24.90/10y/200MB), `institutional` ($0/unlimited/500MB, admin-only).
  `apply_model_license_defaults(model, plan)` recomputes expiry/limits idempotently.

### 2.2 Findings (with severity, location, fix)

| ID | Severity | Finding | Location | Fix |
|---|---|---|---|---|
| AUTH-1 | High | No email verification at registration | `auth.py` register flow | Token + `email_confirmed`; reuse `utils/email.send_email` |
| AUTH-2 | High | No password-reset / "forgot password" flow | `auth.py`, `app.py` | itsdangerous token + email link |
| STOR-1 | High | Files only on ephemeral Railway volume; lost on restart; no multi-instance | `services/storage_service.py`, `storage.py` | S3/Cloudflare R2 backend + presigned serve |
| PAY-1 | High | No real payment gateway (placeholder + dev bypass) | `config.py:127`, `app.py` profile | MoR webhook → `apply_model_license_defaults` (delivered: skeleton, §11) |
| MAIL-1 | Med | If SMTP unset, email is logged not sent; email-change confirm breaks in prod | `utils/email.py` | Require transactional provider in prod + health check |
| SEC-1 | Med | Symlink in `CONVERTED_FOLDER` could serve arbitrary files | `app.py` file serve | `realpath` root containment check |
| SEC-2 | Med | QR image endpoint unauthenticated; model_id enumeration | `/qr-image/<model_id>` | Public by design; rate-limit + only public/active models |
| WORK-1 | Low (prod) | SQLite ignores `FOR UPDATE SKIP LOCKED` → double-claim with >1 worker | `app.py` job claim | Prod uses Postgres; document single-worker on SQLite |
| BUG-1 | Low | Profile plan counts use legacy `User.plan` | `app.py` profile stats | Count `Model3D.license_type` distribution |
| BUG-2 | Low | Audit-log failures silently swallowed (`pass`) | `auth.py` | Log a warning so attacks are visible |
| BUG-3 | Low | "Expiring soon" computed with manual, tz-fragile datetime math | `app.py` profile stats | ORM query + tz-aware compare |
| TZ-1 | Low | Naive datetime assumed UTC | `licensing.py` | Guaranteed by `utc_now`; keep aware everywhere |

### 2.3 Production hardening
- Set `SECRET_KEY` (never ship the `dev-secret-...` default).
- `RATELIMIT_STORAGE_URI` → `REDIS_URL` (memory:// is unsafe across processes).
- Move storage to S3/R2 (STOR-1); add Sentry + structured logging (`/health` exists).
- Keep `ALLOW_DEV_PAYMENTS=0` in production (already the default).

---

## 3. Payment Integration

### 3.1 Provider comparison (decision-ready)

| Provider | Merchant of Record? | Turkey seller | Fees | Global VAT/invoices | Notes |
|---|---|---|---|---|---|
| **LemonSqueezy** | ✅ | Likely (verify) | ~5% + $0.50 | **Handled** | Built-in invoices + license keys; REST API |
| **Paddle** | ✅ | Yes (verify) | ~5% + $0.50 | **Handled** | Mature, Python SDK; no license keys |
| Polar.sh | ✅ | Unclear | 5% + $0.50 | Handled | Open-source; verify Turkey |
| Stripe | ❌ gateway | **No (direct)** | 2.9% + $0.30 | On you (+Stripe Tax) | Turkey can't open an account directly |
| iyzico | ❌ gateway | ✅ excellent | ~2–3% | **On you** | Installments + TRY payout; no invoices/subscriptions |
| PayTR | ❌ gateway | ✅ excellent | ~2.5–4% | **On you** | Similar to iyzico |
| Gumroad | ✅ | Likely | 10% + $0.50 | Handled | Fees too high for these price points |

### 3.2 Recommendation
Given the **global / English-first** decision, a **Merchant of Record** is the
pragmatic choice: a small Turkey-based company should not register for and remit VAT
in dozens of jurisdictions. **Primary: LemonSqueezy** (auto tax + invoices academics
need for reimbursement + low fees). **Fallback: Paddle**. Stripe is not viable from
Turkey directly; iyzico/PayTR only make sense for a Turkey-first audience (you carry
the global tax/invoice burden).

> **Action (founder):** get written confirmation from LemonSqueezy and Paddle support
> that a **Turkey-based seller** is supported and what payout currency/entity is
> required. The skeleton is provider-agnostic so the concrete provider can be chosen
> later by filling config + one adapter method.

> **Architecture note:** LemonSqueezy "license keys" are **not** needed — the app
> already has per-model licensing. On a paid webhook we simply call the existing
> `apply_model_license_defaults(model, plan)`.

### 3.3 Pricing model — analysis & recommendation (founder decides)
- The current $9.90/3y and $24.90/10y are effectively **one-time** purchases → no ARR,
  no SaaS retention metrics, hard to fundraise on.
- **Recommended evolution:** Free (1 model/yr, watermarked, public URL = SEO asset) →
  **Pro (annual)** (N models/yr, no watermark, persistent QR/URL, analytics, DOI/ORCID) →
  **Extended Archive** ($24.90 add-on, 10-year preservation) → **Institutional** (annual
  $5K–$50K, unlimited + white-label + API + SSO). Institutional is where durable ARR and
  defensibility live.
- The payment skeleton supports both one-time and recurring models.

### 3.4 Integration steps (skeleton → live)
- **Skeleton (delivered, §11.B):** provider-agnostic adapter, webhook route,
  `Payment.model_id`, payment→license assignment, idempotency, tests.
- **Go-live (after provider chosen):**
  1. Create products/prices; map `plan → variant/price id` in config.
  2. Implement the adapter's `create_checkout` (hosted checkout) using the live API.
  3. Set `PAYMENT_PROVIDER`, API key, and webhook secret in env.
  4. Sandbox test end-to-end (ngrok for webhooks), duplicate-event idempotency, refunds.
  5. `Payment.currency` defaults to USD; amounts stored in minor units.
  6. Invoices are auto-sent by the MoR; admin payment views already exist.

---

## 4. Blog & FAQ / About / Contact

Information architecture: `/blog`, `/blog/<slug>`, `/faq`, `/about`, `/contact`, plus
discipline landing pages `/ar-for-<field>` (§9.2).

- **/faq, /about, /contact** — delivered in pass 1 (§11.A), on-brand (Ventriloc), with
  FAQPage JSON-LD on `/faq` and a rate-limited contact form using `send_email`.
- **Blog infrastructure** is a larger build (markdown-file or `BlogPost` model + editor):
  **roadmap P1**, not pass 1. Pass 1 ships the SEO foundation + content plan (§5).
- All new pages preserve existing flows and the Ventriloc design (no new visual system).

---

## 5. Marketing Blog Content Plan (27 topics)

Content pillars: (1) Interactive 3D in academic publishing, (2) AR in education &
teaching, (3) QR codes for academic engagement, (4) Per-discipline "AR for [field]".

> Titles/keywords in English (global SEO). Columns: keyword · persona · intent · funnel.

| # | Working title | Primary keyword | Persona | Intent | Funnel |
|---|---|---|---|---|---|
| 1 | The Future of Interactive 3D in Scientific Publishing (2026) | 3d models scientific publishing | Researcher/Editor | Info | TOFU |
| 2 | How AR Is Transforming Anatomy Education | ar anatomy teaching | Med educator | Info | TOFU |
| 3 | Why Interactive 3D Figures Matter in Papers | interactive 3d figures journal | SciComm | Info | TOFU |
| 4 | 8 Technologies Reshaping Research Presentations | academic presentation technology | Academic | Info | TOFU |
| 5 | Why Conference Posters Need Interactive Elements | interactive conference poster | PhD student | Info | TOFU |
| 6 | Museum Curation 2026: Digital 3D Models | museum 3d models digital curation | Curator | Info | TOFU |
| 7 | Complete Guide to QR Codes on Academic Posters | qr code academic poster guide | Academic | How-to | TOFU |
| 8 | Reproducibility: Why 3D Models Matter for Open Research | 3d models open science | Researcher | Info | TOFU |
| 9 | DIY 3D Viewers vs Purpose-Built Platforms | 3d model viewer comparison | Creator | Comparison | MOFU |
| 10 | Choosing the Right 3D File Format (GLB/STL/GLTF/OBJ) | 3d file formats comparison | Technical | How-to | MOFU |
| 11 | Adding 3D Models to Your Thesis (Step-by-Step) | add 3d models thesis pdf | PhD student | How-to | MOFU |
| 12 | AR Anatomy Lab Solutions Compared | ar anatomy platforms comparison | Med educator | Comparison | MOFU |
| 13 | 5 Ways Chemistry Profs Use 3D Molecular Models | chemistry 3d molecular education | Chem educator | Info/How-to | MOFU |
| 14 | QR + 3D: Fully Interactive Conference Posters | interactive qr 3d poster | Presenter | How-to | MOFU |
| 15 | Building an Open-Access 3D Repository for Your Lab | 3d model repository lab | Lab PI | How-to | MOFU |
| 16 | Engaging Gen-Z STEM Students with 3D & AR | engaging stem students 3d ar | Educator | Info | MOFU |
| 17 | Case Study: 3D Figures & Citation Rates | 3d figures research citations | Researcher/Editor | Validation | BOFU |
| 18 | Med School Case Study: AR Cuts Anatomy Lab Costs | ar anatomy lab roi | Admin | ROI | BOFU |
| 19 | Why Museums Digitize Collections: ROI Analysis | museum 3d collection roi | Museum director | Decision | BOFU |
| 20 | Build vs Buy: Interactive 3D for Your Lab | 3d tool build vs buy | Lab manager | Decision | BOFU |
| 21 | AcademicAR vs [Competitor]: Feature & Price | academicar alternative | Researcher | Comparison | BOFU |
| 22 | Getting Your Department to Approve 3D/AR Tools | institutional adoption 3d ar | Dept chair | How-to | BOFU |
| 23 | Expert Interviews: 3D to Amplify Research Impact | research impact 3d models | Researcher | Validation | BOFU |
| 24 | Integrating 3D into Your Publication Workflow | 3d models publication workflow | Editor/Researcher | How-to | BOFU |
| 25 | AcademicAR Customer Success Stories | academicar case studies | Institution | Validation | BOFU |
| 26 | AR for Archaeology: Reconstructing the Past in 3D | ar archaeology reconstruction | Archaeologist | Info | TOFU |
| 27 | Geology in 3D: Teaching Earth Science with AR | geology 3d visualization | Earth scientist | Info | MOFU |

Per-article template: H1 + meta title (≤60ch) + description (≤160ch) + BlogPosting
JSON-LD + 2–3 internal links + 1 (late) product CTA + BreadcrumbList. Cadence: 2–3/month.

---

## 6. Market, Market Share & Investors

### 6.1 Competitors (summary)
- **Closest (academic 3D):** MorphoSource (free natural-history repo), Morphobank
  (phylogenetics), Smithsonian/Sketchfab (heritage, CC0). None combine
  **monetization + QR + AR + publication integration** — AcademicAR's gap to fill.
- **General 3D/AR (indirect):** Sketchfab/Fab (Epic), Google model-viewer (free OSS),
  Figshare (data repo, not 3D-optimized), Plattar/Vectary/Echo3D/Augment
  (enterprise/design), p3d.in, iCn3D/Jmol (molecular).
- **Opening:** Adobe Aero shuts down Dec 2025 → gap in consumer AR tools.
- **Risk:** the core is easy to replicate; Figshare/Sketchfab could add an academic tier.

### 6.2 Market sizing (sourced, approximate)
- AR in Education (AR/VR): ~$25.5B–$51.3B (2025), CAGR ~16–48% (wide source range).
- 3D scanning/digitization: ~$5.7B–$8B (2025), CAGR ~8–11%.
- STM (academic) publishing: ~$34B–$37B (2025/26), CAGR ~3–8%.
- **AcademicAR SAM (interactive 3D in publishing):** realistically **$0.5M–$2M ARR by
  2028** — niche but growing; small-TAM caveat applies (defensibility is essential).

### 6.3 Investor angle
- **Position:** "platform play in academic publishing infrastructure" (like Overleaf for
  LaTeX, Figshare for data) + "pick-and-shovel for the AR shift".
- **Comparables:** Sketchfab raised ~$11.6M (seed+A) before Epic acquisition; Amboss (med
  edtech) $260M Series B (outlier); most 3D software is <$10M Series A.
- **Series A bar:** ~$1.5–3M ARR, 7–15% MoM growth, NRR 110–120%+, burn multiple <2.
- **Narrative:** institutional/publisher lock-in + marketplace network effects + academic
  metadata (ORCID/DOI/citations). "Why now": journals mandate data sharing; 3D scanning is
  cheap; AI generates 3D supply.

---

## 7. Business Model & Growth Ideas (7 pillars)
1. **Freemium per-model SaaS (revised):** Free → annual Pro → Extended add-on (§3.3); ~10% free→paid target.
2. **Institutional / library site licenses:** $10K–$100K/yr, white-label + API + SSO — primary ARR, low churn.
3. **Publisher / journal API & embed widget:** Figshare/OSF/Zenodo, eLife/Frontiers/PLOS; 70/30 revenue share; distribution + defensibility.
4. **Academic 3D marketplace:** researchers license models (CC-BY etc.); 20–30% take-rate.
5. **LMS / repo integrations:** Canvas/Blackboard/Moodle LTI; institutional distribution.
6. **AI 3D generation add-on:** Meshy/Tripo credits (10–50× margin).
7. **Freemium funnel & community:** QR viral loop (QR in PDFs → peer awareness), showcases, citation tracking, leaderboards.

12–24 month trajectory: 2026 PMF + 2–3 journals + 3–5 institutional pilots ($50K–150K
ARR) → 2027 scale ($600K–1.2M) → 2028 platform ($2.4M–6M).

---

## 8. Growth Tactics
- **Viral loop:** QR in publications → new users; institutional showcases; Google Scholar
  discovery via good metadata.
- **Field-specific marketing:** anatomy/paleontology/chemistry communities; conference
  booths (ACS, GSA, ASBMB) + targeted email to department heads.
- **Backlinks/PR (academic):** .edu partnerships + co-branded case studies, ResearchGate/
  Academia.edu profiles, arXiv white paper, EdTech/SciComm guest posts, podcasts.
- **Academic Twitter/X:** #AcademicTwitter #SciComm #HigherEd — authority, not hard sell.
- **Public viewer pages = SEO/distribution asset** (§9.3).
- **KPIs:** organic traffic, keyword rank, backlinks (DR30+), Core Web Vitals, free→paid, NRR.

---

## 9. SEO

### 9.1 Technical checklist
- **P0 (delivered, §11.A):** robots.txt, dynamic sitemap.xml (marketing pages + FAQ/About/
  Contact + public, non-deleted `/p/<slug>`), self-referencing canonical, JSON-LD
  (Organization + WebSite site-wide, FAQPage on /faq, SoftwareApplication on /pricing),
  per-page meta description + OG url/site_name.
- **P1:** BreadcrumbList + Article/BlogPosting on blog; image alt-text audit + WebP; Core
  Web Vitals (lazy-load model-viewer; consider a Tailwind build step to cut render-blocking).
- **P2:** `3DModel`/`CreativeWork` schema on public viewer pages; hreflang if Turkish added.

### 9.2 Programmatic discipline pages
`/ar-for-{anatomy,biology,chemistry,archaeology,geology,engineering,museums,stem-education}`
— template: why AR + use cases + tool (CTA) + 5–7 FAQ + related blog links. Captures "AR for
[field]" secondary keywords; acts as topic-cluster hubs.

### 9.3 Public viewer pages as SEO assets
Each public model page is a unique, indexable page: title/description from paper metadata,
`3DModel` schema, link back to the original article/DOI. Long-tail traffic + natural
backlinks (index only rich-metadata public publications to avoid thin content).

### 9.4 Core Web Vitals
LCP risk from 3D viewer + heavy images → lazy-load + CDN + WebP. INP → debounce/throttle
interactions. CLS < 0.1. Tailwind CDN → build step is a P1 win.

---

## 10. Other Recommendations
- **Analytics:** GA4 or privacy-friendly Plausible; events (upload, view, AR, QR scan).
- **Cookie consent:** policy exists but no banner; required (KVKK/GDPR) if non-essential cookies used.
- **Error monitoring:** Sentry + alerts on payment/worker failures.
- **Storage cleanup:** job to remove orphaned files for expired/deleted publications.
- **i18n readiness:** not needed now (global-English); later Flask-Babel + hreflang.
- **Backups:** existing backup system; add off-site once on S3/R2.
- **Legal:** add MoR seller mention + refund policy to Terms.

---

## 11. Delivered in Pass 1 (this implementation)

All additive and low-risk; existing flows (auth, upload, viewer, QR, mandatory consent)
and the Ventriloc design are preserved.

### 11.A SEO / technical quick wins
- `GET /robots.txt` — allows public pages; disallows admin/dashboard/account/auth/papers/
  models/payment; references the sitemap (built from `SITE_URL`).
- `GET /sitemap.xml` — marketing pages + FAQ/About/Contact + public, non-deleted
  `/p/<slug>` publications (reuses `url_helpers.public_url`).
- Canonical + JSON-LD in `templates/base.html` (self-referencing canonical via
  `canonical_url()`; Organization + WebSite site-wide; overridable `meta_description`,
  `canonical` blocks; OG `url`/`site_name`).
- `/faq` (FAQPage JSON-LD, single source `FAQ_ITEMS`), `/about`, `/contact` (CSRF + rate
  limited, sends via `utils/email.send_email`); footer links to all four.
- `SoftwareApplication` JSON-LD on `/pricing`.

### 11.B Payment skeleton (provider-agnostic)
- `Payment.model_id` FK (migration `d4e5f6a7b8c9` + idempotent SQLite ALTER in
  `ensure_sqlite_schema`).
- `payments.py`: `PaymentProvider` interface + `DevProvider` (settles instantly) +
  `LemonSqueezyProvider` reference skeleton (signature verification implemented;
  `create_checkout` is a documented TODO); `get_payment_provider()` is config-driven.
- `POST /models/<model_id>/upgrade/<plan>` (login + ownership + rate limit): creates a
  pending `Payment` (reusing `build_invoice_number`), then provider returns a checkout URL.
- `POST /payment/webhook/<provider>` (CSRF-exempt): verifies signature, then
  `apply_successful_payment` → `apply_model_license_defaults`. **Idempotent** via
  `provider_reference`; forged plans restricted to user-buyable paid plans.
- Config: `PAYMENT_PROVIDER`, `PAYMENT_CURRENCY` (USD), LemonSqueezy keys/variants
  (`config.py`, `.env.example`).
- Tests: `tests/test_payments.py` (license assignment, ownership, plan rejection, webhook
  idempotency, unpaid/unknown-provider) and `tests/test_seo.py` (robots, sitemap, canonical,
  JSON-LD, FAQ/About/Contact). Full suite green.

---

## 12. Prioritized Roadmap

### P0 — Launch blockers
- [ ] S3/Cloudflare R2 storage backend + presigned serve (STOR-1)
- [ ] Choose MoR (LemonSqueezy/Paddle) + implement `create_checkout` + sandbox test (PAY-1)
- [x] Payment → license assignment via webhook + idempotency (skeleton, §11.B)
- [ ] Password-reset flow (AUTH-2)
- [ ] Email verification at registration + production SMTP (AUTH-1, MAIL-1)
- [ ] Config hardening: SECRET_KEY, Redis rate limit, Sentry
- [x] SEO foundation: robots.txt + sitemap.xml + canonical + JSON-LD (§11.A)

### P1 — Growth & maturity
- [ ] Blog infrastructure (`/blog`, `/blog/<slug>`) + first 8–10 articles (§5)
- [x] FAQ + About + Contact pages (§11.A)
- [ ] Discipline landing pages `/ar-for-*` (§9.2)
- [ ] Pricing evolution: Free + annual Pro + Institutional (§3.3)
- [ ] Analytics (GA4/Plausible) + cookie consent banner
- [ ] Storage cleanup job (orphaned/expired)
- [ ] Core Web Vitals (lazy-load, Tailwind build)
- [ ] Security/bug cleanup: SEC-1, BUG-1/2/3, TZ-1

### P2 — Platform & scale
- [ ] Institutional: SSO + white-label + API
- [ ] Publisher/journal embed widget + revenue share
- [ ] Marketplace (model licensing)
- [ ] LMS (Canvas LTI) integration
- [ ] AI 3D generation add-on
- [ ] i18n (TR + hreflang) if the market requires it

---

## 13. Verification (pass 1)

```bash
python -m py_compile app.py models.py config.py payments.py licensing.py
python -m pytest tests -p no:cacheprovider        # full suite green (incl. new tests)
```

Manual: `GET /robots.txt`, `/sitemap.xml`, `/faq`, `/about`, `/contact`; view-source for
canonical + JSON-LD (validate with Google Rich Results Test). Dev payment: with
`ALLOW_DEV_PAYMENTS=1`, `POST /models/<id>/upgrade/academic` upgrades the model; replaying
the same webhook event is a no-op (idempotency).
