"""Marketing blog content for AcademicAR.

Posts are defined in-repo (version-controlled, no DB/admin needed) and rendered
from Markdown to HTML on demand (cached). Adding a post = append a dict to
``POSTS``. Titles/keywords are English (global SEO focus).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_RENDER_CACHE: dict[str, str] = {}

# Allowlist for sanitizing rendered Markdown. Bodies can come from admin-editable
# DB posts and are emitted with |safe, so raw inline HTML (markdown passes it
# through) must be filtered to block stored XSS.
_ALLOWED_TAGS = [
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "b", "em", "i", "del", "s", "sup", "sub", "blockquote",
    "ul", "ol", "li", "a", "code", "pre", "span", "div",
    "table", "thead", "tbody", "tr", "th", "td", "img",
]
_ALLOWED_ATTRS = {
    "a": ["href", "title", "rel", "id"],
    "img": ["src", "alt", "title"],
    "th": ["align"],
    "td": ["align"],
    "ol": ["start"],
    "li": ["id"],
    "sup": ["id"],
    "div": ["class"],
    "span": ["class"],
    "code": ["class"],
}
_ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def _sanitize_html(html: str) -> str:
    """Strip script/handlers and disallowed tags from rendered HTML."""
    try:
        import bleach
    except Exception:  # pragma: no cover - bleach should be installed
        # Fail closed: without a sanitizer, escape everything rather than emit
        # untrusted HTML through |safe.
        from markupsafe import escape

        return str(escape(html))
    return bleach.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
    )


def render_body(text: str, cache_key: str | None = None) -> str:
    """Render a Markdown body to HTML.

    When ``cache_key`` is given the result is cached under it (use an immutable
    key like a slug for code posts, or ``db:<id>:<updated_at>`` for editable DB
    posts so an edit busts the cache).
    """
    if cache_key is not None and cache_key in _RENDER_CACHE:
        return _RENDER_CACHE[cache_key]
    try:
        import markdown

        html = markdown.markdown(text, extensions=["extra", "sane_lists", "smarty"])
    except Exception:  # pragma: no cover - markdown should be installed
        logger.warning("Markdown unavailable; serving escaped fallback")
        from markupsafe import escape

        html = "".join(f"<p>{escape(p.strip())}</p>" for p in text.split("\n\n") if p.strip())
    # Sanitize BEFORE the table-wrap below so the wrapper div we add is kept.
    html = _sanitize_html(html)
    # Wrap tables so a wide one scrolls inside its own box instead of forcing
    # the whole page to scroll horizontally on mobile.
    if "<table>" in html:
        html = html.replace("<table>", '<div class="blog-table-wrap"><table>').replace(
            "</table>", "</table></div>"
        )
    if cache_key is not None:
        _RENDER_CACHE[cache_key] = html
    return html


POSTS: list[dict] = [
    {
        "slug": "how-to-add-3d-models-to-research-papers",
        "title": "How to Add Interactive 3D Models to Your Research Paper",
        "description": "A step-by-step guide to embedding interactive 3D models and AR in academic papers, theses, and posters using a QR code — no coding required.",
        "date": "2026-06-02",
        "author": "AcademicAR Team",
        "tags": ["3D publishing", "how-to", "QR codes"],
        "persona": "Researchers & PhD students",
        "read_minutes": 6,
        "body": """\
Static figures flatten three-dimensional data. A micrograph, a reconstructed
fossil, a protein, an engineered part — all lose information the moment they
become a 2D image on a page. Interactive 3D fixes that: readers rotate, zoom,
and inspect the actual geometry, and on a phone they can place it in their room
in augmented reality. Here's how to add that to your next publication.

## 1. Export a clean 3D model

Start from whatever your pipeline produces — a CT/MRI segmentation, a
photogrammetry scan, a CAD assembly, or a molecular surface — and export to one
of the common interchange formats: **GLB/glTF, STL, OBJ, or FBX**. Decimate
extreme polygon counts (a few hundred thousand triangles is plenty for the web)
and make sure the model is watertight and correctly scaled.

## 2. Upload and let it convert

Upload the file to AcademicAR. It is automatically converted to web-optimized
**GLB** with Draco geometry compression and compressed textures, and a companion
**USDZ** is generated so iPhones and iPads can launch AR directly. You don't need
a 3D engineer or a custom WebGL build.

## 3. Get your viewer link and QR code

Every model gets a stable public viewer URL and a **permanent QR code**. Because
the QR resolves through a fixed identifier, it keeps working even if you later
replace the file, recolor it, or extend its license — so the QR you print today
will not rot.

## 4. Place it in your paper, thesis, or poster

- **Journal/PDF:** add a figure note such as *"Interactive 3D model available at
  [link] — scan the QR code"* and drop the QR image next to the figure.
- **Conference poster:** put the QR in the corner of the relevant panel; ~half of
  attendees will scan a poster QR, turning a static panel into a hands-on demo.
- **Thesis:** link from the figure caption; examiners can explore the geometry
  while they read.
- **Website/repository:** embed the viewer as a lightweight iframe widget.

## 5. Keep it compliant

Before sharing, confirm the model is anonymized where required and that you hold
the rights to publish it. Responsible sharing is part of good scholarship, and
the upload step makes that confirmation explicit.

That's it — one upload turns a 3D asset into an interactive, AR-ready figure your
readers can actually explore.

*Ready to try it? [Create a free account](/auth/register) and publish your first
interactive model.*
""",
    },
    {
        "slug": "qr-codes-on-academic-posters-guide",
        "title": "The Complete Guide to QR Codes on Academic Posters",
        "description": "Best practices for putting QR codes on conference posters: what to link, where to place them, sizing, and how to turn a static poster into an interactive demo.",
        "date": "2026-06-03",
        "author": "AcademicAR Team",
        "tags": ["QR codes", "conference posters", "how-to"],
        "persona": "Conference presenters",
        "read_minutes": 5,
        "body": """\
A poster has minutes to make an impression, and wall space is scarce. A QR code
is the cheapest way to extend a poster beyond its borders — linking to your data,
your paper, or, increasingly, an **interactive 3D model** of whatever you're
presenting. Here's how to do it well.

## What to link to

- **An interactive 3D/AR model** of your specimen, structure, or device — the most
  memorable option, because viewers can handle it themselves.
- The full paper or preprint (DOI link).
- Supplementary data, code, or a short video.

Avoid linking to a generic lab homepage; make the destination specific to the
poster.

## Placement and sizing

- Put the QR **near the relevant figure**, not just in a footer, so the link has
  obvious context.
- Make it **at least 3–4 cm square** on a printed poster so phones lock on from a
  comfortable standing distance.
- Keep a quiet margin of white space around the code.
- Add a one-line call to action: *"Scan to explore the model in 3D & AR."*

## Make it durable

Use a QR that points to a **stable resolver URL**, not a raw file path that might
move. AcademicAR's model QR codes resolve through a permanent identifier, so the
same printed poster keeps working after you replace or upgrade the model later.

## Measure it (optional)

If you want to know whether people scanned, link through a destination you control
so you can see views over the conference. Even rough numbers help you justify the
effort next time.

## A quick checklist

1. One specific, valuable destination per QR.
2. Placed next to the figure it explains.
3. 3–4 cm minimum, with margin.
4. A short call to action.
5. A stable URL that won't break.

Done right, a QR turns a poster from something people glance at into something they
hold in their hands.

*[Generate a permanent QR code for your 3D model](/auth/register) in a couple of
minutes.*
""",
    },
    {
        "slug": "glb-stl-gltf-obj-3d-format-guide",
        "title": "GLB vs STL vs glTF vs OBJ: Choosing a 3D Format for the Web",
        "description": "A practical comparison of GLB, glTF, STL, OBJ, and FBX for sharing research 3D models online — which to use, and why GLB usually wins for the web.",
        "date": "2026-06-04",
        "author": "AcademicAR Team",
        "tags": ["3D formats", "glTF", "comparison"],
        "persona": "Technical researchers",
        "read_minutes": 6,
        "body": """\
"What format should I export?" is the first question when you want to share a 3D
model online. Here's a researcher-friendly comparison of the formats you'll meet.

## STL — geometry only

STL is the lingua franca of 3D printing and many segmentation tools. It stores
**triangles and nothing else**: no color, no texture, no material. It's perfect as
a *source* format from CT/MRI or CAD, but on its own a web viewer can only show it
as a plain gray shape.

## OBJ — geometry plus simple materials

OBJ adds UV coordinates and references an `.mtl` material file plus texture images.
It's widely supported and human-readable, but it's verbose and splits a model
across several files, which is awkward to host.

## FBX — rich but proprietary

FBX (from Autodesk) carries geometry, materials, rigs, and animation. It's common
in CAD and animation pipelines but is a proprietary binary that needs conversion
for the open web.

## glTF / GLB — built for the web

glTF is the "JPEG of 3D": an open standard designed for efficient delivery and
real-time rendering. **GLB** is its single-file binary form — geometry, materials,
textures, and animation all in one `.glb`. It supports physically based rendering
(PBR) so materials look right, and it compresses well.

## So which should you use?

- **Working/source format:** keep your STL, OBJ, or FBX from the original pipeline.
- **Sharing on the web:** convert to **GLB**. It's one file, renders fast, supports
  real materials, and is what browser viewers and AR expect.

The good news: you don't have to convert by hand. Upload STL, OBJ, FBX, or GLB to
AcademicAR and it produces an optimized GLB (Draco-compressed geometry, compressed
textures) plus a USDZ for iOS AR — so your readers get a fast, correct model
regardless of what you started from.

*[Upload any format and get a web-ready GLB](/auth/register) automatically.*
""",
    },
    {
        "slug": "augmented-reality-anatomy-education",
        "title": "How Augmented Reality Is Transforming Anatomy Education",
        "description": "Why AR and interactive 3D models are reshaping how anatomy is taught — spatial understanding, accessibility, and lower lab costs.",
        "date": "2026-06-05",
        "author": "AcademicAR Team",
        "tags": ["anatomy", "education", "augmented reality"],
        "persona": "Medical & anatomy educators",
        "read_minutes": 5,
        "body": """\
Anatomy is inherently three-dimensional, yet it is still often taught from 2D
atlases. Augmented reality and interactive 3D models close that gap, letting
students manipulate structures in space rather than memorizing flat cross-sections.

## Why 3D and AR help learning

Spatial structures — the branching of a bronchial tree, the layers of the heart,
the course of a nerve — are far easier to understand when you can rotate them and
see how parts relate. AR adds a further step: placing a life-size (or scaled)
model into the room, so students walk around it and view it from any angle.

## Practical classroom uses

- **Pre-lab orientation:** students explore a structure in 3D before a dissection,
  arriving better prepared.
- **Self-study:** an interactive model linked from lecture notes lets students
  review at their own pace.
- **Assessment and demonstration:** annotate key landmarks directly on the model.
- **Accessibility:** remote and distance learners get the same hands-on object as
  those in the lab.

## The cost and access angle

Cadaveric and physical model resources are expensive and limited. Digital 3D
models don't replace them, but they extend access dramatically — every student
with a phone has the structure in their pocket, available any time.

## How to add it to your course

You don't need a development team. Take a 3D anatomical model (from a scan or a
licensed library), upload it, and share the viewer link or QR code in your LMS,
slides, or handouts. Students open it in a browser; on a phone they can switch to
AR with a tap — no app to install.

Interactive 3D won't replace the dissection room, but it makes the spatial
reasoning at the heart of anatomy visible to every student, every time.

*[Publish an interactive anatomy model](/auth/register) and share it with your
class today.*
""",
    },
    {
        "slug": "interactive-3d-figures-scientific-publishing",
        "title": "Why Interactive 3D Figures Belong in Scientific Publishing",
        "description": "Static figures lose information. Interactive 3D figures improve comprehension, reproducibility, and engagement — and they're easier to add than you think.",
        "date": "2026-06-05",
        "author": "AcademicAR Team",
        "tags": ["scientific publishing", "open science", "3D figures"],
        "persona": "Researchers & editors",
        "read_minutes": 5,
        "body": """\
Most scientific results that are three-dimensional are still communicated in two
dimensions. We render a chosen viewpoint, flatten it, and hope the reader
reconstructs the rest. Interactive 3D figures remove that compromise.

## What we lose with static figures

A single rendered angle hides occluded structures, makes spatial relationships
ambiguous, and forces authors to pick one viewpoint among many. Reviewers and
readers can't check the parts the author didn't show.

## What interactive 3D adds

- **Comprehension:** readers explore the geometry themselves, from any angle.
- **Reproducibility:** the actual model — not a screenshot of it — travels with the
  paper, supporting the move toward FAIR, open research objects.
- **Engagement:** an interactive figure is memorable, and on mobile it becomes an
  AR object the reader can place in front of them.

## Why now

Three forces are converging: journals increasingly **mandate data availability**;
3D capture (photogrammetry, micro-CT, AI generation) is getting cheap and common;
and open web standards (glTF/GLB, USDZ, WebXR) make interactive 3D render anywhere
without plugins. The infrastructure to publish 3D well finally exists.

## The friction problem — and the fix

Historically, embedding interactive 3D meant custom web work that most authors
won't do. That's the gap AcademicAR fills: upload your model, get an optimized,
AR-ready viewer and a permanent QR code, and link it from your figure caption. The
interactive object lives alongside your paper instead of dying as a static image.

Interactive 3D figures aren't a gimmick — they're a more honest way to communicate
three-dimensional results. As tooling removes the friction, they'll become an
expected part of the record.

*[Turn your next figure into an interactive 3D object](/auth/register).*
""",
    },
    {
        "slug": "ar-for-archaeology-3d-artifacts",
        "title": "AR for Archaeology: Bringing Artifacts to Life in 3D",
        "description": "How 3D scanning and augmented reality help archaeologists share, study, and teach with fragile artifacts — without risking the originals.",
        "date": "2026-06-06",
        "author": "AcademicAR Team",
        "tags": ["archaeology", "museums", "3D scanning"],
        "persona": "Archaeologists & curators",
        "read_minutes": 5,
        "body": """\
Artifacts are fragile, irreplaceable, and often locked away in storage. 3D scanning
plus augmented reality lets archaeologists and museums share them widely while the
originals stay safe.

## Why digitize artifacts

- **Preservation:** a high-fidelity scan is a permanent record if an object is
  damaged, lost, or degrades.
- **Access:** researchers worldwide can examine an object without travel or
  handling the original.
- **Teaching and outreach:** students and the public can rotate, zoom, and place an
  artifact in AR — far more engaging than a photo behind glass.

## From scan to shareable model

Photogrammetry (many overlapping photos) or structured-light scanning produces a
detailed mesh with color. After cleanup and decimation, export to a web format and
publish it as an interactive viewer. Add **annotations** to point out tool marks,
inscriptions, or repairs, turning the model into a guided object lesson.

## Putting it in publications and exhibits

- **Papers:** link the 3D model from your figures so reviewers can inspect the
  surface themselves.
- **Posters and labels:** a QR code next to an artifact label lets visitors explore
  it in 3D and AR on their own phones.
- **Online collections:** embed the viewer in a catalog page to bring a static
  record to life.

## Responsible sharing

Digitization raises questions of provenance, ownership, and cultural sensitivity.
Confirm you have the rights to share an object and respect any restrictions from
source communities — good practice that AcademicAR's upload step makes explicit.

3D and AR let an artifact be studied by everyone, everywhere, while the original
rests safely in its case.

*[Publish a 3D artifact with annotations](/auth/register) and share it via a QR
code.*
""",
    },
]


def get_all_posts() -> list[dict]:
    """All built-in (code) posts, newest first."""
    return sorted(POSTS, key=lambda p: p["date"], reverse=True)


def get_post(slug: str) -> dict | None:
    for post in POSTS:
        if post["slug"] == slug:
            return post
    return None


def code_post_slugs() -> set[str]:
    """Slugs reserved by the built-in code posts (avoid DB slug collisions)."""
    return {p["slug"] for p in POSTS}
