# AcademicAR — Landing v7.1 Design Reference
> Warm-accent marketing layer on the Ventriloc analytical canvas

**Theme:** light
**Status:** design spec for the `v7.1` branch (marketing/landing redesign). Implementation target: `templates/landing.html` + `static/css/style.css`.
**Base system:** inherits all core tokens from `DESIGN.md` (Ventriloc). This file documents the *marketing surface* — the public landing page shown in the v7.1 reference mockup — which uses the same palette and type, but applies **Sunset Orange more assertively** (filled CTAs, icon chips, numbered badges) than the analytical app surfaces.

---

## Philosophy

The v7.1 landing keeps the Ventriloc foundation — pristine white canvas, graphite text, thin borders, restrained gray surfaces — but layers a confident **product-marketing voice** on top. Where the in-app screens treat Sunset Orange as a sparing data highlight, the landing page promotes it to the **primary brand action color**: the "Get Started" / "Start Publishing" buttons, the numbered workflow badges, the soft icon chips, and the affirmative checkmarks all carry orange. The page reads as a modern research-SaaS site: generous whitespace, centered section headers, soft outlined cards, one product hero mockup, and a single dark "closing" band before the footer.

The overall impression: **credible, calm, academic — but unmistakably a product you can buy today.**

> **Boundary note:** This assertive orange usage is scoped to the *public landing/marketing page only*. It is a deliberate, documented exception to the Ventriloc "never use orange as a button background" rule. Authenticated app surfaces (dashboard, paper detail, editor, viewer) keep the restrained Ventriloc treatment.

---

## Tokens — Colors

Inherits the Ventriloc palette. Marketing-specific roles are noted in the **Role** column.

| Name | Value | Token | Role (landing v7.1) |
|------|-------|-------|---------------------|
| Midnight Graphite | `#202020` | `--color-midnight-graphite` | Primary text, headlines, dark filled "Get Started" button, dark CTA band background, logo mark |
| Canvas White | `#ffffff` | `--color-canvas-white` | Page background, card surfaces, laptop mockup screen |
| Cloud Whisper | `#f5f5f5` | `--color-cloud-whisper` | Alternating section background (`alt-surface`), "Traditional Publication" card, soft thumbnail wells |
| Slate Mist | `#efefef` | `--color-slate-mist` | Card hairline fills, divider zones, before/after neutral card |
| Warm Ivory | `#ebe6dd` | `--color-warm-ivory` | Optional subtle section warmth (use sparingly) |
| Dark Shale | `#4d4d4d` | `--color-dark-shale` | Body copy under headlines, card descriptions |
| Silver Ash | `#828282` | `--color-silver-ash` | Captions, microcopy, muted nav items, "before" X-marks, decorative line art |
| Light Pearl | `#e8e8e8` | `--color-light-pearl` | Card borders (1px), hairline dividers, stats-bar separators |
| Sunset Orange | `#ff682c` | `--color-sunset-orange` | **Primary brand accent**: "3D Models" headline highlight, filled primary CTAs, numbered step badges, feature icon glyphs, affirmative checkmarks, "AcademicAR Publication" card border, "Recommended" badge |
| Orange Wash | `#fff1ea` | `--color-orange-wash` | Soft feature-icon chip background (≈8% orange tint), "AcademicAR Publication" card tint |
| Data Gold | `#816729` | `--color-data-gold` | Reserved secondary accent for data/illustration only (not used in primary CTAs) |

### Derived accent tints (new in v7.1)

| Name | Value | Token | Use |
|------|-------|-------|-----|
| Orange Wash | `#fff1ea` | `--color-orange-wash` | Feature icon chip fill, affirmative card background |
| Orange Soft Border | `#ffd9c7` | `--color-orange-soft-border` | 1px border on orange-tinted cards |
| Orange Pressed | `#e85a22` | `--color-orange-pressed` | Hover/active state for filled orange CTAs |

---

## Tokens — Typography

Identical families to Ventriloc. On the live site, **Montserrat** substitutes for PolySans (loaded in `base.html`), and **Inter** carries all body/UI text.

### Montserrat (PolySans substitute) — Headlines & featured numerals · `--font-polysans`
- **Substitute for:** PolySans
- **Weights:** 400, 600, 700
- **Landing sizes:** 48px (hero H1), 32px (section H2), 22px (card title), 40px+ (stat values)
- **Letter spacing:** -0.02em on display/headline sizes
- **Role:** Hero headline, centered section titles, pricing prices, stat counters (`2,000+`).

### Inter — Body, nav, buttons, labels, microcopy · `--font-inter`
- **Substitute:** system-ui
- **Weights:** 400, 500, 600, 700
- **Landing sizes:** 12px (microcopy/eyebrow), 14px (card desc), 15–16px (body/nav/buttons), 18px (section sub-lead)
- **Letter spacing:** normal for body; **0.08–0.14em uppercase** for small section eyebrows ("How it works", "Features", "Use Cases", "Before / After")
- **Role:** All paragraph text, nav links, button labels, feature descriptions, checklist items, stat labels.

### Type Scale (landing)

| Role | Family | Size | Weight | Line height | Tracking | Notes |
|------|--------|------|--------|-------------|----------|-------|
| hero-display | Montserrat | 48px | 700 | 1.08 | -0.02em | "Publish Interactive **3D Models**…" |
| section-title | Montserrat | 32px | 700 | 1.15 | -0.01em | Centered (How it works, Features, …) |
| card-title | Montserrat/Inter | 18–22px | 600 | 1.2 | normal | Feature / use-case / step titles |
| stat-value | Montserrat | 40px | 700 | 1.0 | -0.01em | `2,000+`, `3,500+` |
| sub-lead | Inter | 18px | 400 | 1.4 | normal | Centered subtitle under section title |
| body | Inter | 15px | 400 | 1.5 | normal | Hero paragraph, card descriptions |
| label | Inter | 14px | 500 | 1.4 | normal | Checklist, nav |
| caption | Inter | 12px | 600 | 1.3 | normal | Step footnote ("PDF + GLB/STL"), microcopy |
| eyebrow | Inter | 12px | 600 | 1.2 | 0.10em uppercase | Optional small kicker above titles |

---

## Tokens — Spacing & Shapes

**Base unit:** 4px · **Density:** comfortable (marketing-generous)

### Spacing Scale (landing rhythm)

| Token | Value | Use |
|-------|-------|-----|
| `--spacing-8` | 8px | Icon ↔ label gaps, chip padding |
| `--spacing-12` | 12px | Checklist item gaps |
| `--spacing-16` | 16px | Intra-card stack |
| `--spacing-20` | 20px | Card internal gap, button group gap |
| `--spacing-32` | 32px | Card padding (marketing cards) |
| `--spacing-40` | 40px | Grid gutters |
| `--spacing-60` | 60px | Title-block → grid gap |
| `--spacing-96` | 96px | **Section vertical rhythm** (between major sections) |

### Border Radius

| Element | Value |
|---------|-------|
| buttons (pill) | 999px / 20px |
| marketing cards | 16px |
| feature icon chip | 12px (rounded square) or 999px (circle) |
| laptop mockup screen | 10px |
| step number badge | 999px (circle) |
| dark CTA band | 20px |
| use-case photo | 16px |
| default | 8px |

### Layout

- **Page max-width:** 1200px, centered (`landing-container`)
- **Section vertical rhythm:** 96px (top & bottom padding per section)
- **Section header → content gap:** 60px
- **Card grid gutter:** 24–40px
- **Hero column gap:** 48–64px

---

## Components

### Top Navigation (marketing)
**Role:** Sticky header on white.

Left: dark rounded-square logo mark (graphite `#202020`, white "A", ~32px, 8px radius) + "AcademicAR" wordmark (Inter 600, 16px, graphite). Center/right: text nav links — `Features`, `Use Cases`, `Pricing`, `Examples`, `About`, `Dashboard` — Inter 400/500, 15px, Silver Ash → graphite on hover. Far right: **"Get Started"** primary button (filled graphite `#202020`, white text, pill/8px radius, ~10×20px padding). Thin bottom border `#e8e8e8` when scrolled.

### Primary Button — Dark (`btn-primary`)
**Role:** Top-nav "Get Started", hero "Start Publishing".
Filled Midnight Graphite `#202020`, white text, Inter 600 15px, padding 12×24px, radius 8–999px. Hover: subtle lift / `#000`. No shadow at rest; optional soft shadow on hover.

### Primary Button — Orange (`btn-primary-orange`)
**Role:** Dark-band CTA ("Start Publishing Now").
Filled Sunset Orange `#ff682c`, white text, Inter 600 15px, padding 12×24px, radius 8px. Hover: `--color-orange-pressed` `#e85a22`. **Only used inside the dark CTA band**, where it provides the page's single boldest action.

### Secondary / Ghost Button (`btn-secondary`)
**Role:** "View Example Publication", "View Pricing Plans →".
Transparent fill, graphite text + 1px `#e8e8e8` border (or borderless link with trailing → arrow on dark band, white/80 text). Inter 500 15px. Hover: border darkens to graphite / underline.

### Hero Block
**Role:** Top split section.
Two-column on desktop (≈ 1.05fr / 1fr), single column stacked on mobile.
- **Left:** hero H1 (Montserrat 700 48px) with the words **"3D Models"** colored Sunset Orange inline; sub-lead paragraph (Inter 15px Dark Shale); button row (dark primary + ghost); **feature checklist row** — 4 inline items each = orange checkmark glyph + Inter 14px label ("No coding", "DOI & PMID support", "QR generated automatically", "AR ready").
- **Right:** **Laptop mockup** — a hardware-framed browser screenshot of a real AcademicAR publication page (3D skull model, metadata table: Authors / Institution / DOI / PMID, a "Share this publication" panel with QR + copy field + action buttons). Rendered on white with a thin device bezel and soft drop shadow.

### Laptop Mockup
**Role:** Hero product proof.
Graphite/dark laptop chassis silhouette, 10px-radius screen, screenshot bleed to bezel. Subtle `0 30px 60px rgba(32,32,32,0.12)` shadow. Decorative only (`aria-hidden` if it duplicates copy).

### How-It-Works Step Card
**Role:** 3-up numbered workflow.
White card, 16px radius, 1px `#e8e8e8` border, 32px padding, centered content. Top: **orange circular number badge** (`#ff682c` fill, white numeral, ~28px circle). Middle: outlined line-art icon cluster (cloud-upload + file, QR + link, phone + model). Title (Montserrat/Inter 600 18px). Description (Inter 14px Dark Shale). Footnote caption (Inter 12px Silver Ash, e.g. "PDF + GLB/STL"). **Arrow connectors** (`→`, Silver Ash) sit between cards on desktop; hidden on mobile stack.

### Feature Card
**Role:** 6-up capability grid (3×2 desktop, 2×1 tablet, 1-col mobile).
White card, 16px radius, 1px `#e8e8e8` border, 24–32px padding, left-aligned. Top: **orange icon chip** — rounded-square/circle filled with Orange Wash `#fff1ea`, containing a Sunset Orange outlined glyph (~22px). Title (Inter 600 16–18px, graphite). Description (Inter 14px Dark Shale). Titles: Stable QR Links · 3D & AR Ready · Academic Metadata · Long-Term Hosting · PDF + Model Together · No App Required.

### Use-Case Photo Card
**Role:** 4-up audience grid.
White card, 16px radius, 1px border, photo/illustration thumbnail on a `#f5f5f5` well at top. Bottom-left of the image: **dark circular icon badge** (graphite `#202020` fill, white glyph, ~36px). Below image: title (Inter 600 16px) + description (Inter 14px Dark Shale). Cards: Scientific Publication · Theses & Dissertations · Medical Education · Research Projects.

### Before / After Comparison
**Role:** Value-contrast pair with center "VS" token.
Two cards side by side, a circular dark **"VS"** badge centered between them (graphite fill, white text).
- **Left "Traditional Publication":** neutral `#f5f5f5` card, 16px radius, gray header, list items each prefixed with a **gray ✕** (Silver Ash): "PDF only", "Static figures", "Limited visualization". Flat PDF document glyph at right.
- **Right "AcademicAR Publication":** white card with **Sunset Orange 1px border** + faint Orange Wash tint, list items each prefixed with an **orange ✓**: "PDF", "Interactive 3D model", "QR access", "AR viewing", "Shareable viewer". PDF + QR + phone glyph cluster at right.

### Dark CTA Band
**Role:** Single closing conversion block before stats.
Full-width-within-container rounded band (20px radius), Midnight Graphite `#202020` background, white text. Left: headline (Montserrat 700 ~28px "Ready to publish your 3D models with your research?") + sub-line (Inter 14px white/70). Right: **orange primary button** ("Start Publishing Now", `btn-primary-orange`) + ghost link ("View Pricing Plans →", white/80, trailing arrow).

### Stats Bar
**Role:** Social-proof footer strip.
4 evenly-spaced columns on white (or very light surface), separated by thin `#e8e8e8` verticals on desktop. Each column: small outlined icon (Silver Ash) + **stat value** (Montserrat 700 40px graphite) + **label** (Inter 14px Silver Ash). Values: `2,000+` Publications · `3,500+` 3D Models · `1,200+` Researchers · `80+` Countries.

### Section Header (centered)
**Role:** Intro for How it works / Features / Use Cases / Before-After.
Centered stack: section title (Montserrat 700 32px graphite) + sub-lead (Inter 18px Silver Ash/Dark Shale), max-width ~640px, centered, 60px gap to the grid below.

---

## Section Order (top → bottom)

1. **Top nav** — logo + links + dark "Get Started".
2. **Hero** — split: headline/checklist/buttons ↔ laptop mockup.
3. **How it works** — centered header + 3 numbered step cards with arrows.
4. **Features** — centered header + 6 orange-chip feature cards (3×2).
5. **Use Cases** — centered header + 4 photo cards with dark icon badges.
6. **Before / After** — centered header + 2 comparison cards with "VS".
7. **Dark CTA band** — graphite block + orange "Start Publishing Now".
8. **Stats bar** — 2,000+ / 3,500+ / 1,200+ / 80+.
9. **Footer** — (existing site footer, graphite band).

---

## Do's and Don'ts

### Do
- Promote Sunset Orange to the primary action color **on the landing page**: filled CTAs (in the dark band), numbered badges, feature-icon chips, and affirmative checkmarks.
- Keep the page on a Canvas White base with `#f5f5f5` alternating sections for rhythm; use exactly one dark graphite band (the closing CTA) for contrast.
- Center section headers (title + sub-lead) and constrain them to ~640px for a calm marketing cadence.
- Use 1px `#e8e8e8` borders + 16px radius for all marketing cards; keep shadows minimal (reserve soft shadow for the laptop mockup and hover states).
- Pair every affirmative list with orange ✓ and every "before/negative" list with gray ✕ for instant scan-ability.
- Maintain 96px section rhythm and generous card padding (24–32px).
- Keep stat values in Montserrat 700 and labels in Inter for clear value/label hierarchy.

### Don't
- Don't carry the landing's assertive orange-filled buttons into the authenticated app — those screens keep the restrained Ventriloc treatment (orange as accent only).
- Don't introduce a second dark band; the page should have a single graphite "closing" moment.
- Don't add gradients, glows, or multi-color illustrations — line-art icons and one orange accent only.
- Don't mix radii: marketing cards are 16px, pills are 999px, default app elements stay 8px.
- Don't let body copy run wide — cap paragraphs near 640px; cap hero paragraph near 460px.
- Don't use Data Gold in CTAs; it stays reserved for data/illustration accents.
- Don't stack more than one accent treatment on a single element (e.g., orange border *and* orange fill *and* orange text).

## Surfaces

| Level | Name | Value | Purpose (landing) |
|-------|------|-------|-------------------|
| 0 | Canvas White | `#ffffff` | Base page, hero, cards |
| 1 | Cloud Whisper | `#f5f5f5` | Alternating sections, thumbnail wells, neutral "before" card |
| 2 | Slate Mist | `#efefef` | Subtle dividers / inner fills |
| 3 | Orange Wash | `#fff1ea` | Feature icon chips, affirmative "after" card tint |
| 4 | Midnight Graphite | `#202020` | Single dark CTA band, logo mark, dark icon badges |

## Imagery

Two registers. **(1) Product proof:** one realistic laptop-framed screenshot of an actual AcademicAR publication page in the hero (skull model + metadata + share panel). **(2) Use-case visuals:** four square thumbnails — a scientific molecule render, an anatomical skull, a skeletal/ribcage render, and a mechanical part — each on a light `#f5f5f5` well with a dark circular icon badge overlaid bottom-left. **Icons:** consistent outlined line-art at a uniform stroke weight; feature icons sit inside Orange Wash chips with Sunset Orange glyphs; workflow/use-case badges are solid (orange circles for steps, graphite circles for use cases). No photography of people; the subject is always the research artifact.

## Layout

Contained 1200px layout centered on white, with 96px vertical section rhythm. The hero is a two-column split (copy + checklist + buttons on the left, laptop product mockup on the right). All subsequent content sections use a **centered header block** (title + sub-lead) above a responsive card grid: 3-up for the workflow, 3×2 for features, 4-up for use cases, and a 2-up comparison for before/after. A single full-width dark graphite CTA band precedes a 4-column stats strip. Grids collapse gracefully: 3/4-up → 2-up on tablet → 1-up on mobile, with arrow connectors and "VS" tokens hidden on stacked layouts.

## Agent Prompt Guide

Quick Color Reference:
```
text:            #202020   (graphite)
body text:       #4d4d4d   (dark shale)
muted/caption:   #828282   (silver ash)
background:      #ffffff   (canvas white)
alt section:     #f5f5f5   (cloud whisper)
card border:     #e8e8e8   (light pearl)
brand accent:    #ff682c   (sunset orange)
accent wash:     #fff1ea   (orange wash)
accent pressed:  #e85a22   (orange pressed)
dark band:       #202020   (graphite)
```

Example Component Prompts:
1. **Feature card:** White surface, 16px radius, 1px `#e8e8e8` border, 32px padding. Top-left icon chip: 44px rounded-square filled `#fff1ea` with a 22px Sunset Orange outlined glyph. Title 'Stable QR Links' Inter 600 18px `#202020`. Body 'Your QR never changes, even after replacing the model' Inter 14px `#4d4d4d`.
2. **Workflow step card:** Centered white card, 16px radius, 1px border, 32px padding. Top: 28px orange circle `#ff682c` with white numeral '1'. Title 'Upload your paper and 3D model' Inter 600 18px. Caption 'PDF + GLB/STL' Inter 12px `#828282`. Place a Silver Ash `→` connector to the right (hidden < 900px).
3. **Dark CTA band:** Full-width rounded 20px block, `#202020` background, white text. Headline Montserrat 700 28px. Primary button filled `#ff682c` (hover `#e85a22`), white label 'Start Publishing Now'; ghost link 'View Pricing Plans →' white/80.

## Similar Brands

- **Sketchfab** — 3D-model-first marketing with a clean light canvas, product mockups, and a single warm action color.
- **Stripe** — White/light system, strong typographic hierarchy, one vibrant accent for action, generous section rhythm.
- **Linear** — Minimal cards, crisp type, restrained palette, centered section headers.
- **Notion** — Soft outlined cards, friendly product screenshots, comfortable whitespace on white.

## Quick Start

### CSS Custom Properties (additive — extends DESIGN.md)

```css
:root {
  /* — inherits all Ventriloc tokens from DESIGN.md — */

  /* v7.1 marketing accent tints */
  --color-orange-wash: #fff1ea;
  --color-orange-soft-border: #ffd9c7;
  --color-orange-pressed: #e85a22;

  /* v7.1 landing rhythm */
  --landing-max-width: 1200px;
  --landing-section-y: 96px;
  --landing-header-gap: 60px;
  --landing-card-radius: 16px;
  --landing-card-border: #e8e8e8;
  --landing-card-padding: 32px;

  /* v7.1 marketing type sizes */
  --text-hero: 48px;
  --leading-hero: 1.08;
  --tracking-hero: -0.02em;
  --text-section-title: 32px;
  --text-stat-value: 40px;
  --text-sub-lead: 18px;
}
```

### Tailwind extend (additive)

```js
theme: {
  extend: {
    colors: {
      'orange-wash': '#fff1ea',
      'orange-soft-border': '#ffd9c7',
      'orange-pressed': '#e85a22',
    },
    borderRadius: { 'card': '16px' },
    maxWidth: { 'landing': '1200px' },
  }
}
```

---

> **Implementation reminder (v7.1):** This document describes the public landing page only. When wiring it into `templates/landing.html`, keep the existing `reveal-up` scroll-in behavior, the `model-viewer` hero option (the mockup may be a static screenshot *or* the live viewer), and the existing footer. Do not propagate orange-filled buttons into authenticated app templates — see the boundary note at the top.
