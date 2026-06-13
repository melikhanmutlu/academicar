---
name: academicar-design
description: AcademicAR / Ventriloc visual design language — use whenever building, restyling, or reviewing any UI in this repo (templates/*.html, static/css/style.css, landing, dashboard, viewer, publication pages). Encodes the palette, typography, components, spacing, and the non-negotiable look-and-feel so new UI does not drift into a different visual system.
---

# AcademicAR Design Language

The full token reference is `DESIGN.md` at the repo root — treat it as the source
of truth and read it for exact values. This skill is the working summary plus the
rules that must never be broken.

## The look in one line
Analytical architecture on a clean canvas: gray-on-white, sharp dark text, thin
borders, flat surfaces, and a single warm accent (Sunset Orange) used sparingly.
Think Stripe / Linear / Figma restraint — not a colorful SaaS template.

## Non-negotiables (from CLAUDE.md)
- White canvas, graphite text, thin borders, restrained gray surfaces, **sparing**
  sunset-orange accents.
- Do **not** redesign screens into a different visual system while doing
  infrastructure/product work. Preserve existing flows and layout intent.
- Never invent a filled primary-CTA color. There is **no** colored primary button —
  primary actions use the neutral ghost/graphite treatment. Orange is an *accent*,
  never a button background.

## Color (see DESIGN.md for the full table)
- Text / strong borders: Midnight Graphite `#202020`
- Backgrounds (surfaces 0→3): Canvas White `#ffffff`, Cloud Whisper `#f5f5f5`,
  Slate Mist `#efefef`, Warm Ivory `#ebe6dd`
- Secondary/tertiary text: Dark Shale `#4d4d4d`, Silver Ash `#828282`
- Accent (sparing): Sunset Orange `#ff682c`; data accent: Data Gold `#816729`

## Typography
- **PolySans** (sub: Montserrat) — headlines/featured only, sizes 32/40/66px,
  letter-spacing `-0.02em`.
- **Inter** (sub: system-ui) — body, nav, buttons, data; weights 400/500/600;
  sizes 12–18px.
- Keep weight/size variations within these families; no extra typefaces.

## Spacing & shape
- Base unit 4px. Section gap **80px**, card padding **40px**, element gap **20px**.
- Radius: cards/default 8px, buttons & inputs 20px, avatars/pills 200px. Do not
  introduce other radii.
- Flat surfaces: no arbitrary shadows, no gradients beyond the subtle hero wash.

## Core components
- **Ghost buttons:** transparent bg, graphite text, 2px bottom border (primary) or
  silver text no border (secondary). 20px radius, 18px horizontal padding.
- **Cards:** Slate Mist `#efefef`, 8px radius, generous padding, no shadow.
- **Nav links:** graphite, no bg, 2px bottom border on hover/active.

## When building or changing UI — checklist
1. Pull colors/type/spacing from the tokens above, not ad-hoc values.
2. Spend boldness in one place; keep everything else quiet. Orange highlights one
   thing per view at most — never let an accent CTA out-shout the primary action.
3. Match `templates/`'s existing structure and Tailwind/`static/css/style.css`
   idioms; don't bolt on a new design system.
4. Respect the quality floor: responsive to mobile (390px), visible keyboard focus,
   `prefers-reduced-motion` honored (content must be visible without animation),
   and no content that is invisible until a scroll-reveal fires.
5. Copy is design material: sentence case, plain active-voice verbs, the button
   label matches the resulting toast ("Publish" → "Published"). Empty/error states
   give direction, not mood.
