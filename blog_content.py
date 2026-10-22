"""Marketing blog content for AcademicAR.

Posts are defined in-repo (version-controlled, no DB/admin needed) and rendered
from Markdown to HTML on demand (cached). Adding a post = append a dict to
``POSTS``. Titles/keywords are English (global SEO focus). When a post's
content is revised, set an optional ``"updated": "YYYY-MM-DD"`` so its
dateModified and sitemap lastmod move with it.
"""
from __future__ import annotations

import logging
import re

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
_INLINE_IMG_RE = re.compile(r'<img ([^>]*src="/static/images/blog/inline/[^"]*"[^>]*?)\s*/?>')


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
    # Interior illustrations load lazily and reserve their 2:1 box so the page
    # doesn't jump as they arrive.
    html = _INLINE_IMG_RE.sub(r'<img \1 width="1600" height="800" loading="lazy" decoding="async">', html)
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
        "updated": "2026-10-19",
        "author": "AcademicAR Team",
        "tags": ["3D publishing", "how-to", "QR codes"],
        "persona": "Researchers & PhD students",
        "read_minutes": 6,
        "body": """\
![A research article figure lifting off the page as an interactive 3D bone model](/static/images/blog/covers/how-to-add-3d-models-to-research-papers.webp)

Static figures flatten three-dimensional data. A micrograph, a reconstructed
fossil, a protein, an engineered part — all lose information the moment they
become a 2D image on a page. Interactive 3D fixes that: readers rotate, zoom,
and inspect the actual geometry, and on a phone they can place it in their room
in augmented reality. Here's how to add that to your next publication.

## 1. Export a clean 3D model

Start from whatever your pipeline produces — a CT/MRI scan or segmentation, a
photogrammetry scan, a CAD assembly, or a molecular surface. AcademicAR accepts
**GLB/glTF, STL, OBJ, FBX, STEP/STP**, a **DICOM series** (as a ZIP), and
segmentations in **NIfTI, NRRD, or DICOM-SEG**, so you often don't need to
convert anything yourself. If you export a mesh, decimate extreme polygon counts
(a few hundred thousand triangles is plenty for the web) and check the scale.

Not sure which format to export? See
[GLB vs STL vs glTF vs OBJ](/blog/glb-stl-gltf-obj-3d-format-guide). Starting
from a scan? Our [DICOM to 3D guide](/blog/dicom-to-3d-model-guide) covers it.

## 2. Upload and let it convert

Upload the file to a project. It is converted in the background to web-optimized
**GLB** with Draco geometry compression and compressed textures, and a companion
**USDZ** is generated so iPhones and iPads can launch AR directly. Multi-part
models (assemblies, segmentations with several structures) get one **layer** per
part, so readers can hide, isolate, and recolour them. You don't need a 3D
engineer or a custom WebGL build.

![A converted bone model with its viewer link and QR code](/static/images/blog/inline/how-to-add-3d-models-to-research-papers-a.webp)

## 3. Get your viewer link and QR code

Every model gets a stable public viewer URL and a **stable QR code**. Because
the QR resolves through a fixed identifier, it keeps working even if you later
replace the file, recolor it, or extend its license — so the QR you print today
will not rot.

## 4. Place it in your paper, thesis, or poster

- **Journal/PDF:** add a figure note such as *"Interactive 3D model available at
  [link] — scan the QR code"* and drop the QR image next to the figure. Many
  journals accept a QR inside the figure panel; check the author guidelines.
- **Conference poster:** put the QR next to the relevant panel; our
  [poster QR guide](/blog/qr-codes-on-academic-posters-guide) covers size and
  placement.
- **Thesis:** link from the figure caption; examiners can explore the geometry
  while they read.
- **Website, LMS, or repository:** [embed the viewer](/blog/embed-3d-model-website)
  as a lightweight iframe.

![A printed figure and its interactive 3D model side by side](/static/images/blog/inline/how-to-add-3d-models-to-research-papers-b.webp)

## 5. Make the figure itself from the model

The same model can produce the static figure that goes into the PDF. Save a
view (camera, visible layers, labels) and export a
[publication-ready figure](/blog/publication-ready-3d-figures-comparisons) with a
scale bar and a QR card, so the printed image and the interactive model always
match. Saved **scenes** let you link straight to the view a figure shows.

## 6. Keep it compliant

Before sharing, confirm the model is anonymized where required and that you hold
the rights to publish it. Responsible sharing is part of good scholarship, and
the upload step makes that confirmation explicit. Medical scans need an extra
confirmation, and the raw scan is deleted after conversion.

That's it — one upload turns a 3D asset into an interactive, AR-ready figure your
readers can actually explore.

## Frequently asked questions

### Can I put an interactive 3D model in a PDF paper?

A PDF can't reliably show interactive 3D in every reader, but it can carry a link
and a QR code. Readers open the model in their browser from the link, and on a
phone the QR code opens it in 3D and AR.

### Will the link still work if I update the model after publication?

Yes. The viewer link and QR code point to a permanent identifier, so replacing
the file, changing colours, or upgrading the plan keeps the same link.

### Which 3D formats can I upload?

GLB/glTF, STL, OBJ (with its MTL and textures), FBX, STEP/STP, a DICOM series as
a ZIP, and segmentations in NIfTI, NRRD, or DICOM-SEG.

*Ready to try it? [Create a free account](/auth/register) and publish your first
interactive model.*
""",
    },
    {
        "slug": "qr-codes-on-academic-posters-guide",
        "title": "The Complete Guide to QR Codes on Academic Posters",
        "description": "Best practices for putting QR codes on conference posters: what to link, where to place them, sizing, and how to turn a static poster into an interactive demo.",
        "date": "2026-06-03",
        "updated": "2026-10-19",
        "author": "AcademicAR Team",
        "tags": ["QR codes", "conference posters", "how-to"],
        "persona": "Conference presenters",
        "read_minutes": 6,
        "body": """\
![A conference poster with a QR code being scanned by a phone that shows a 3D model](/static/images/blog/covers/qr-codes-on-academic-posters-guide.webp)

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
poster. If you have several models, one QR per model next to its figure works
better than one QR to a list.

## Placement and sizing

- Put the QR **near the relevant figure**, not just in a footer, so the link has
  obvious context.
- Make it **at least 3–4 cm square** on a printed poster so phones lock on from a
  comfortable standing distance; 5 cm or more if people will scan from across an
  aisle.
- Keep a quiet margin of white space around the code, and print it dark on light.
  Inverted or low-contrast QR codes fail on many phones.
- Add a one-line call to action: *"Scan to explore the model in 3D & AR."*

![Sizing a QR code on a poster with a ruler](/static/images/blog/inline/qr-codes-on-academic-posters-guide-a.webp)

## Make it durable

Use a QR that points to a **stable resolver URL**, not a raw file path that might
move. AcademicAR's model QR codes resolve through a permanent identifier, so the
same printed poster keeps working after you replace or upgrade the model later.
That matters because posters get photographed, shared, and pinned on lab walls for
years.

## Design the landing experience

The scan is only half of it. What opens should work on a phone held at arm's
length, on conference Wi-Fi:

- **Keep the model light.** Decimate dense scans so it loads quickly.
- **Start from a good view.** Save a default view or a scene so the first thing
  people see is the part your poster discusses.
- **Offer AR.** Many visitors will tap *View in AR* and place the model on the
  floor in front of the poster. That moment is what people remember.

The same QR can go on a slide at your talk; see
[how to add a 3D model to PowerPoint](/blog/add-3d-model-to-powerpoint).

## Measure it (optional)

If you want to know whether people scanned, link through a destination you
control. AcademicAR shows each model's **views, QR scans, and AR starts**, so you
can see what the poster session actually produced and justify the effort next
time.

![The mobile landing experience after scanning a poster QR code](/static/images/blog/inline/qr-codes-on-academic-posters-guide-b.webp)

## A quick checklist

1. One specific, valuable destination per QR.
2. Placed next to the figure it explains.
3. 3–4 cm minimum, with margin and strong contrast.
4. A short call to action.
5. A stable URL that won't break.
6. A light model and a good first view on a phone.

Done right, a QR turns a poster from something people glance at into something they
hold in their hands.

## Frequently asked questions

### How big should a QR code be on a poster?

At least 3–4 cm square for scanning from a normal standing distance, and larger
(5 cm or more) if people will scan from further away. Leave a white margin around
it.

### What should a poster QR code link to?

Something specific to the poster: an interactive 3D model of the specimen or
device, the paper or preprint, or the dataset. A 3D model is the most engaging
because visitors can explore it and place it in AR.

### Will my poster QR code still work after the conference?

It will if it points to a stable link. AcademicAR's QR codes resolve through a
permanent identifier, so they keep working when you replace or update the model.

*[Generate a stable QR code for your 3D model](/auth/register) in a couple of
minutes.*
""",
    },
    {
        "slug": "glb-stl-gltf-obj-3d-format-guide",
        "title": "GLB vs STL vs glTF vs OBJ: Choosing a 3D Format for the Web",
        "description": "A practical comparison of GLB, glTF, STL, OBJ, and FBX for sharing research 3D models online — which to use, and why GLB usually wins for the web.",
        "date": "2026-06-04",
        "updated": "2026-10-19",
        "author": "AcademicAR Team",
        "tags": ["3D formats", "glTF", "comparison"],
        "persona": "Technical researchers",
        "read_minutes": 6,
        "body": """\
![Four 3D file types, each with the kind of model it holds](/static/images/blog/covers/glb-stl-gltf-obj-3d-format-guide.webp)

"What format should I export?" is the first question when you want to share a 3D
model online. Here's a researcher-friendly comparison of the formats you'll meet.

## STL — geometry only

STL is the lingua franca of 3D printing and many segmentation tools. It stores
**triangles and nothing else**: no units, no color, no texture, no material, no
separate parts. It's perfect as a *source* format from CT/MRI or CAD, but on its
own a web viewer can only show it as a plain gray shape, and you have to say
whether it was made in millimetres or metres.

## OBJ — geometry plus simple materials

OBJ adds UV coordinates and references an `.mtl` material file plus texture images.
It's widely supported and human-readable, but it's verbose and splits a model
across several files, which is awkward to host. Like STL, it has no units.

![A bare triangle mesh next to the same part with materials](/static/images/blog/inline/glb-stl-gltf-obj-3d-format-guide-a.webp)

## FBX — rich but proprietary

FBX (from Autodesk) carries geometry, materials, a hierarchy of parts, the
modelling unit, rigs, and animation. It's common in CAD and animation pipelines
but is a proprietary binary that needs conversion for the open web.

## STEP — exact CAD geometry

STEP (`.step`, `.stp`) is the neutral exchange format of mechanical CAD. It
stores exact surfaces (not triangles) and the assembly structure, so it must be
tessellated into a mesh before any viewer can draw it. See
[what a STEP file is and how to open it](/blog/open-step-stp-file-online).

## glTF / GLB — built for the web

glTF is the "JPEG of 3D": an open standard designed for efficient delivery and
real-time rendering. **GLB** is its single-file binary form — geometry, materials,
textures, named parts, and animation all in one `.glb`, in metres. It supports
physically based rendering (PBR) so materials look right, and it compresses well
with Draco.

## USDZ — Apple's AR format

USDZ is the packaged form of Pixar's Universal Scene Description and is what iPhone
and iPad **AR Quick Look** opens. You rarely author it directly: you convert from
GLB. See [GLB to USDZ](/blog/glb-to-usdz-iphone-ar).

![One GLB file opening in a browser and on a phone](/static/images/blog/inline/glb-stl-gltf-obj-3d-format-guide-b.webp)

## At a glance

| Format | Colour & textures | Parts | Units | Web-ready |
| --- | --- | --- | --- | --- |
| STL | No | No | No | Needs conversion |
| OBJ | Yes (MTL + images) | Groups | No | Needs conversion |
| FBX | Yes | Yes | Yes | Needs conversion |
| STEP | Colours only | Yes | Yes | Needs tessellation |
| GLB | Yes | Yes | Metres | Yes |
| USDZ | Yes | Yes | Yes | iPhone/iPad AR |

## So which should you use?

- **Working/source format:** keep your STL, OBJ, FBX, or STEP from the original
  pipeline.
- **Sharing on the web:** convert to **GLB**. It's one file, renders fast, supports
  real materials, and is what browser viewers and Android AR expect.
- **iPhone AR:** add a **USDZ** generated from the GLB.

The good news: you don't have to convert by hand. Upload STL, OBJ, FBX, STEP, or
GLB to AcademicAR and it produces an optimized GLB (Draco-compressed geometry,
compressed textures) plus a USDZ for iOS AR — so your readers get a fast, correct
model regardless of what you started from. Step-by-step guides:
[STL to GLB](/blog/convert-stl-to-glb) and
[OBJ and FBX to GLB](/blog/convert-obj-fbx-to-glb).

## Frequently asked questions

### What is the difference between glTF and GLB?

They are the same format in two packagings. glTF is a JSON file that can
reference separate binary and image files; GLB packs everything into one binary
file, which is easier to host and share.

### Is GLB better than STL?

For viewing and sharing, yes: GLB carries colours, materials, parts, and real
units, and it compresses well. STL is still the standard for 3D printing, so keep
it for the printer.

### Which 3D format is best for the web?

GLB. It is the format browsers, web viewers such as model-viewer, and Android AR
load directly. Add USDZ for AR on iPhone and iPad.

*[Upload any format and get a web-ready GLB](/auth/register) automatically.*
""",
    },
    {
        "slug": "augmented-reality-anatomy-education",
        "title": "How Augmented Reality Is Transforming Anatomy Education",
        "description": "Why AR and interactive 3D models are reshaping how anatomy is taught — spatial understanding, accessibility, and lower lab costs.",
        "date": "2026-06-05",
        "updated": "2026-10-19",
        "author": "AcademicAR Team",
        "tags": ["anatomy", "education", "augmented reality"],
        "persona": "Medical & anatomy educators",
        "read_minutes": 5,
        "body": """\
![Two students looking at a skull and spine model in augmented reality above a tablet](/static/images/blog/covers/augmented-reality-anatomy-education.webp)

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

![Students exploring a floating hand skeleton in AR](/static/images/blog/inline/augmented-reality-anatomy-education-a.webp)

## Features that matter for anatomy teaching

- **Layers.** A model built from a segmentation keeps each structure separate:
  students hide the ribs to see the lungs, or isolate one vessel. See
  [sharing medical segmentations](/blog/medical-image-segmentation-to-3d-viewer).
- **Cross-sections.** A section plane and a colour-coded
  [2D slice view](/blog/section-plane-slice-view-cross-sections) connect the 3D
  model to the CT and MRI planes students learn to read.
- **Guided tours.** Save a sequence of views with labels and play it as a
  [tour](/blog/saved-scenes-guided-tours-3d-teaching), so a lecture walks through
  the anatomy in order.
- **Measurements.** [Structure dimensions and distances](/blog/measuring-anatomy-3d-layer-metrics)
  make size relationships concrete.

## Where the models come from

You can start from a licensed anatomy library, from anonymized patient scans with
the right approvals, or from your own specimens. A CT series can be uploaded
directly and turned into bone, skin, and vessel layers; our
[DICOM to 3D guide](/blog/dicom-to-3d-model-guide) explains how.

![A layered heart model in AR with parts pulled apart](/static/images/blog/inline/augmented-reality-anatomy-education-b.webp)

## The cost and access angle

Cadaveric and physical model resources are expensive and limited. Digital 3D
models don't replace them, but they extend access dramatically — every student
with a phone has the structure in their pocket, available any time.

## How to add it to your course

You don't need a development team. Take a 3D anatomical model, upload it, and
share the viewer link or QR code in your LMS, slides, or handouts, or
[embed it in a course page](/blog/embed-3d-model-website). Students open it in a
browser; on a phone they can switch to AR with a tap — no app to install. More
ideas for clinical teaching are in
[3D in medical education and case reports](/blog/3d-models-surgical-education-case-reports).

Interactive 3D won't replace the dissection room, but it makes the spatial
reasoning at the heart of anatomy visible to every student, every time.

## Frequently asked questions

### Do students need an app to view anatomy models in AR?

No. The model opens in the phone's browser, and the View in AR button uses the
AR viewer built into iOS and Android.

### Can I use patient CT scans for teaching models?

Only with the approvals your institution requires and after full anonymization.
Medical uploads to AcademicAR need an explicit anonymization confirmation, and the
raw scan is deleted after conversion.

### Can students see inside a 3D anatomy model?

Yes. Layers let them hide outer structures, and the section plane cuts through the
model to show cross-sections like a CT or MRI slice.

*[Publish an interactive anatomy model](/auth/register) and share it with your
class today. See also [AR for anatomy teaching](/ar-for-anatomy).*
""",
    },
    {
        "slug": "interactive-3d-figures-scientific-publishing",
        "title": "Why Interactive 3D Figures Belong in Scientific Publishing",
        "description": "Static figures lose information. Interactive 3D figures improve comprehension, reproducibility, and engagement — and they're easier to add than you think.",
        "date": "2026-06-05",
        "updated": "2026-10-19",
        "author": "AcademicAR Team",
        "tags": ["scientific publishing", "open science", "3D figures"],
        "persona": "Researchers & editors",
        "read_minutes": 5,
        "body": """\
![A flat lattice figure unfolding into an interactive 3D lattice](/static/images/blog/covers/interactive-3d-figures-scientific-publishing.webp)

Most scientific results that are three-dimensional are still communicated in two
dimensions. We render a chosen viewpoint, flatten it, and hope the reader
reconstructs the rest. Interactive 3D figures remove that compromise.

## What we lose with static figures

A single rendered angle hides occluded structures, makes spatial relationships
ambiguous, and forces authors to pick one viewpoint among many. Reviewers and
readers can't check the parts the author didn't show, and they can't measure
anything the figure doesn't label.

## What interactive 3D adds

- **Comprehension:** readers explore the geometry themselves, from any angle.
- **Reproducibility:** the actual model — not a screenshot of it — travels with the
  paper, supporting the move toward FAIR, open research objects.
- **Verification:** reviewers can cut through the model with a section plane and
  check dimensions instead of trusting a caption.
- **Engagement:** an interactive figure is memorable, and on mobile it becomes an
  AR object the reader can place in front of them.

![A static paper figure compared with an interactive 3D structure](/static/images/blog/inline/interactive-3d-figures-scientific-publishing-a.webp)

## Why now

Three forces are converging: journals increasingly **mandate data availability**;
3D capture (photogrammetry, micro-CT, AI generation) is getting cheap and common;
and open web standards (glTF/GLB, USDZ, WebXR) make interactive 3D render anywhere
without plugins. The infrastructure to publish 3D well finally exists.

## What a good interactive figure looks like

- **It opens on the view the paper discusses.** Save that view as a scene and
  link to it directly, so the reader doesn't start from an arbitrary angle.
- **It keeps parts separate.** Structures, components, or phases are layers the
  reader can hide and isolate.
- **It matches the printed figure.** Export the static figure from the same model
  so the two never drift apart; see
  [publication-ready figures and comparisons](/blog/publication-ready-3d-figures-comparisons).
- **It has a stable link** that still works after you correct or replace the model.
- **It is light enough for a phone**, because many readers will scan the QR code.

## The friction problem — and the fix

Historically, embedding interactive 3D meant custom web work that most authors
won't do. That's the gap AcademicAR fills: upload your model, get an optimized,
AR-ready viewer and a stable QR code, and link it from your figure caption. The
interactive object lives alongside your paper instead of dying as a static image.
For the practical steps, see
[how to add interactive 3D models to your research paper](/blog/how-to-add-3d-models-to-research-papers).

![A repository record linked to an interactive 3D model viewer](/static/images/blog/inline/interactive-3d-figures-scientific-publishing-b.webp)

## Deposit and display

Interactive viewing doesn't replace archiving. Deposit the underlying data in a
repository with a persistent identifier, cite it, and use the interactive viewer
as the readable layer on top. Our
[guide to research 3D platforms](/blog/sketchfab-alternative-researchers) compares
the options.

Interactive 3D figures aren't a gimmick — they're a more honest way to communicate
three-dimensional results. As tooling removes the friction, they'll become an
expected part of the record.

## Frequently asked questions

### Do journals accept interactive 3D figures?

Most journals publish a static figure in the article and accept links to
supplementary material. A common approach is a static figure with a caption link
and QR code to the interactive model.

### How do I cite an interactive 3D model in a paper?

Cite the archived dataset by its DOI, and add the viewer link or QR code in the
figure caption or data availability statement.

### What makes an interactive figure reproducible?

The actual geometry is shared, not only a rendering, and it stays available at a
stable link. Archiving the source data in a repository completes it.

*[Turn your next figure into an interactive 3D object](/auth/register).*
""",
    },
    {
        "slug": "ar-for-archaeology-3d-artifacts",
        "title": "AR for Archaeology: Bringing Artifacts to Life in 3D",
        "description": "How 3D scanning and augmented reality help archaeologists share, study, and teach with fragile artifacts — without risking the originals.",
        "date": "2026-06-06",
        "updated": "2026-10-19",
        "author": "AcademicAR Team",
        "tags": ["archaeology", "museums", "3D scanning"],
        "persona": "Archaeologists & curators",
        "read_minutes": 5,
        "body": """\
![An amphora on a museum plinth surrounded by photogrammetry camera positions, with a phone showing its 3D scan](/static/images/blog/covers/ar-for-archaeology-3d-artifacts.webp)

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
detailed mesh with color. A few habits make the result far better:

- **Shoot with diffuse light** and plenty of overlap (60–80% between photos), and
  include a **scale bar** in the scene so the model has real dimensions.
- **Clean and decimate.** Remove the turntable and background, and reduce the
  triangle count for the web; keep the full-resolution scan as your archive copy.
- **Export with textures**, as GLB, or OBJ with its MTL and images, or FBX. See
  [how to convert OBJ and FBX to GLB](/blog/convert-obj-fbx-to-glb).

Then publish it as an interactive viewer. Add **annotations** to point out tool
marks, inscriptions, or repairs, turning the model into a guided object lesson.

![Zooming into surface detail on a scanned pot](/static/images/blog/inline/ar-for-archaeology-3d-artifacts-a.webp)

## Look closer than the display case allows

- **Section planes** cut through a vessel to show wall thickness or a profile,
  the 3D equivalent of a drawn section.
- **Measurements** give dimensions and distances between parts.
- **Saved scenes and tours** walk students through an object step by step; see
  [teaching with guided tours](/blog/saved-scenes-guided-tours-3d-teaching).
- **Side-by-side comparisons** show an object before and after conservation, or
  two related finds next to each other.

## Putting it in publications and exhibits

- **Papers:** link the 3D model from your figures so reviewers can inspect the
  surface themselves; see
  [interactive 3D figures in publishing](/blog/interactive-3d-figures-scientific-publishing).
- **Posters and labels:** a QR code next to an artifact label lets visitors explore
  it in 3D and AR on their own phones; our
  [QR code guide](/blog/qr-codes-on-academic-posters-guide) covers sizing.
- **Online collections:** [embed the viewer](/blog/embed-3d-model-website) in a
  catalog page to bring a static record to life.

![A museum display case with a QR plaque linking to the 3D model](/static/images/blog/inline/ar-for-archaeology-3d-artifacts-b.webp)

## Responsible sharing

Digitization raises questions of provenance, ownership, and cultural sensitivity.
Confirm you have the rights to share an object and respect any restrictions from
source communities — good practice that AcademicAR's upload step makes explicit.

3D and AR let an artifact be studied by everyone, everywhere, while the original
rests safely in its case.

## Frequently asked questions

### What is the best way to 3D scan an artifact?

Photogrammetry with a camera and good, even light works for most objects and is
inexpensive. Structured-light scanners give higher accuracy for small, detailed
pieces. Include a scale bar either way.

### Can museum visitors view artifacts in AR without an app?

Yes. A QR code on the label opens the model in the phone's browser, and the View
in AR button uses the AR viewer built into iOS and Android.

### How large can a 3D scan be for web sharing?

Keep the full-resolution scan as the archive copy and publish a decimated version
of a few hundred thousand triangles with compressed textures, so it loads quickly
on phones.

*[Publish a 3D artifact with annotations](/auth/register) and share it via a QR
code. See also [AR for archaeology](/ar-for-archaeology) and
[AR for museums](/ar-for-museums).*
""",
    },
    {
        "slug": "dicom-to-3d-model-guide",
        "title": "From DICOM to Interactive 3D: A Practical Guide for Researchers",
        "description": "How to turn a CT or MRI DICOM series into an interactive, layered 3D model with AR — what to export, how anonymization works, and what happens to the raw scan.",
        "date": "2026-09-24",
        "updated": "2026-10-19",
        "author": "AcademicAR Team",
        "tags": ["DICOM", "medical imaging", "how-to"],
        "persona": "Clinician-researchers & radiology teams",
        "read_minutes": 7,
        "body": """\
![CT slices turning into a 3D skull shown in AR on a phone](/static/images/blog/covers/dicom-to-3d-model-guide.webp)

Most medical 3D work starts in the same place: a stack of DICOM images from a CT or
MRI scanner. Turning that stack into something a reader can rotate, cut open, and
place on the desk in augmented reality used to take a segmentation workstation, a
mesh editor, and a web developer. Here is how to do it in one upload — and what to
think about before you press the button.

![From a DICOM series to an AR-ready 3D model in five steps](/static/images/blog/dicom-pipeline.svg)
*From an anonymized DICOM series to a GLB + USDZ model. The raw scan is deleted after conversion.*

## What a DICOM series actually is

A scan is not one file. It is a **series** of 2D slices, each a separate DICOM file
carrying pixel data plus metadata: slice position, pixel spacing, orientation, and
— importantly — patient identifiers. To rebuild the volume correctly, the converter
needs every slice of the series together, so you upload them as a single **ZIP**.

AcademicAR reads uncompressed series as well as the common compressed transfer
syntaxes (JPEG Lossless, JPEG-LS, JPEG 2000, and RLE), so exports straight from a
PACS or a research archive usually work as they are.

## Step 1: Anonymize before you export

The rule is simple: **remove patient identifiers before the data leaves your
institution.** Use your hospital's de-identification workflow or a tool such as
the anonymizer in your DICOM viewer, and follow your ethics approval. AcademicAR
asks you to confirm anonymization, rights, and ethics responsibility on every
upload, and medical uploads carry an extra confirmation on top of that.

Two further safeguards are built in:

- The **raw scan is deleted after conversion** — whether the conversion succeeds
  or fails. Only the resulting surface mesh is kept.
- Medical uploads are never archived or mirrored as source files, and patient
  identifiers are never written to logs.

![A CT slice stack and a threshold selecting bone](/static/images/blog/inline/dicom-to-3d-model-guide-a.webp)

## Step 2: Choose what to extract

A volume becomes a 3D model by deciding which voxels belong to a structure. For CT
you can pick one or more **threshold presets**:

| Preset | What it keeps | Works for |
|---|---|---|
| Bone | Dense tissue, 250 HU and above | CT |
| Skin | The outer body surface, -300 HU and above | CT |
| Contrast vessels | Contrast-filled vessels, 150 HU and above | Contrast CT |
| Auto threshold | An automatic (Otsu) split into two intensity classes | CT and MR |
| Custom range | Your own minimum (and optional maximum) in HU | CT |

Each preset you select becomes its own **layer** in the viewer, with its own colour.
Why the presets sit where they do is explained in
[Hounsfield units explained](/blog/hounsfield-units-ct-thresholding-3d).
If the presets aren't precise enough — for example, you need individual organs —
segment the scan first and upload the segmentation instead (see our
[guide to sharing segmentations](/blog/medical-image-segmentation-to-3d-viewer)).

## Step 3: Let the worker do the heavy lifting

Conversion runs in a background worker, not in your browser. It rebuilds the
volume, extracts a surface for each layer, smooths and decimates it to a size that
renders well on a phone, and produces a web-optimized **GLB** plus a **USDZ** for
iPhone and iPad AR. You can watch the progress on the model page.

## Step 4: Explore and share

The finished model opens in the viewer with a **Layers panel** (show, hide, fade,
or recolour bone, skin, and vessels independently), a **section plane** to cut
through it, and a **2D slice view** that shows the cut in the familiar axial,
coronal, and sagittal conventions. Share it with a stable link or QR code in your
paper, poster, or lecture slides.

![A layered skull model with a section plane in the viewer](/static/images/blog/inline/dicom-to-3d-model-guide-b.webp)

## A note on scope

These models are for **research communication and education**. They are surface
reconstructions, not diagnostic images, and they should not be used for clinical
decisions. If you need a physical model instead, see
[DICOM to STL for 3D printing](/blog/dicom-to-stl-3d-printing).

## Frequently asked questions

### Do I need to upload every DICOM file in the series?

Yes. The volume can only be rebuilt from all of its slices, so put the whole
series in a single ZIP and upload that one file.

### Which compressed DICOM files are supported?

Uncompressed series and the common compressed transfer syntaxes: JPEG Lossless,
JPEG-LS, JPEG 2000, and RLE. Exports straight from a PACS or a research archive
usually work as they are.

### Is the original scan kept after conversion?

No. The raw scan is deleted after conversion, whether it succeeds or fails, and
only the surface mesh is kept. Medical source files are never archived or
mirrored, and patient identifiers are never written to logs.

*[Create a free account](/auth/register) and turn your next anonymized scan into
an interactive figure.*
""",
    },
    {
        "slug": "hounsfield-units-ct-thresholding-3d",
        "title": "Hounsfield Units Explained: Choosing CT Thresholds for 3D Models",
        "description": "What Hounsfield units are, why bone, skin, and contrast-filled vessels separate at different HU values, and how to pick a preset or custom HU range for a clean 3D model.",
        "date": "2026-09-25",
        "updated": "2026-10-19",
        "author": "AcademicAR Team",
        "tags": ["DICOM", "CT", "Hounsfield units"],
        "persona": "Medical imaging researchers & students",
        "read_minutes": 6,
        "body": """\
![A skeleton above a CT density scale with a threshold marker](/static/images/blog/covers/hounsfield-units-ct-thresholding-3d.webp)

When you turn a CT scan into a 3D model, one number decides almost everything about
the result: the **threshold**. Set it too low and the bone model is buried in soft
tissue; set it too high and thin structures disappear. Understanding Hounsfield
units makes that choice predictable instead of trial and error.

## What a Hounsfield unit is

CT measures how strongly each voxel attenuates X-rays and rescales it to a
standard scale, the **Hounsfield unit (HU)**:

- Air is about **-1000 HU**.
- Water is **0 HU** by definition.
- Fat sits around **-100 HU**, most soft tissue between **+20 and +80 HU**.
- Contrast-enhanced blood often reaches **150-400 HU**.
- Cortical bone ranges from a few hundred to well over **1000 HU**.

Because the scale is calibrated, the same threshold means roughly the same tissue
across scanners — which is what makes presets possible. MRI, by contrast, has no
absolute intensity scale, so HU thresholds do not apply to it.

![The Hounsfield scale with typical tissue values and the Skin, Contrast and Bone preset thresholds](/static/images/blog/hounsfield-scale.svg)
*Typical tissue values on the Hounsfield scale, and where the CT presets cut.*

## The presets, and why they sit where they do

- **Bone (250 HU and above).** High enough to exclude soft tissue and most
  contrast, low enough to keep thinner cortical bone. Good for skulls, spines,
  pelvises, and fracture morphology.
- **Skin (-300 HU and above).** Everything denser than air counts as "body"; only
  the largest connected piece is kept, so the table and stray noise drop out.
  Useful for facial and body-surface models.
- **Contrast vessels (150 HU and above).** Captures contrast-filled vessels — but
  bone is just as bright, so a threshold alone cannot separate them. If you also
  select Bone, bone is subtracted from the vessel layer so the two layers do not
  overlap.
- **Auto threshold (Otsu).** Splits the volume into two intensity classes
  automatically. It is the only preset that makes sense for **MR**, and a quick
  first look for unfamiliar CT data.

![A custom density band highlighted on a vertebra slice](/static/images/blog/inline/hounsfield-units-ct-thresholding-3d-a.webp)

## When to use a custom range

Presets cover the common cases; research questions often don't. A **custom HU
range** lets you set your own minimum and, optionally, a maximum — for instance a
band that isolates dense calcifications, a bone-density window for a specific
specimen, or a soft-tissue band in a contrast study. Each range becomes its own
coloured layer, so you can compare bands side by side in the viewer.

![Thin bone lost at a high threshold and kept at a lower one](/static/images/blog/inline/hounsfield-units-ct-thresholding-3d-b.webp)

## Practical tips

1. **Start broad, then narrow.** Run one preset, inspect it with the section plane,
   then refine with a custom range.
2. **Watch thin structures.** Orbital walls, nasal turbinates, and small vessels
   are partial-volume voxels; a slightly lower threshold keeps them.
3. **Metal artefacts** from implants show up as streaks at very high HU — a maximum
   in your custom range can trim them.
4. **Need organs, not densities?** Thresholds separate densities, not anatomy.
   Segment first and upload the
   [segmentation](/blog/medical-image-segmentation-to-3d-viewer) for organ-level layers.

For the whole route from scan to shared model, see
[From DICOM to interactive 3D](/blog/dicom-to-3d-model-guide), and for a printed model,
[DICOM to STL for 3D printing](/blog/dicom-to-stl-3d-printing).

## Frequently asked questions

### What HU threshold should I use for bone?

The bone preset keeps everything at 250 HU and above, which excludes soft tissue
while keeping thinner cortical bone. If thin structures such as orbital walls
disappear, try a custom range with a slightly lower minimum.

### Can I use Hounsfield thresholds on MRI?

No. MRI has no absolute intensity scale, so HU values do not apply. Use the auto
threshold (Otsu) preset for a quick surface, or segment the MRI and upload the
segmentation.

### Why does my vessel model include bone?

Contrast-filled vessels and bone have similar HU values, so a threshold alone
cannot separate them. Select the Bone preset as well, and bone is subtracted from
the vessel layer.

*[Upload an anonymized CT series](/auth/register) and try the presets on your own
data.*
""",
    },
    {
        "slug": "medical-image-segmentation-to-3d-viewer",
        "title": "Sharing Medical Segmentations in 3D: NIfTI, NRRD, and DICOM-SEG",
        "description": "Publish segmentations from 3D Slicer, ITK-SNAP, or TotalSegmentator as an interactive, layered 3D model — one colour-coded layer per structure, ready for papers and teaching.",
        "date": "2026-09-26",
        "updated": "2026-10-19",
        "author": "AcademicAR Team",
        "tags": ["segmentation", "medical imaging", "3D Slicer"],
        "persona": "Medical imaging & AI researchers",
        "read_minutes": 6,
        "body": """\
![Segmented abdominal organs floating above scan slices](/static/images/blog/covers/medical-image-segmentation-to-3d-viewer.webp)

Segmentation is where much of the scientific value in medical imaging lives. Hours
of manual contouring or a carefully validated AI model produce label maps that
describe anatomy structure by structure — and then they end up as a single
screenshot in a figure. Here is how to share the segmentation itself.

## Formats you can upload directly

AcademicAR reads the segmentation formats your tools already write:

- **NIfTI** (`.nii`, `.nii.gz`) — the default for most research pipelines and AI
  models such as TotalSegmentator or nnU-Net.
- **NRRD**, including 3D Slicer's **`.seg.nrrd`** — segment names and colours are
  carried over from Slicer (see our
  [3D Slicer export guide](/blog/3d-slicer-export-share)).
- **DICOM-SEG** — the standard segmentation object used by PACS-connected tools.
- **A ZIP of masks** — one binary mask per structure, as many pipelines export them.

The converter checks the content, not just the extension: a file full of continuous
image intensities is recognised as an image, not a segmentation, so you get a
clear message instead of a meaningless model.

## One structure, one layer

Each non-empty label becomes a **separate layer** in the viewer, with up to 64
layers per model. That matters for readers:

- They can **show, hide, or fade** individual structures — hide the ribs to see the
  lungs, fade the liver to reveal the vessels inside it.
- They can **recolour** layers or apply a finish, and you can save a **default
  layer view** so everyone opens the model the way you intended.
- Names come along where the format provides them, so the legend reads "left
  kidney", not "label 7".

![A segmentation label map becomes one named, coloured layer per structure in the viewer](/static/images/blog/segmentation-layers.svg)
*Each non-empty label becomes its own layer that readers can show, hide, fade or recolour.*

Large multi-structure outputs are handled one mask at a time, so a full-body
TotalSegmentator result does not need special preparation.

![Segmented organs separated into individual layers](/static/images/blog/inline/medical-image-segmentation-to-3d-viewer-a.webp)

## Why share the segmentation, not just a render

- **Reviewers can check it.** Over- and under-segmentation is obvious when you can
  cut through the model with the
  [section plane](/blog/section-plane-slice-view-cross-sections).
- **AI papers become tangible.** Readers see what the model actually segments,
  in 3D, on their own phone.
- **Teaching gets a new resource.** A single labelled segmentation becomes an
  explorable atlas for a whole course.

## Privacy still applies

A segmentation can carry identifying metadata, and a skin surface can be
recognisable. Use de-identified data, follow your ethics approval, and avoid face
surfaces unless you have consent. The uploaded source file is deleted after
conversion; only the mesh is kept.

![Shortest distance between two segmented structures](/static/images/blog/inline/medical-image-segmentation-to-3d-viewer-b.webp)

## Measurements come built in

For layered models, AcademicAR computes per-structure **dimensions** and the
**distances between neighbouring structures** — see our post on
[layer measurements](/blog/measuring-anatomy-3d-layer-metrics).

## Frequently asked questions

### Which segmentation formats can I upload?

NIfTI (.nii and .nii.gz), NRRD including 3D Slicer's .seg.nrrd, DICOM-SEG, and a
ZIP with one binary mask per structure.

### How many structures can one model have?

Each non-empty label becomes its own layer, up to 64 layers per model. Large
multi-structure outputs, such as a full-body TotalSegmentator result, need no
special preparation.

### Are segment names and colours kept?

Yes, where the format provides them. A .seg.nrrd from 3D Slicer carries its
segment names and colours, so the legend reads "left kidney" rather than "label
7".

*[Publish your segmentation](/auth/register) as an interactive, layered 3D model.*
""",
    },
    {
        "slug": "section-plane-slice-view-cross-sections",
        "title": "Reading Cross-Sections in 3D: Section Planes and Colour-Coded Slice Views",
        "description": "How the section plane and the 2D slice view let readers cut through a 3D model, see every structure in its own colour, and measure on the cut — for anatomy and engineering alike.",
        "date": "2026-09-28",
        "updated": "2026-10-19",
        "author": "AcademicAR Team",
        "tags": ["section plane", "slice view", "cross-sections"],
        "persona": "Medical educators, engineers & architects",
        "read_minutes": 6,
        "body": """\
![A head model cut by a plane next to its colour-coded 2D slice](/static/images/blog/covers/section-plane-slice-view-cross-sections.webp)

A 3D model shows the outside of things. Most of the interesting questions are
about the inside: how a tumour sits against a vessel, how thick a wall is, how a
shaft passes through a housing. Cross-sections answer those questions, and they
are one of the oldest tools in both radiology and technical drawing. In AcademicAR
they work directly in the browser.

## The section plane

Open the **Section** panel and a plane cuts through the model along the X, Y, or
Z axis. Drag it through the model and everything in front disappears, revealing
the interior. A few details make it practical:

- **Axis guides** show where the plane is and how the model is oriented.
- **Face** turns the camera straight onto the cut, so you look at it squarely
  instead of at an angle.
- The cut respects the **Layers panel**: hide the skin and cut only the skeleton,
  or keep a faded outer shell for context.

![A section plane cutting through a head model](/static/images/blog/inline/section-plane-slice-view-cross-sections-a.webp)

## The 2D slice view

Next to the 3D cut, the **slice view** draws the cross-section as a flat 2D image.
Each structure's contour is drawn **in its own layer colour**, so in a segmented
scan you can immediately tell the liver from the portal vein from the vertebra —
just like a colour-coded segmentation overlay in a medical imaging workstation.

For scans, the slice view follows the conventions clinicians already know:

- Plane colours follow the 3D Slicer convention — **axial red, coronal green,
  sagittal yellow**.
- Orientation labels (**R/L, A/P, S/I**) sit at the edges, laid out in the
  radiological convention (patient right on the left of the screen).

For CAD and architectural models, it uses the familiar engineering colours
instead — X red, Y green, Z blue — with plus/minus axis labels.

![Axial, coronal and sagittal slice views with Slicer plane colours and radiological orientation labels](/static/images/blog/slice-view-conventions.svg)
*Slice view conventions for scans: each structure in its own colour, Slicer plane colours, radiological orientation.*

The slice view supports zoom and pan, shows a scale bar, and reports which
structures the plane currently cuts.

## Measuring on the cut

A two-point **measure** on the slice view snaps to the contours, so you measure
wall thickness, a canal diameter, or the gap between two parts precisely on the
section, not on an arbitrary surface point. Points that fall outside the solid —
in a gap between parts or inside a hollow — are refused, so you don't accidentally
measure empty space. For automatic dimensions and distances between
structures, see [layer measurements](/blog/measuring-anatomy-3d-layer-metrics).

![Measuring a wall thickness on a cut assembly](/static/images/blog/inline/section-plane-slice-view-cross-sections-b.webp)

## Where it helps

- **Anatomy teaching:** move an axial plane down the torso and relate the 3D model
  to the CT slices students will read later.
- **Medical research:** show the relationship of a lesion to surrounding
  structures at the exact level you discuss in the text.
- **Engineering:** inspect wall thicknesses, internal channels, and fits in an
  assembly without a CAD licence ([STEP assemblies in AR](/blog/step-cad-assemblies-ar-engineering)).
- **Architecture:** cut a building model to produce live plans and sections
  ([architecture models with sections](/blog/architecture-3d-models-sections-ar)).

## Frequently asked questions

### Do I need software to cut through a 3D model?

No. The section plane and the 2D slice view run in the browser, on the same viewer
link you share with readers.

### What do the colours in the slice view mean?

Each structure is drawn in its own layer colour. For scans, the plane colours
follow 3D Slicer (axial red, coronal green, sagittal yellow); for CAD and
architectural models, X is red, Y green, and Z blue.

### Can I measure on the cross-section?

Yes. The two-point measure on the slice view snaps to the contours, and points
that fall outside the solid are refused, so you measure the material rather than
empty space.

*[Upload a layered model](/auth/register) and cut through it in the browser.*
""",
    },
    {
        "slug": "measuring-anatomy-3d-layer-metrics",
        "title": "Measuring in 3D: Structure Dimensions and Distances Between Layers",
        "description": "Layered 3D models come with automatic measurements: oriented dimensions, maximum diameter, and the closest distance between neighbouring structures — useful in anatomy, morphology, and engineering.",
        "date": "2026-09-29",
        "updated": "2026-10-19",
        "author": "AcademicAR Team",
        "tags": ["measurement", "morphometrics", "segmentation"],
        "persona": "Researchers working with segmented or multi-part models",
        "read_minutes": 5,
        "body": """\
![A kidney in a bounding box and the closest distance to a vessel](/static/images/blog/covers/measuring-anatomy-3d-layer-metrics.webp)

"How big is it?" and "How close is it to that?" are among the first questions a
reader asks about a 3D structure. In a static figure the answer is a number in the
caption. In AcademicAR, layered models carry their own measurements, so readers
can see the numbers in context.

## What is measured

When a model has multiple layers — structures from a segmentation, presets from a
CT, or parts of a CAD assembly — the worker computes, for each layer:

- **Oriented dimensions:** the three edge lengths of the tightest box around the
  structure, aligned with the structure itself rather than the scanner axes. This
  gives a meaningful length, width, and depth even for an oblique organ.
- **Maximum diameter:** the largest distance between any two points of the
  structure — the familiar "longest axis" measure.

And between structures:

- **Minimum distance** between neighbouring layers, with the two closest points
  marked in the viewer. Pairs further than about 5 cm apart are skipped, so the
  list stays focused on relationships that matter.

Values are reported in **millimetres**, computed from the full-resolution geometry
before compression. Layer measurements are included in the paid model plans
([pricing](/pricing)).

![Oriented dimensions and maximum diameter of one layer, and the minimum distance between two layers](/static/images/blog/layer-metrics.svg)
*What is measured per layer and between neighbouring layers.*

![Oriented dimensions of a structure inside its bounding box](/static/images/blog/inline/measuring-anatomy-3d-layer-metrics-a.webp)

## Why this matters in medicine

- **Tumour-to-vessel distance** is central to resectability discussions; showing it
  on an interactive model makes the spatial relationship obvious.
- **Organ and lesion dimensions** support
  [case reports and teaching cases](/blog/3d-models-surgical-education-case-reports).
- **Anatomical variation** studies can present the measured structure and the
  number together.

![Clearance between two parts](/static/images/blog/inline/measuring-anatomy-3d-layer-metrics-b.webp)

## And in engineering and the sciences

- **Clearances in assemblies:** the minimum distance between two parts is a
  clearance check readers can inspect (see
  [STEP assemblies in AR](/blog/step-cad-assemblies-ar-engineering)).
- **Morphometrics** in biology, palaeontology, and archaeology: oriented dimensions
  and maximum diameter are standard descriptors of specimens.

## You stay in control

Measurements depend on the model's scale, so check units after upload; if you
rescale the model, its measurements are recomputed. The owner can also **hide the
measurements** from public viewers — useful when numbers are preliminary or when
the model is meant for orientation rather than quantification.

For precise point-to-point checks at a specific level, combine these with the
snapping measure in the [slice view](/blog/section-plane-slice-view-cross-sections).

## Frequently asked questions

### Which models get layer measurements?

Models with more than one layer: structures from a segmentation, presets from a
CT, or parts of a CAD assembly. Layer measurements are included in the paid model
plans.

### What units are the measurements in?

Millimetres, computed from the full-resolution geometry before compression. They
depend on the model's scale, so check the units after upload; rescaling the model
recomputes them.

### Can I hide the measurements from public viewers?

Yes. The owner can hide them, which is useful when numbers are preliminary or the
model is meant for orientation rather than quantification.

*[Publish a layered model](/auth/register) and let readers see the numbers in 3D.*
""",
    },
    {
        "slug": "3d-models-surgical-education-case-reports",
        "title": "Interactive 3D in Medical Education and Case Reports",
        "description": "How educators and clinician-researchers use layered 3D models, guided tours, and AR to teach anatomy, discuss complex cases, and enrich case reports — responsibly.",
        "date": "2026-09-30",
        "updated": "2026-10-19",
        "author": "AcademicAR Team",
        "tags": ["medical education", "case reports", "augmented reality"],
        "persona": "Medical educators & clinician-researchers",
        "read_minutes": 6,
        "body": """\
![Medical students around a 3D heart shown in AR on a tablet](/static/images/blog/covers/3d-models-surgical-education-case-reports.webp)

Medicine is taught and published in two dimensions — slides, atlases, journal
figures — yet the problems it deals with are spatial. Interactive 3D models built
from real imaging close that gap for students, residents, and readers of the
literature.

## In the classroom and the skills lab

- **From slices to anatomy.** Students struggle to connect axial CT slices to 3D
  anatomy. A model with a moving
  [section plane and a colour-coded slice view](/blog/section-plane-slice-view-cross-sections)
  lets them see both at once.
- **Layered dissection.** With one layer per structure, students peel away skin,
  muscle, and bone in order — and rebuild them — as often as they like.
- **AR at the bedside of the textbook.** On a phone or tablet, the model can be
  placed on the desk and walked around, with no app to install (more in
  [AR for anatomy education](/blog/augmented-reality-anatomy-education)).
- **Guided tours.** Save up to 12 views — each with its own camera, visible layers,
  colours, and section cut — and play them as a step-by-step tour. Each view gets
  its own link and QR code for handouts. (More in our post on
  [scenes and tours](/blog/saved-scenes-guided-tours-3d-teaching).)

![A seminar table with a pelvis model and a tablet showing it in AR](/static/images/blog/inline/3d-models-surgical-education-case-reports-a.webp)

## In case reports and clinical research papers

Complex anatomy — congenital heart disease, craniofacial deformities, pelvic
fractures, vascular anomalies — is notoriously hard to convey in a few static
figures. Linking an interactive model from the figure caption lets readers:

- rotate the anatomy and view it from the surgical approach,
- toggle structures to see what lies behind them,
- check [dimensions and distances](/blog/measuring-anatomy-3d-layer-metrics) that the text refers to.

A QR code next to the figure brings the same model to readers of the printed
journal or a conference poster.

![A case report page with a figure and a QR code in the caption](/static/images/blog/inline/3d-models-surgical-education-case-reports-b.webp)

## Doing it responsibly

- **De-identify** imaging before upload and follow your ethics approval and
  journal policy for patient images. Facial surface reconstructions deserve extra
  care.
- **Get consent** where your institution requires it for publication of case
  material.
- **Be clear about scope.** These are communication and teaching tools built from
  surface reconstructions, not diagnostic or planning software.

AcademicAR makes the compliance step explicit: every upload confirms
anonymization, rights, and ethics responsibility, medical uploads carry an extra
confirmation, and the raw scan is deleted once the 3D model is built.

## For departments

Institutional plans let a department or faculty fund models for all its members
from a shared quota, with a public showcase page for its teaching collection.
[Learn more about institutional plans](/institutional).

## Frequently asked questions

### How do I add a 3D model to a case report?

Upload the model, link its viewer page from the figure caption, and place its QR
code next to the printed figure. Our guide to [adding 3D models to research papers](/blog/how-to-add-3d-models-to-research-papers)
covers the details.

### Do students need an app to see the model in AR?

No. On a phone or tablet, the model opens in the browser and can be placed in AR
with the device's built-in viewer.

### Can these models be used for surgical planning?

No. They are communication and teaching tools built from surface reconstructions,
not diagnostic or planning software.

*[Create your first teaching model](/auth/register) from an anonymized scan or a
segmentation.*
""",
    },
    {
        "slug": "step-cad-assemblies-ar-engineering",
        "title": "From STEP to AR: Sharing CAD Assemblies in Engineering Research",
        "description": "Upload STEP/STP, FBX, or OBJ assemblies and share them as interactive 3D with one layer per part, cross-sections, clearances, and AR — for engineering papers, theses, and teaching.",
        "date": "2026-10-01",
        "updated": "2026-10-19",
        "author": "AcademicAR Team",
        "tags": ["engineering", "STEP", "CAD"],
        "persona": "Engineering researchers & educators",
        "read_minutes": 6,
        "body": """\
![An exploded gearbox assembly and the same model in AR on a phone](/static/images/blog/covers/step-cad-assemblies-ar-engineering.webp)

Engineering research is full of 3D objects — prototypes, test rigs, mechanisms,
fixtures, sensors — that end up as an isometric screenshot in a paper. Reviewers
can't see the hidden parts, students can't see how the mechanism fits together,
and the CAD file itself stays locked behind a commercial licence. Here's how to
share the assembly instead.

## Upload STEP directly

**STEP (`.step`/`.stp`)** is the neutral exchange format every CAD package can
export, from SolidWorks and Fusion to CATIA, Creo, Inventor, and FreeCAD.
AcademicAR converts STEP with OpenCascade (new to the format? see
[how to open a STEP file online](/blog/open-step-stp-file-online)):

- Each part becomes its own node, and **STEP colours** are carried over.
- Geometry is tessellated finely enough that holes and fillets stay smooth on small
  mechanical parts, with a cap on triangle count so the model still loads on a
  phone.
- The output is a compressed GLB plus a USDZ for iOS AR.

FBX, OBJ, STL, and GLB are accepted too, so meshes from simulation post-processing
or 3D scanning work the same way (see [OBJ and FBX to GLB](/blog/convert-obj-fbx-to-glb)).

## One part, one layer

Multi-part models get a **Layers panel** with one layer per part — repeated parts,
such as a set of bolts, are grouped into one layer. Readers can:

- hide the housing to see the gear train,
- fade a cover to show what sits behind it,
- recolour parts to match the colours used in your paper's figures.

![A STEP assembly tree converted into one viewer layer per part, with repeated parts grouped](/static/images/blog/step-assembly-layers.svg)
*Every part becomes a layer; repeated parts, such as bolts, are grouped.*

![A gearbox exploded into its parts](/static/images/blog/inline/step-cad-assemblies-ar-engineering-a.webp)

## Look inside with sections

The **section plane** cuts through the assembly along any axis, and the **2D slice
view** shows the cut as a clean, colour-coded section — a live version of the
section views in a technical drawing. The snapping measure on the slice reads wall
thicknesses, bore diameters, and gaps directly. (More in our post on
[section planes and slice views](/blog/section-plane-slice-view-cross-sections).)

## Clearances and dimensions, automatically

For layered models, the worker computes each part's oriented dimensions and the
**minimum distance between neighbouring parts** — a quick, visible clearance
check that readers can explore themselves.

![The same assembly on a laptop, a phone and a project poster](/static/images/blog/inline/step-cad-assemblies-ar-engineering-b.webp)

## Where engineers use it

- **Papers and theses:** link an interactive model from the figure caption so
  reviewers can inspect the design from every angle.
- **Teaching:** mechanisms, machine elements, and assemblies are far easier to
  understand when students can take them apart layer by layer — in AR on their
  desk.
- **Student projects and capstones:** a QR code on the project poster lets the
  jury explore the actual design at the exhibition.
- **Industry collaboration:** share a design review link without sending native
  CAD files.

## Keep intellectual property in mind

Confirm you have the rights to publish a design before uploading, especially for
work done with industry partners. A simplified or envelope model is often enough
for communication.

## Frequently asked questions

### Which CAD programs can export STEP?

Practically all of them, including SolidWorks, Fusion, CATIA, Creo, Inventor, and
FreeCAD. STEP is the neutral exchange format every CAD package can write.

### Are part colours and the assembly structure kept?

Yes. Each part becomes its own node and layer, STEP colours carry over, and
repeated parts such as a set of bolts are grouped into one layer.

### Do readers need a CAD licence to view the assembly?

No. The converted model opens in any modern browser and in AR on a phone, from a
link or a QR code.

*[Upload a STEP assembly](/auth/register) and share it as an interactive, layered
model. See also [AR for engineering](/ar-for-engineering).*
""",
    },
    {
        "slug": "architecture-3d-models-sections-ar",
        "title": "Architecture in 3D and AR: Sharing Building Models, Plans, and Sections",
        "description": "How architecture researchers, studios, and students can share building and heritage models online — with live sections, layer-by-layer exploration, saved views, and AR placement.",
        "date": "2026-10-02",
        "updated": "2026-10-19",
        "author": "AcademicAR Team",
        "tags": ["architecture", "heritage", "augmented reality"],
        "persona": "Architecture researchers, instructors & students",
        "read_minutes": 6,
        "body": """\
![A building model cut open by a section plane, placed in AR on a table](/static/images/blog/covers/architecture-3d-models-sections-ar.webp)

Architecture has always been communicated through drawings that are cuts through a
3D idea: plans, sections, elevations. Digital models made the 3D idea explicit, yet
most still reach their audience as rendered images. Interactive 3D lets the reader
walk around — and through — the building themselves.

## Getting the model out of your design tool

Most architecture tools export at least one format AcademicAR accepts:

- **Rhino, SketchUp, Blender, 3ds Max:** export **GLB/glTF**, **FBX**, or **OBJ**.
- **Revit and ArchiCAD:** export FBX or OBJ (directly or via a plug-in); see
  [OBJ and FBX to GLB](/blog/convert-obj-fbx-to-glb) for what happens on upload.
- **Photogrammetry and laser-scan meshes** of existing buildings and heritage sites:
  export OBJ or GLB with textures.
- **Fabrication-oriented models** from CAD tools: **STEP**.

Keep it light: simplify furniture and entourage, merge tiny details, and check the
units — a model in centimetres shown as metres will look a hundred times too big in
AR. Models can be rescaled after upload.

![A building exploded into systems](/static/images/blog/inline/architecture-3d-models-sections-ar-a.webp)

## Layers as building systems

When a model has multiple named parts, each becomes a **layer**. Organise your
export by building system — structure, envelope, floors, circulation, services,
landscape — and readers can switch them on and off, fade the façade to reveal the
structure, or recolour systems to match your diagrams.

## Live plans and sections

The **section plane** is a natural fit for architecture. Cut horizontally for a
live **plan**, vertically for a live **section**, and slide the plane to move
through the building floor by floor. The **2D slice view** shows the cut as a flat
drawing with a scale bar, and the snapping measure reads wall thicknesses and room
widths directly on the section. More in our post on
[section planes and slice views](/blog/section-plane-slice-view-cross-sections).

![A horizontal cut gives a live plan and a vertical cut gives a live section of a building model](/static/images/blog/architecture-cuts.svg)
*A horizontal plane gives a live plan, a vertical plane a live section, with a scale bar and a snapping measure.*

## Saved views and tours for reviews

Save up to 12 **scenes** — each with its own camera, visible layers, colours, and
section — and play them as a **tour**. A design review, a studio jury, or a
heritage-documentation walkthrough becomes a guided sequence anyone can open from
a link or a QR code (see [scenes and tours](/blog/saved-scenes-guided-tours-3d-teaching)).

![A pavilion placed in AR on a table](/static/images/blog/inline/architecture-3d-models-sections-ar-b.webp)

## AR on site and on the table

On a phone, the model can be placed in AR: a massing model on the review table, a
reconstructed heritage element next to its ruins, a pavilion at its real scale in
the courtyard where it will stand.

## Uses in research and teaching

- **Heritage and conservation:** publish scanned buildings and fragments with
  sections that reveal construction layers (see also
  [AR for archaeology](/blog/ar-for-archaeology-3d-artifacts)).
- **Design research:** attach the actual model to a paper on form, structure, or
  performance.
- **Studio teaching:** students submit an interactive model with their boards; the
  jury scans a QR code on the poster.
- **Building technology courses:** explode a façade or a structural system layer
  by layer.

## Frequently asked questions

### How do I view a Revit or SketchUp model in AR?

Export it as GLB, FBX, or OBJ (Revit via FBX or OBJ), upload it, and open the
viewer link or QR code on a phone. Tap View in AR to place the model on the table
or on site.

### Why is my building the wrong size in AR?

Usually a unit mismatch: a model in centimetres read as metres appears a hundred
times too big. Check the units before export; models can also be rescaled after
upload.

### Can I get a floor plan from the 3D model?

Yes. A horizontal section plane gives a live plan and a vertical one a live
section. The 2D slice view shows the cut as a flat drawing with a scale bar.

*[Upload your building model](/auth/register) and share it with live sections and
AR.*
""",
    },
    {
        "slug": "saved-scenes-guided-tours-3d-teaching",
        "title": "Saved Scenes and Guided Tours: Teaching with 3D Models Step by Step",
        "description": "Save up to 12 views of a 3D model — each with camera, layers, colours, and section cut — share each with its own link and QR code, and play them as a guided tour in class or in AR.",
        "date": "2026-10-03",
        "updated": "2026-10-19",
        "author": "AcademicAR Team",
        "tags": ["teaching", "scenes", "guided tours"],
        "persona": "Educators in medicine, engineering & architecture",
        "read_minutes": 5,
        "body": """\
![Five saved views of a skull connected as a guided tour](/static/images/blog/covers/saved-scenes-guided-tours-3d-teaching.webp)

A free-to-explore 3D model is wonderful for curious readers and overwhelming for
students on their first encounter. Good teaching sequences attention: first this,
then that, now look inside. **Scenes** bring that sequence to a 3D model.

## What a scene stores

A scene is a saved view of a model. It captures:

- the **camera** position and orientation,
- which **layers** are visible, their colours and transparency,
- the **section cut**, if one is active (see
  [section planes and slice views](/blog/section-plane-slice-view-cross-sections)),
- the background, orientation, and **lighting**.

You can save up to **12 scenes per model** and give each a name, such as "1. Skull —
lateral view", "2. Remove the mandible", or "3. Axial cut at the orbits".

![Three saved views of the same model](/static/images/blog/inline/saved-scenes-guided-tours-3d-teaching-a.webp)

## Share a scene on its own

Each scene has its own **link and QR code**. Put scene 3's QR code next to the
paragraph that discusses it, on the slide where you need it, or on the exam
handout. Readers land exactly on the view you prepared — with the right layers
visible and the right cut in place — and can then explore freely from there.

## Play it as a tour

The **tour** plays your scenes in order, moving smoothly from one to the next. In a
lecture it replaces a series of static screenshots with one live model; for
self-study, students step through the sequence at their own pace. To bring the
model into the slides themselves, see
[how to add a 3D model to PowerPoint](/blog/add-3d-model-to-powerpoint).

![Four saved scenes of a skull model played as a guided tour](/static/images/blog/scenes-tour.svg)
*A tour plays saved scenes in order; each scene also has its own link and QR code.*

## Scenes in AR

A scene can also be opened in AR with **only its visible layers, its colours, and
its section cut**, so a student placing "the heart, opened at the four-chamber
plane" on their desk sees exactly that — not the whole model.

![A projected 3D heart with step markers](/static/images/blog/inline/saved-scenes-guided-tours-3d-teaching-b.webp)

## Example sequences

**Anatomy:** whole thorax, then hide the ribs, then the lungs faded, then a coronal
cut through the heart, then an isolated vessel tree.

**Engineering:** closed gearbox, then housing hidden, then a section through the
shaft, then a close-up of the bearing seat.

**Architecture:** exterior massing, then façade faded, then a plan cut on each
floor, then a longitudinal section.

## Tips for good tours

1. **One idea per scene.** If a scene needs two sentences to explain, split it.
2. **Change one thing at a time** — a layer, a cut, or the camera — so students
   follow what changed.
3. **Number scene names** so the order is obvious when shared individually.
4. **Pair each scene with a question** in your handout: "What structure lies
   directly anterior to ...?"

## Frequently asked questions

### How many scenes can a model have?

Up to 12 saved scenes per model, each with its own name, camera, visible layers,
colours, section cut, background, and lighting.

### Can students open a single scene directly?

Yes. Each scene has its own link and QR code, so readers land on exactly that view
and can explore freely from there.

### Does AR show the scene or the whole model?

The scene. AR opens with only the scene's visible layers, its colours, and its
section cut.

*[Create your first tour](/auth/register) on any model you have uploaded.*
""",
    },
    {
        "slug": "publication-ready-3d-figures-comparisons",
        "title": "Publication-Ready Figures and Side-by-Side Comparisons from 3D Models",
        "description": "Export high-resolution figures from an interactive 3D model with a title, layer legend, scale bar, and QR code, and pair two models in a synchronized comparison for before/after and design studies.",
        "date": "2026-10-05",
        "updated": "2026-10-19",
        "author": "AcademicAR Team",
        "tags": ["scientific figures", "comparison", "publishing"],
        "persona": "Researchers preparing papers & posters",
        "read_minutes": 6,
        "body": """\
![Two femur models compared side by side on a figure with legend, scale bar and QR code](/static/images/blog/covers/publication-ready-3d-figures-comparisons.webp)

An interactive model is the best way to explore 3D results, but papers still need
printed figures, and many findings are comparisons: before and after, healthy and
diseased, design A and design B. Here's how to get both from the same models.

## Figures straight from the viewer

**Figure export** renders the current view at publication resolution — 2000, 3000,
or 4000 pixels wide, well beyond a screenshot — and composes it into a
ready-to-use figure with optional:

- a **title**,
- a **layer legend** listing each visible structure or part with its colour,
- a **scale bar** derived from the model's real dimensions,
- a **QR card** that links to the interactive model,
- a **white or transparent background**, ready to drop into a journal layout.

Because the figure is built from the same view as the model, you can set up layers,
colours, lighting, and a section cut first, then export — and export again later
from a saved scene if a reviewer asks for a different angle.

![An exported figure with title, model render, layer legend, scale bar and QR card](/static/images/blog/figure-export-anatomy.svg)
*The parts of an exported figure.*

![A composed figure with legend, scale bar and QR card](/static/images/blog/inline/publication-ready-3d-figures-comparisons-a.webp)

## Why the QR card matters

A printed figure is a single viewpoint. The QR card in the corner turns it into a
door to the full model: readers of the PDF or the printed poster scan it and
explore the geometry themselves (tips for sizing and placing it are in our
[poster QR guide](/blog/qr-codes-on-academic-posters-guide)). That is the most direct way to make a 3D result
both citable as a figure and explorable as data.

## Side-by-side comparisons

**Comparisons** pair two models in one view with their own public link. Typical
uses:

- **Medicine:** pre- and post-operative anatomy, two segmentation methods on the
  same scan, a normal and a variant anatomy.
- **Engineering:** two design iterations, a part before and after topology
  optimization, a simulated and a scanned geometry.
- **Architecture and heritage:** a site before and after restoration, a design
  proposal next to the existing building.
- **Biology and palaeontology:** two specimens or species for morphological
  comparison.

You can pair any two models from projects you are allowed to edit, and share the
comparison link or QR code in your paper or poster just like a single model.

Figure export and comparisons are included in the paid model plans — see
[pricing](/pricing) for details.

![Pre- and post-operative models side by side](/static/images/blog/inline/publication-ready-3d-figures-comparisons-b.webp)

## A workflow that holds up in peer review

1. Upload the models and organise layers and colours.
2. Save the key views as [scenes](/blog/saved-scenes-guided-tours-3d-teaching).
3. Export figures from those scenes with a legend, scale bar, and QR card.
4. Link the interactive model or **comparison** from the caption.
5. When a reviewer asks for another angle, open the scene, adjust, and export
   again — the figure and the model stay consistent.

For how this compares with general-purpose 3D hosting, see
[a Sketchfab alternative for researchers](/blog/sketchfab-alternative-researchers).

## Frequently asked questions

### What resolution are exported figures?

2000, 3000, or 4000 pixels wide, well beyond a screenshot, with a white or
transparent background.

### What can an exported figure include?

Optionally a title, a legend of the visible layers with their colours, a scale bar
from the model's real dimensions, and a QR card that links to the interactive
model.

### Which models can be compared side by side?

Any two models from projects you are allowed to edit. The comparison gets its own
public link and QR code. Figure export and comparisons are included in the paid
model plans.

*[Create a free account](/auth/register) and export your first publication figure
from a 3D model.*
""",
    },
    {
        "slug": "convert-stl-to-glb",
        "title": "How to Convert STL to GLB (and Why It Matters for Sharing)",
        "description": "Convert STL to GLB for the web: set units and colour, keep the file small with Draco, and get a viewer link, AR, and a QR code in one step.",
        "date": "2026-10-08",
        "updated": "2026-10-19",
        "author": "AcademicAR Team",
        "tags": ["STL", "GLB", "3D formats", "how-to"],
        "persona": "Researchers, engineers & makers",
        "read_minutes": 6,
        "body": """\
![A faceted STL mesh of a femur converted into a smooth, web-ready 3D model](/static/images/blog/covers/convert-stl-to-glb.webp)

STL is the format almost every 3D pipeline can write: segmentation software, CAD,
3D scanners, and slicers for 3D printing. GLB is the format browsers, phones, and
AR viewers want. If you need to show an STL to someone who doesn't have CAD or
printing software, converting it to GLB is the step in between. Here's what the
conversion actually does and how to get it right.

## STL and GLB in one paragraph each

**STL** stores a list of triangles and nothing else: no units, no colour, no
materials, no parts. Most STLs are also uncompressed, so a detailed scan or
segmentation can easily be tens or hundreds of megabytes.

**GLB** is the single-file, binary form of **glTF 2.0**, the open standard built for
real-time 3D on the web. One `.glb` holds geometry, materials, textures, and a
scene of named parts, and it supports compression. Browser viewers such as
Google's `<model-viewer>` and Android's AR viewer load GLB directly.

For a longer comparison with OBJ, FBX, and glTF, see our
[3D format guide](/blog/glb-stl-gltf-obj-3d-format-guide). Starting from OBJ or FBX
instead? See [OBJ and FBX to GLB](/blog/convert-obj-fbx-to-glb).

## What a good STL to GLB conversion does

A conversion is more than rewriting triangles into another container. To end up
with a model that looks right and loads fast, it should:

1. **Fix the scale.** STL has no units, so the same file can mean 120 mm or 120 m.
   You have to say which one it is, or AR will show your femur the size of a
   building.
2. **Add a material.** A plain STL renders as flat grey. Giving it a colour and a
   physically based material makes the shape readable.
3. **Clean up the mesh**, merging duplicate vertices so the file doesn't waste
   space, and add the texture coordinates some AR viewers expect.
4. **Compress the geometry.** Draco compression typically shrinks mesh data many
   times over, which is the difference between a model that opens instantly on
   mobile data and one that never finishes loading.

![The four conversion steps from an STL to a GLB, USDZ, viewer link and QR code](/static/images/blog/stl-to-glb-pipeline.svg)
*What the conversion adds to an STL, and what comes out.*

![A large STL shrinking into a small GLB](/static/images/blog/inline/convert-stl-to-glb-a.webp)

## Converting STL to GLB with AcademicAR

1. Create a project and choose **Upload model**.
2. Select your `.stl` file and pick the **source unit**: millimetres, centimetres,
   or metres. This is required for STL and OBJ because the file can't tell us.
3. Optionally set a **model colour**. Without one, the model is shown in a neutral
   material.
4. Confirm the compliance checkbox (that you have the rights to publish the model
   and that any patient data is anonymised) and upload.

Conversion runs in the background. When it finishes you get:

- an optimized **GLB** with Draco-compressed geometry,
- a **USDZ** companion, so iPhones and iPads can open the model in AR
  ([why iPhone needs USDZ](/blog/glb-to-usdz-iphone-ar)),
- a public **viewer link** that works in any modern browser,
- a **QR code** that opens the same model on a phone.

There is nothing to install and no command line involved.

## Tips for large STL files

- **Decimate before uploading.** For on-screen viewing, a few hundred thousand
  triangles is plenty. Tools like Meshmixer, Blender (Decimate modifier), or 3D
  Slicer's surface toolbox can reduce counts by 80–90% with no visible loss.
- **Split by part when it matters.** One STL is one mesh. If readers should be
  able to hide or recolour parts separately, export the parts to a format that
  keeps them apart, such as STEP, FBX, or GLB, or upload a segmentation that
  keeps one label per structure.
- **Check that the mesh is closed.** Holes and flipped faces in an STL often come
  from the original export and are easiest to fix at the source.

![A 3D printer and a browser viewer showing the same part](/static/images/blog/inline/convert-stl-to-glb-b.webp)

## Why not just share the STL?

You can attach an STL to a paper's supplementary material, and you should if
readers might 3D print it. But very few readers will download it, find software
that opens it, and set up the view. A GLB in a web viewer turns the same geometry
into something they can rotate in a second, on any device, and place on their desk
in AR. How sharing works is covered in
[how to view an STL online](/blog/view-stl-online-share-link).

## Frequently asked questions

### Does an STL file have units?

No. STL stores only triangles, so the same numbers can mean millimetres or metres.
Choose the source unit on upload so the model appears at its real size in AR.

### Can I convert STL to GLB without installing software?

Yes. Upload the STL in the browser; conversion runs in the background and returns
an optimized GLB, a USDZ for iPhone AR, a viewer link, and a QR code.

### Will the GLB have colour?

STL carries no material, so you can set a model colour on upload. Without one, the
model is shown in a neutral material.

*[Create a free account](/auth/register) and convert your first STL to a shareable
GLB with a viewer link and QR code.*
""",
    },
    {
        "slug": "view-stl-online-share-link",
        "title": "How to View and Share an STL File Online (No Software Needed)",
        "description": "Open an STL file in your browser and share it as a link and QR code that opens in 3D and AR on any phone. No CAD or slicer software needed.",
        "date": "2026-10-09",
        "updated": "2026-10-19",
        "author": "AcademicAR Team",
        "tags": ["STL", "3D viewer", "sharing", "how-to"],
        "persona": "Researchers, teachers & students",
        "read_minutes": 5,
        "body": """\
![A 3D model shared from a browser to a phone with a QR code](/static/images/blog/covers/view-stl-online-share-link.webp)

Someone sends you an STL file, or you need to send one. The usual answer is
"open it in a slicer" or "install a CAD viewer", which is fine for engineers and
a dead end for everyone else. Here are the ways to view an STL online, and how to
turn it into a link anyone can open.

## Option 1: a quick local preview

If you only need to look at a file yourself, a browser-based STL viewer that
renders the file locally is enough: drag the file in and rotate it. Your operating
system may also have a built-in preview (Windows 3D Viewer, macOS Quick Look).

The limitation is that nothing is shared. The model lives on your computer, and
the next person has to repeat the process with their own copy of the file.

## Option 2: a link someone else can open

To show an STL to a colleague, reviewer, student, or the audience at a talk, you
want a **hosted viewer**: the model lives at a URL, and anyone who opens it sees it
in 3D without downloading anything.

With AcademicAR the steps are:

1. Upload the `.stl` and choose its unit (mm, cm, or m) and, optionally, a colour
   (what the conversion does is explained in [STL to GLB](/blog/convert-stl-to-glb)).
2. The file is converted to an optimized GLB (plus a USDZ for iPhone AR).
3. Copy the **viewer link** or download the **QR code**.

Whoever opens the link can rotate, zoom, and pan, and on a phone tap **View in AR** to place the model on their desk at its
real size.

![A local STL preview compared with a hosted viewer link and QR code](/static/images/blog/stl-share-link.svg)
*A local preview is for you; a hosted link is for everyone else.*

![Viewer tools around a 3D model](/static/images/blog/inline/view-stl-online-share-link-a.webp)

## What readers can do in the viewer

Beyond turning the model around, the viewer includes tools that are useful for
real work:

- **Section plane** to cut through the model along any axis and look inside.
- **Slice view**, which shows that cut as a clean 2D cross-section with a
  snapping two-point measure.
- **Annotations and saved scenes**, so you can point at a feature and guide
  readers through the model in a fixed order.
- **Lighting presets and a light or dark background** to suit the model.
- **Screenshots** of the current view.

## A link that survives updates

A common problem with shared files is versioning: you fix the model and the copy
everyone has is now outdated. On AcademicAR you can **replace the model** while
the link and QR code stay the same, so a QR printed on a poster or in a thesis
keeps pointing at the current version. If a model's access window ends, the link
shows a clear notice instead of an error page.

![One 3D model reached from a thesis, a link and a phone](/static/images/blog/inline/view-stl-online-share-link-b.webp)

## Where this helps

- **Papers and theses:** put the link or QR code in the figure caption.
- **Teaching:** share one link with a whole class instead of a file per student,
  or [embed the viewer](/blog/embed-3d-model-website) in a course page.
- **Posters and talks:** a QR code turns a still image into a model the audience
  can explore on their phones (see our
  [poster QR guide](/blog/qr-codes-on-academic-posters-guide)).
- **Collaboration:** show a design or a segmentation to a co-author who doesn't
  have the software you used.

## Privacy note

If the STL comes from patient imaging, make sure it is anonymised and that you have
consent and ethics approval to publish it. Faces reconstructed from head CT or MRI
can be identifying even without any metadata.

## Frequently asked questions

### Can I view an STL file online without installing software?

Yes. A browser-based viewer previews the file on your own computer, and a hosted
viewer gives you a link that anyone can open in a browser.

### Do people need an account to open the link?

No. The public viewer opens without signing in, in any modern browser, and in AR
on a phone.

### What happens to the link if I fix the model?

Replace the model and the link and QR code stay the same, so printed QR codes keep
pointing at the current version.

*[Create a free account](/auth/register) to share your STL as a link and QR code.*
""",
    },
    {
        "slug": "open-step-stp-file-online",
        "title": "What Is a STEP (.stp) File and How to Open It Without CAD",
        "description": "STEP and STP files explained: what's inside, which programs open them, and how to view and share a STEP assembly in your browser and AR without CAD.",
        "date": "2026-10-10",
        "updated": "2026-10-19",
        "author": "AcademicAR Team",
        "tags": ["STEP", "CAD", "3D viewer", "how-to"],
        "persona": "Engineers, students & collaborators",
        "read_minutes": 6,
        "body": """\
![A cutaway CAD valve assembly, half exact surfaces and half triangle mesh](/static/images/blog/covers/open-step-stp-file-online.webp)

A `.step` or `.stp` file is the most common way CAD models travel between people
who don't use the same software. If you've received one and don't have
SolidWorks, Fusion, or CATIA, or you need to show one to someone who doesn't, this
guide covers what the file is and the easiest ways to open it.

## What a STEP file is

**STEP** (Standard for the Exchange of Product model data, ISO 10303) is a neutral
CAD format. The `.step` and `.stp` extensions are the same thing. Unlike mesh
formats such as STL (see our [3D format guide](/blog/glb-stl-gltf-obj-3d-format-guide)), a STEP file describes **exact geometry**: surfaces, edges,
and solids defined mathematically, organised into an **assembly tree** of parts,
often with colours and part names.

That precision is why manufacturers and engineers exchange STEP. It is also why
STEP files can't be shown directly in a browser: the exact surfaces first have to
be turned into triangles (tessellated) before a 3D viewer or AR app can draw them.

![Exact STEP surfaces tessellated into a triangle mesh](/static/images/blog/step-exact-vs-mesh.svg)
*Exact surfaces are tessellated into triangles before a browser or AR app can draw them.*

![An assembly tree and its parts](/static/images/blog/inline/open-step-stp-file-online-a.webp)

## Ways to open a STEP file

- **Desktop CAD:** any major CAD package imports STEP. Free options include
  **FreeCAD** and Autodesk's free viewers.
- **Dedicated viewers:** several free desktop and browser viewers open STEP for
  inspection on your own machine.
- **A hosted 3D viewer:** convert once, then open it anywhere, including on a
  phone, and share it with others as a link.

The first two are good for your own inspection. The third is what you want when
the goal is to **show** the model to reviewers, students, or a project partner.

## Opening and sharing STEP with AcademicAR

Upload the `.step` or `.stp` file to a project. The conversion uses OpenCascade,
the open-source CAD kernel, and runs in the background:

- Each part of the assembly becomes its own node, and **STEP colours** carry over.
- Surfaces are tessellated finely enough that holes and fillets stay smooth on
  small mechanical parts, with a cap on triangle count so the model still loads on
  a phone.
- The result is a compressed **GLB** for browsers and Android AR and a **USDZ**
  for iPhone and iPad AR ([why both](/blog/glb-to-usdz-iphone-ar)).
- STEP units are kept as authored, so the model appears at its real size in AR.

You get a viewer link and a QR code. Anyone can open the assembly in their
browser, with no CAD licence and nothing to install.

## Assemblies become layers

Multi-part models get a **Layers panel** with one layer per part, and repeated
parts such as a set of bolts are grouped. Viewers can hide the housing to see the
mechanism, fade a cover, or recolour parts. The **section plane** and **2D slice
view** cut through the assembly like a section view on a drawing. For more on
engineering use, see [From STEP to AR](/blog/step-cad-assemblies-ar-engineering).

![Simplifying a large assembly](/static/images/blog/inline/open-step-stp-file-online-b.webp)

## Troubleshooting large STEP files

- **Very large assemblies** can take a while to tessellate. If conversion times
  out, export the sub-assembly you actually need to show, or simplify small
  features (threads, text, fasteners) before export.
- **Missing colours** usually mean the CAD export didn't include them. Most CAD
  tools have an option to export colours or appearance with STEP.
- **IP and confidentiality:** confirm you have the right to publish a design
  before uploading, especially for work with industry partners. An envelope or
  simplified model is often enough.

## Frequently asked questions

### Is a .stp file different from a .step file?

No. Both extensions are the same STEP format (ISO 10303) and open in the same
tools.

### Can I open a STEP file on a phone?

Not directly, because phones need triangle meshes. Convert the STEP to GLB and
USDZ, for example by uploading it to a hosted viewer, and open the link or QR code
on the phone.

### Why are the colours missing from my STEP model?

The CAD export probably didn't include them. Most CAD tools have an option to
export colours or appearance with STEP; re-export with it switched on.

*[Upload a STEP file](/auth/register) and open it in your browser and in AR,
without CAD software.*
""",
    },
    {
        "slug": "how-to-open-glb-file",
        "title": "How to Open a GLB File on Desktop, iPhone, and Android",
        "description": "What a GLB file is and how to open it on Windows, Mac, iPhone, and Android, why iPhones need USDZ for AR, and how to share a GLB as a link.",
        "date": "2026-10-12",
        "updated": "2026-10-19",
        "author": "AcademicAR Team",
        "tags": ["GLB", "glTF", "augmented reality", "how-to"],
        "persona": "Anyone sharing 3D models",
        "read_minutes": 5,
        "body": """\
![The same skull model on a laptop, an iPhone in AR, and an Android phone](/static/images/blog/covers/how-to-open-glb-file.webp)

GLB has become the default format for 3D on the web, so more and more people
receive `.glb` files without knowing what to do with them. Here's what the format
is and how to open it on each device.

## What is a GLB file?

A **GLB** file is the binary, single-file version of **glTF 2.0**, an open standard
from the Khronos Group often called "the JPEG of 3D". One `.glb` contains the
model's geometry, materials, textures, and, where present, animations and a tree
of named parts. It is designed to load quickly in real-time viewers, which is why
browsers, game engines, and AR tools support it. For how it compares with STL,
OBJ, and FBX, see our [3D format guide](/blog/glb-stl-gltf-obj-3d-format-guide).

![A GLB file and the model it contains](/static/images/blog/inline/how-to-open-glb-file-a.webp)

## Opening a GLB on Windows and Mac

- **Windows:** Microsoft's **3D Viewer** app opens GLB where it is installed, and
  Microsoft 365 apps such as PowerPoint can insert GLB files as 3D models
  (see [how to add a 3D model to PowerPoint](/blog/add-3d-model-to-powerpoint)).
- **Mac:** Apple's built-in previews are built around USDZ rather than GLB, so use
  a browser-based viewer or a 3D app such as Blender, which imports glTF/GLB.
- **Any computer:** drag the file into a browser-based glTF viewer. This is the
  fastest option and needs no installation.

## Opening a GLB on Android

Android's **Scene Viewer** (part of Google Play Services for AR) displays GLB
models in 3D and in AR. Web pages built with Google's `<model-viewer>` hand the
model to Scene Viewer when you tap the AR button.

## Opening a GLB on iPhone and iPad

This is where most people get stuck. iOS **AR Quick Look** does not use GLB: it
expects **USDZ**, Apple's format. A GLB will display in a web viewer on an iPhone,
but to place it in your room in AR you need a USDZ version of the same model
(see [GLB to USDZ for iPhone AR](/blog/glb-to-usdz-iphone-ar)).

![Which file a desktop browser, Android and iPhone use from one viewer link](/static/images/blog/glb-devices.svg)
*One link serves GLB to browsers and Android, and USDZ to iPhone AR.*

## Sharing a GLB so it opens everywhere

If the goal is for other people to see the model, the most reliable route is a
hosted viewer that handles every device for you. Upload the `.glb` to AcademicAR
and you get:

- an optimized GLB (Draco geometry compression, webp textures) that loads fast on
  mobile,
- a **USDZ companion generated automatically**, so **View in AR** works on iPhone
  and iPad as well as Android,
- a **viewer link** that opens in any browser, and a **QR code** for print,
- for multi-part GLBs, a **Layers panel** with one layer per part.

GLB units are kept as authored, so AR shows the model at the size it was modelled.

![The same model exported at the wrong scale](/static/images/blog/inline/how-to-open-glb-file-b.webp)

## Common GLB problems

- **The model is huge or tiny in AR:** the exporter used the wrong unit. glTF is
  defined in metres, so a model exported in millimetres appears 1000 times too
  large. Fix the scale in the source application and re-export.
- **Textures are missing:** a `.gltf` file references external images, while a
  `.glb` embeds them. Export as GLB (binary) to keep everything in one file.
- **The file is too large to share:** compress geometry with Draco and textures
  with webp or KTX2, or let the upload optimizer do it.

## Frequently asked questions

### What is the difference between GLB and glTF?

They are the same standard. A .gltf file references external files such as images,
while a .glb packs everything into one binary file, which is easier to share.

### Can I open a GLB on an iPhone?

In a web viewer, yes. For AR, iOS needs a USDZ version of the model; AcademicAR
generates one automatically when you upload a GLB.

### Why is my GLB huge or tiny in AR?

glTF is defined in metres, so a model exported in millimetres appears 1000 times
too large. Fix the scale in the source application and export again.

*[Upload a GLB](/auth/register) and get a link that opens in 3D and AR on every
device.*
""",
    },
    {
        "slug": "3d-slicer-export-share",
        "title": "3D Slicer: Export Models and Share Them as Interactive 3D",
        "description": "Export segmentations from 3D Slicer as STL, OBJ, or .seg.nrrd and share them as a layered 3D viewer link with AR and a QR code, step by step.",
        "date": "2026-10-13",
        "updated": "2026-10-19",
        "author": "AcademicAR Team",
        "tags": ["3D Slicer", "segmentation", "medical imaging", "how-to"],
        "persona": "Medical imaging researchers & clinicians",
        "read_minutes": 7,
        "body": """\
![CT slices turned into a 3D kidney model with its vessels highlighted](/static/images/blog/covers/3d-slicer-export-share.webp)

3D Slicer is one of the most widely used free tools for segmenting CT and MRI
data. Getting a segmentation **out** of Slicer and in front of reviewers, students,
or colleagues is a separate problem. This guide covers the export options and how
to turn the result into a 3D link anyone can open.

## Two routes out of Slicer

There are two good ways to move a Slicer segmentation to a shareable viewer:

1. **Export surface models (STL or OBJ).** Slicer builds the 3D surfaces and you
   upload the meshes.
2. **Save the segmentation itself (`.seg.nrrd`).** You upload the label map and
   the surfaces are built on upload, one layer per segment.

Route 2 is usually better for multi-structure segmentations, because segment names
and colours travel with the file. Route 1 is useful when you have already
smoothed, decimated, or edited the surfaces in Slicer. If the goal is a 3D print
rather than a viewer, see [DICOM to STL for 3D printing](/blog/dicom-to-stl-3d-printing).

![Two routes from a 3D Slicer segmentation to a shareable 3D viewer link](/static/images/blog/slicer-export-routes.svg)
*Export surface models, or save the segmentation itself.*

![A segmented model with three export files](/static/images/blog/inline/3d-slicer-export-share-a.webp)

## Route 1: export STL or OBJ from Slicer

1. Open the **Segmentations** module and select your segmentation.
2. Expand **Export/import models and labelmaps**.
3. Choose **Export to files**, pick the output folder, and set the format to
   **STL** or **OBJ**.
4. Tick **Merge into single file** if you want one file for all segments. Leave
   it unticked for one file per segment.
5. Check the **size scale** and **coordinate system** settings. Slicer works in
   millimetres, so keep that unit in mind for the next step.

Then upload the file to AcademicAR and set the **source unit to millimetres**. A
single STL becomes one mesh. If readers should toggle structures separately,
prefer OBJ or the `.seg.nrrd` route below.

## Route 2: save the segmentation as .seg.nrrd

1. In Slicer, use **File → Save** (or **Save Data**).
2. Make sure the segmentation node is ticked and saved as **`.seg.nrrd`**.
3. Upload that file to AcademicAR as a **medical segmentation**.

On upload, each non-empty segment becomes its own named, coloured layer, with up
to 64 layers per model. NIfTI (`.nii`/`.nii.gz`), DICOM-SEG, and a ZIP of masks
work the same way if your pipeline produces those instead (see our post on
[segmentation formats](/blog/medical-image-segmentation-to-3d-viewer)).

Medical uploads need an extra confirmation that the data is anonymised and that
you have the right to share it. The raw file is deleted after conversion.

## What your readers get

The result is a viewer link and a QR code. Readers can:

- **show, hide, fade, or recolour** each structure in the Layers panel,
- cut through the model with the **section plane** and read the cut in the
  colour-coded **slice view**,
- see **structure dimensions and distances between layers** where layer
  measurements are enabled,
- open the model in **AR** on iPhone or Android at its real size.

You can also save a **default layer view** and **scenes** that walk readers through
the anatomy step by step.

## Starting from the DICOM instead

If you haven't segmented yet and only need bone or skin surfaces, you can skip
Slicer and upload the DICOM series as a ZIP with a threshold preset or a custom
Hounsfield range. See [From DICOM to Interactive 3D](/blog/dicom-to-3d-model-guide)
and [Hounsfield units explained](/blog/hounsfield-units-ct-thresholding-3d).

![Removing identifying facial features before publishing](/static/images/blog/inline/3d-slicer-export-share-b.webp)

## Before you publish

- Remove faces and identifying features from head and neck reconstructions.
- Confirm consent and ethics approval cover publication of the 3D data.
- Name your segments clearly in Slicer: those names become the legend your readers
  see.

## Frequently asked questions

### What is the best format to export from 3D Slicer?

For sharing a multi-structure segmentation, save it as .seg.nrrd: segment names
and colours travel with the file and each segment becomes its own layer. Export
STL or OBJ when you have already edited the surfaces in Slicer.

### Which unit should I choose for a Slicer STL?

Millimetres. Slicer works in millimetres, so set the source unit to mm on upload.

### Can readers toggle segments in a merged STL?

No. A single STL becomes one mesh. Use OBJ or the .seg.nrrd route if readers
should show and hide structures separately.

*[Create a free account](/auth/register) and share your first Slicer segmentation
as an interactive 3D model.*
""",
    },
    {
        "slug": "embed-3d-model-website",
        "title": "How to Embed a 3D Model on a Website, LMS, or Lab Page",
        "description": "Two ways to embed an interactive 3D model on a web page: self-host with model-viewer, or paste a hosted viewer iframe into WordPress, Moodle, or Canvas.",
        "date": "2026-10-14",
        "updated": "2026-10-19",
        "author": "AcademicAR Team",
        "tags": ["embedding", "3D viewer", "teaching", "how-to"],
        "persona": "Lab website owners & educators",
        "read_minutes": 6,
        "body": """\
![A web page with an embedded interactive 3D molecule viewer](/static/images/blog/covers/embed-3d-model-website.webp)

A lab website, a course page, or a project site is a natural home for the 3D
models behind your research. There are two practical ways to put one there: build
the viewer yourself, or embed a hosted viewer. Here's how each works and when to
pick which.

## Option 1: self-host with model-viewer

Google's open-source **`<model-viewer>`** web component is the standard way to show
a [GLB](/blog/how-to-open-glb-file) on a web page. The minimal version is two lines:

```html
<script type="module" src="https://ajax.googleapis.com/ajax/libs/model-viewer/4.3.0/model-viewer.min.js"></script>
<model-viewer src="model.glb" camera-controls ar alt="A 3D model"></model-viewer>
```

This works well if you control the site and are comfortable with HTML. You are
then responsible for:

- **Converting** your source files to an optimized GLB (and compressing them),
- **Producing a USDZ** if you want AR on iPhones (`ios-src`),
- **Hosting** the files with the right content types and caching,
- **Updating** every page by hand when the model changes.

![A web page with an embedded 3D viewer](/static/images/blog/inline/embed-3d-model-website-a.webp)

## Option 2: embed a hosted viewer

If your site is WordPress, Squarespace, Google Sites, a university CMS, or an LMS
such as **Moodle** or **Canvas**, you often can't add scripts, but you can paste an
**iframe**. A hosted viewer gives you an iframe that already handles conversion,
compression, AR, and updates.

With AcademicAR:

1. Upload the model (GLB, STL, OBJ, FBX, STEP, or a medical scan or segmentation).
2. On the project page, click **Embed** next to the model. This copies an iframe
   snippet like:

```html
<iframe src="https://academicar.com/view/<model-id>?embed=true"
        width="100%" height="480" style="border:0;border-radius:8px"
        allow="xr-spatial-tracking; fullscreen" allowfullscreen loading="lazy"></iframe>
```

3. Paste it into an HTML or embed block on your page.

Visitors can rotate and zoom the model in the page, open it full screen, and open
it in AR on their phones. If you later **replace the model**, every page that
embeds it shows the new version, with no edits on your side.

## Which should you choose?

| | Self-hosted model-viewer | Hosted embed |
|---|---|---|
| Setup | HTML and file hosting | Paste an iframe |
| Works in LMS / CMS without scripts | Rarely | Yes |
| Conversion from STL, STEP, DICOM | Do it yourself | Automatic |
| iPhone AR (USDZ) | Generate it yourself | Automatic |
| Layers, section plane, slice view | Build it yourself | Included |
| Updating the model | Re-upload on every site | Replace once |

Self-hosting gives full control over look and code. A hosted embed is faster and
easier to maintain, especially when the same model also appears in a paper, a
poster QR code, and a course page, or in your slides (see
[3D models in PowerPoint](/blog/add-3d-model-to-powerpoint)).

![A course page with an embedded 3D skeleton](/static/images/blog/inline/embed-3d-model-website-b.webp)

## Tips for embeds that work well

- **Give the iframe a descriptive `title`** for screen readers. The copied snippet
  includes the model name.
- **Use `loading="lazy"`** so pages with several models don't load them all at
  once.
- **Keep the height at 400–600 px** on desktop. On phones, the full-screen button
  gives readers more room.
- **Add a short caption** with what the reader should look for, just like a figure.

## Frequently asked questions

### Can I embed a 3D model in Moodle or Canvas?

Yes. Learning platforms usually block scripts but accept an iframe, so paste the
copied embed code into an HTML or embed block.

### Does AR work in an embedded viewer?

Yes. Visitors can rotate and zoom in the page, open it full screen, and open the
model in AR on their phones.

### Do I need to update the embed when the model changes?

No. Replace the model once and every page that embeds it shows the new version.

*[Create a free account](/auth/register), upload a model, and copy its embed code.
See also [AR for STEM education](/ar-for-stem-education).*
""",
    },
    {
        "slug": "convert-obj-fbx-to-glb",
        "title": "How to Convert OBJ and FBX to GLB Without Losing Textures",
        "description": "Convert OBJ (with its MTL and textures) and FBX to GLB for the web: what each format carries, the free tools, and how to keep materials, units, and parts intact.",
        "date": "2026-10-15",
        "author": "AcademicAR Team",
        "tags": ["OBJ", "FBX", "GLB", "3D formats", "how-to"],
        "persona": "Researchers, 3D artists & engineers",
        "read_minutes": 7,
        "body": """\
![Loose OBJ mesh, texture tiles and material sheet merging into one GLB model](/static/images/blog/covers/convert-obj-fbx-to-glb.webp)

OBJ and FBX are two of the most common formats you'll get out of scanners, modelling
tools, and CAD exports. Neither is what a browser or phone wants to load: the web
runs on **GLB**, the single-file form of glTF 2.0. Converting is easy to get
*almost* right, and the usual failures are the same every time: missing textures,
a model the size of a building, or a multi-part object welded into one grey lump.
Here's how to avoid all three.

## What OBJ and FBX actually carry

**OBJ** is a plain-text mesh format. The `.obj` file holds geometry and texture
coordinates, but its colours and materials live in a separate **`.mtl`** file, and
the images that file points to (`.png`, `.jpg`) sit next to it. Lose any of those
and the model arrives untextured. OBJ also has **no units**.

**FBX** (Autodesk) is a binary scene format. It can hold several meshes, a
hierarchy of parts, materials, textures (embedded or referenced), and the unit the
scene was modelled in. That makes it richer than OBJ, but it is proprietary, so
web viewers can't read it directly.

![What an OBJ and an FBX bring into a GLB: separate files versus one file with units](/static/images/blog/obj-fbx-to-glb.svg)
*An OBJ needs its MTL and textures alongside it; an FBX brings its own units.*

For a side-by-side of all the common formats, see
[GLB vs STL vs glTF vs OBJ](/blog/glb-stl-gltf-obj-3d-format-guide).

![Textures merging back onto a model](/static/images/blog/inline/convert-obj-fbx-to-glb-a.webp)

## Free tools that convert OBJ and FBX to GLB

- **Blender.** Import the OBJ or FBX, check the scale and materials, then
  *File → Export → glTF 2.0* and choose the binary `.glb` option. The most
  control, and the most clicks.
- **obj2gltf.** An open-source command-line converter from Cesium for OBJ. Point
  it at the `.obj` and it picks up the `.mtl` and textures from the same folder.
- **FBX2glTF.** An open-source command-line converter for FBX that writes glTF
  materials (physically based rendering) from FBX materials.

All three work. What they don't do is host the result, make it open in AR on an
iPhone, or give you a link to send.

## Converting OBJ and FBX with AcademicAR

AcademicAR runs the same open-source converters for you and then does the web
work on top:

1. Create a project and choose **Upload model**.
2. **For OBJ:** select the `.obj`, then add its **companion files** (the `.mtl` and
   the texture images it references) in the field that appears. Pick the
   **source unit** (mm, cm, or m), because OBJ can't tell us.
3. **For FBX:** select the `.fbx`. The unit is read from the file, so
   **Embedded** is selected for you; you can override it if the exporter got it
   wrong.
4. Confirm the compliance checkbox and upload.

The result is an optimized **GLB** (Draco-compressed geometry, WebP textures), a
**USDZ** for iPhone AR, a viewer link, and a stable QR code.

## Keep parts separate

If your OBJ or FBX contains several named objects, they become separate
**layers** in the viewer: readers can hide, isolate, and recolour each part. Name
the parts in your modelling tool before exporting ("housing", "rotor", "left
kidney"), because those names become the layer names. One merged object stays
one layer.

![A robot arm split into named layers](/static/images/blog/inline/convert-obj-fbx-to-glb-b.webp)

## Troubleshooting

- **The model is untextured.** For OBJ, the `.mtl` or the images weren't uploaded,
  or the `.mtl` refers to them by an absolute path from the author's computer.
  Open the `.mtl` in a text editor and make the texture paths plain file names.
- **The model is huge or tiny.** The unit was wrong. You can change it later on
  the model's edit page without re-uploading.
- **Everything is one colour.** The source had no materials, or all parts shared
  one. Recolour layers in the viewer, or assign distinct materials before export.

## Frequently asked questions

### Can I convert OBJ to GLB without losing textures?

Yes, as long as the converter can find the `.mtl` file and every texture image it
references. Keep them in the same folder (or upload them as companion files) and
make sure the `.mtl` uses relative file names rather than absolute paths.

### Does FBX to GLB keep the original units?

FBX stores the unit the scene was modelled in, and good converters bake it into
the GLB in metres, which is what glTF expects. If the exporter wrote the wrong
unit, override it rather than rescaling the mesh by hand.

### Is GLB better than OBJ for sharing?

For the web, yes. A GLB is one compact file with geometry, materials, textures,
and named parts, and it loads directly in browsers and Android AR. OBJ is fine as
a working format but splits a model across several files.

*[Create a free account](/auth/register) and upload an OBJ or FBX to get a GLB,
AR, and a shareable link in one step.*
""",
    },
    {
        "slug": "glb-to-usdz-iphone-ar",
        "title": "GLB to USDZ: How to Show a 3D Model in AR on iPhone",
        "description": "Why iPhone AR needs USDZ instead of GLB, the free ways to convert GLB to USDZ, and how to share one link that opens AR on both iPhone and Android.",
        "date": "2026-10-16",
        "author": "AcademicAR Team",
        "tags": ["USDZ", "GLB", "augmented reality", "how-to"],
        "persona": "Anyone sharing 3D models in AR",
        "read_minutes": 6,
        "body": """\
![An iPhone showing an anatomical heart model placed on a desk in augmented reality](/static/images/blog/covers/glb-to-usdz-iphone-ar.webp)

You have a GLB that opens in the browser and in AR on Android, and on an iPhone
the **View in AR** button does nothing useful. That's not a bug in your model:
Apple's AR viewer, **AR Quick Look**, uses a different file format, **USDZ**. To
reach both platforms you need both files, or a viewer that serves the right one to
each device.

## Why iPhone needs USDZ

**GLB** (glTF 2.0) is the open standard used by browsers, Android's **Scene
Viewer**, and most web 3D tools. **USDZ** is Apple's packaged version of Pixar's
Universal Scene Description, and it is what AR Quick Look opens on iPhone and
iPad. Quick Look launches straight from Safari (and from other browsers on iOS
when the page links the USDZ properly), with no app to install.

Some web viewers can generate a USDZ in the browser on the fly, but that only
works in Safari and fails in in-app browsers, which is exactly where people open
links from email and messaging apps. A real USDZ file next to the GLB is the
reliable route.

![One upload produces a GLB for browsers and Android and a USDZ for iPhone AR](/static/images/blog/glb-usdz-ar.svg)
*One link, two files: GLB for browsers and Android, USDZ for iPhone AR.*

![An iPhone and an Android phone showing AR](/static/images/blog/inline/glb-to-usdz-iphone-ar-a.webp)

## Free ways to convert GLB to USDZ

- **Reality Converter** (free, macOS). Drag in a GLB, check the preview and the
  materials, and export a USDZ.
- **Blender.** Import the GLB (*File → Import → glTF 2.0*) and export with the
  USD exporter, choosing `.usdz`.
- **A hosted viewer that does it for you**, so you never handle two files.

Whichever you use, open the USDZ on an iPhone before you share it. The two
problems you'll see are scale (the model appears at the wrong size) and
materials that don't carry over exactly.

## Automatic USDZ with AcademicAR

When you upload a model (GLB, STL, OBJ, FBX, STEP, or a medical scan), AcademicAR
builds the web GLB and a **USDZ companion** from it automatically, with the
real-world scale written into the file. The public viewer link then does the
right thing on every device:

- in a desktop browser, the model is interactive in 3D;
- on Android, **View in AR** hands the GLB to Scene Viewer;
- on iPhone and iPad, **View in AR** opens the USDZ in AR Quick Look.

If you recolour the model, change its layers, or replace the file, the USDZ is
rebuilt so iPhone AR stays in step with what the browser shows. The QR code
doesn't change.

![A figurine in AR beside a ruler for scale](/static/images/blog/inline/glb-to-usdz-iphone-ar-b.webp)

## Tips for good AR on iPhone

- **Get the units right first.** A model exported in millimetres but read as
  metres appears a thousand times too big. Fix the unit before converting.
- **Keep it light.** AR runs on a phone. Decimate dense scans to a few hundred
  thousand triangles; nobody can see more on a 6-inch screen.
- **Test in the place you'll use it.** AR needs a textured, well-lit surface to
  find the floor or table. A plain white desk under dim light is the hardest
  case.

For the other direction, opening a GLB on every device, see
[How to open a GLB file](/blog/how-to-open-glb-file).

## Frequently asked questions

### Can an iPhone open a GLB file?

Not in AR Quick Look, which needs USDZ. A web viewer can show a GLB in 3D in
Safari, but to place the model in your room on an iPhone you need a USDZ version
of it.

### Is USDZ the same as USD?

USDZ is a zipped, single-file package of a USD scene and its textures. Apple's AR
Quick Look opens USDZ, not loose USD files.

### Do I need an app to view USDZ on iPhone?

No. AR Quick Look is built into iOS and iPadOS. Tapping a properly linked USDZ in
Safari opens the model in AR directly.

*[Create a free account](/auth/register) and upload a model to get a GLB, a USDZ,
and one link that opens AR on any phone.*
""",
    },
    {
        "slug": "add-3d-model-to-powerpoint",
        "title": "How to Add a 3D Model to PowerPoint (and Let the Audience Explore It)",
        "description": "Four ways to put a 3D model in a PowerPoint presentation: native 3D models, rendered images, video, and a QR code that opens the model in 3D and AR on every phone.",
        "date": "2026-10-17",
        "author": "AcademicAR Team",
        "tags": ["PowerPoint", "teaching", "presentations", "how-to"],
        "persona": "Lecturers, researchers & conference speakers",
        "read_minutes": 7,
        "body": """\
![A slide with a 3D molecular model and a QR code, shown on a screen and a laptop](/static/images/blog/covers/add-3d-model-to-powerpoint.webp)

A 3D model makes a lecture or conference talk easier to follow: one rotating
object explains what three static views can't. PowerPoint gives you more than one
way to bring it in, and they suit different situations. Here are the four routes,
what each is good at, and when to combine them.

![Four ways to bring a 3D model into a slide deck and what each one keeps](/static/images/blog/powerpoint-3d-options.svg)
*Native 3D, images, video, or a live link: what each route keeps.*

## 1. Insert the model natively

PowerPoint for Microsoft 365 (Windows and Mac) can insert 3D files directly:
**Insert → 3D Models → This Device**. It accepts common formats including
**GLB, FBX, OBJ, STL, PLY, and 3MF**. You can rotate the model on the slide, use
the **Morph** transition to animate between two orientations, and add 3D
animations such as a turntable.

**Good for:** a single, light model you want to turn live on stage.
**Watch out for:** file size (dense scans make the deck slow and huge), limited
material quality, no layers or sections, and older PowerPoint versions or other
presentation tools that won't show it.

![A slide with a 3D model and rotation handles](/static/images/blog/inline/add-3d-model-to-powerpoint-a.webp)

## 2. Use rendered images

A still is the most robust option: it works in every version of PowerPoint,
Keynote, Google Slides, and the PDF handout. From the AcademicAR viewer you can
take **screenshots** of the current view, or, on paid plans, export a
**publication figure** with a scale bar, labels, and a QR card in one image.

**Good for:** handouts, posters made from slides, and anything printed.

## 3. Insert a short video

A turntable or a guided move through a model is often clearer than live
rotation, and it plays reliably. Record the view from the viewer's **video
recording** tool (it records MP4 in browsers that support it, which PowerPoint
and Keynote play), then **Insert → Video**. The owner's browser also generates
short showcase videos of a model automatically, saved with the project's media.

**Good for:** talks where you can't risk live interaction, and recorded lectures.

![Phones scanning a QR code on a projected slide](/static/images/blog/inline/add-3d-model-to-powerpoint-b.webp)

## 4. Put a QR code and link on the slide

This is the only route that lets the **audience** explore the model: they scan the
QR code, the model opens in their phone's browser, and one tap places it on their
desk in **AR**. Every AcademicAR model has a stable viewer link and QR code, so
the slide keeps working after you update the model, and you can see how many
people scanned it.

**Good for:** teaching, conference talks, and any slide where you want people to
look at the model themselves. Make the QR at least a fifth of the slide height
so it scans from the back of the room.

## Combine them

The strongest decks use two routes: a video or image on the slide for everyone,
and a QR code in the corner for anyone who wants to explore. For teaching, save
**scenes** (fixed views with labels and visible layers) and play them as a
[guided tour](/blog/saved-scenes-guided-tours-3d-teaching) from the viewer
instead of a slide sequence. You can also attach the deck itself to the
project, so students find the slides and the model on one page.

To put the same model on a course page or lab website, see
[how to embed a 3D model](/blog/embed-3d-model-website).

## Frequently asked questions

### Which 3D formats can PowerPoint insert?

PowerPoint for Microsoft 365 inserts GLB, FBX, OBJ, STL, PLY, and 3MF files via
Insert, 3D Models. Very large or highly detailed files are best simplified first.

### Can the audience interact with a 3D model in a PowerPoint slide?

Only the presenter can rotate a model inserted into the slide. To let the
audience explore it themselves, add a QR code that opens the model in their
phone's browser and in AR.

### Will a 3D model in PowerPoint work in Google Slides or Keynote?

Native PowerPoint 3D models don't carry over to other tools. Images, MP4 video,
and a QR code with a link work everywhere.

*[Create a free account](/auth/register), upload a model, and put its QR code on
your next slide.*
""",
    },
    {
        "slug": "dicom-to-stl-3d-printing",
        "title": "DICOM to STL: How to Turn a CT Scan into a 3D Printable Model",
        "description": "Convert DICOM CT or MRI to STL for 3D printing with free tools: anonymize, segment by Hounsfield threshold, clean the mesh, export, and share it interactively.",
        "date": "2026-10-18",
        "author": "AcademicAR Team",
        "tags": ["DICOM", "STL", "medical imaging", "3D printing", "how-to"],
        "persona": "Clinicians, medical researchers & students",
        "read_minutes": 8,
        "body": """\
![CT slices turned into a 3D printed vertebra](/static/images/blog/covers/dicom-to-stl-3d-printing.webp)

A CT scan is a stack of images; a 3D printer needs a closed surface. Getting from
one to the other, **DICOM to STL**, takes four steps whatever software you use:
prepare the data, segment the structure, turn it into a mesh, and clean that mesh
for printing. This guide walks through them with free tools, and shows how to
share the same model interactively with people who will never hold the print.

## Step 1: Prepare and anonymize the DICOM

Export the series from your PACS or scanner as DICOM files, one folder per
series. **Anonymize before the data leaves the clinical environment**: patient
name, ID, birth date, and institution live in the DICOM headers, and some scans
also have burned-in text. Use your institution's anonymization tool, and follow
its ethics and data protection rules for any research use.

Thin slices give better models. A 0.5–1 mm slice thickness is ideal for bone;
5 mm slices produce visible stair-steps.

## Step 2: Segment the structure

Segmentation decides which voxels belong to the model. For bone on CT, a
**threshold** on the Hounsfield scale does most of the work: bone sits above
roughly 250 HU, soft tissue well below. Our
[Hounsfield units guide](/blog/hounsfield-units-ct-thresholding-3d) explains how
to choose the value.

- **3D Slicer** (free, open source): load the series with the DICOM module, then
  in **Segment Editor** use *Threshold*, then *Islands* to keep the largest
  connected piece, and *Scissors* to cut away the table or neighbouring bones.
- **InVesalius** (free, open source): built for CT, with bone presets and a
  direct surface export.

MRI has no absolute intensity scale, so thresholds work poorly; expect to paint or
use region-growing tools instead.

![From DICOM slices to a printed model: segment, mesh, clean, export STL](/static/images/blog/dicom-to-stl-steps.svg)
*The four steps from a CT series to a printable STL, and the branch for sharing.*

![A segmented volume becoming a smooth surface mesh](/static/images/blog/inline/dicom-to-stl-3d-printing-a.webp)

## Step 3: Make the surface and export STL

In 3D Slicer, *Show 3D* turns the segment into a surface; apply light
**smoothing** to remove voxel steps without erasing real anatomy. Then use
**Segmentations → Export to files**, choose STL, and keep the coordinate system
and units (millimetres) consistent with your printer software. InVesalius exports
STL straight from the surface it builds.

Our [3D Slicer export guide](/blog/3d-slicer-export-share) covers the export
options in more detail.

## Step 4: Clean the mesh for printing

A model that looks fine on screen can still fail on a printer. Before slicing:

- **Make it watertight.** Holes and non-manifold edges confuse slicers. Blender's
  3D-Print Toolbox or Meshmixer can find and repair them.
- **Check wall thickness.** Thin cortical bone, orbital walls, and small vessels
  may be thinner than your printer can produce.
- **Reduce the triangle count.** Scans export millions of triangles; a few hundred
  thousand is plenty for printing at anatomical scale.
- **Orient and support** the model in your slicer, and print a small test piece
  first.

![A printed vertebra beside a laptop showing it](/static/images/blog/inline/dicom-to-stl-3d-printing-b.webp)

## Share the model before (or instead of) printing

A print reaches the people in the room. For colleagues, students, reviewers, and
patients' care teams elsewhere, an interactive model is faster and free to copy.
You can upload either file to AcademicAR:

- **The STL you just made**, with the unit set to millimetres.
- **The anonymized DICOM series as a ZIP.** AcademicAR builds the 3D model itself
  with **Bone, Skin, Contrast, and Auto** presets or a custom HU range, each as its
  own layer. Medical uploads need an extra confirmation that the data is
  anonymized, and the raw scan is deleted after conversion.

Either way you get a viewer link, AR on phones, and a QR code to put next to the
printed model or in the paper. AcademicAR is for viewing and sharing; it does not
produce printable STL files, so keep the STL from your segmentation software for
the printer.

For the full DICOM-to-viewer workflow, see
[From DICOM to interactive 3D](/blog/dicom-to-3d-model-guide).

## Frequently asked questions

### What is the best free software to convert DICOM to STL?

3D Slicer and InVesalius are both free and open source. 3D Slicer is the more
flexible of the two and handles CT and MRI; InVesalius is simpler and focused on
CT, with presets for bone and a direct STL export.

### What threshold should I use for bone?

On CT, around 250 HU and above separates bone from soft tissue in most scans.
Thin or osteoporotic bone may need a lower value; check the result against the
slices before exporting.

### Can I 3D print directly from DICOM?

No. A printer needs a closed surface mesh such as STL or 3MF. The DICOM series has
to be segmented and converted to a mesh first, then sliced for your printer.

*[Create a free account](/auth/register) and share your segmented model as an
interactive 3D link alongside the print.*
""",
    },
    {
        "slug": "sketchfab-alternative-researchers",
        "title": "A Sketchfab Alternative for Researchers: What Academic 3D Sharing Needs",
        "description": "Sketchfab is a great general 3D platform. Here's what researchers need on top of it, from stable QR codes and medical uploads to sections and measurements, and how the options compare.",
        "date": "2026-10-19",
        "author": "AcademicAR Team",
        "tags": ["3D viewer", "scientific publishing", "comparison", "sharing"],
        "persona": "Researchers, labs & institutions",
        "read_minutes": 7,
        "body": """\
![A journal page with a QR code and a fossil skull model cut by a section plane](/static/images/blog/covers/sketchfab-alternative-researchers.webp)

**Sketchfab** is the best-known place to publish 3D models online, and for good
reason: it has a polished viewer, embeds, annotations, AR, and a large community.
Plenty of museums and labs use it. But it was built as a general platform for
3D creators, and research has needs it wasn't designed around. If you're looking
for a Sketchfab alternative for academic work, here's what to check, and where
each kind of tool fits.

## What research sharing needs

**A link that outlives the paper.** A figure caption or poster QR code is printed
once and read for years. The link must keep pointing at the right model after you
replace the file, fix the colours, or move it to another project.

**Your input formats.** Research models start as CT and MRI scans, segmentations,
STEP assemblies from CAD, and photogrammetry. Converting each of these by hand
before upload is where most of the time goes.

**Compliance built in.** Medical and human-subject data needs anonymization, and
authors must confirm they have the right to publish. The platform should make
that explicit and handle raw scans carefully.

**Scientific tools, not only display.** Readers want to look *inside* (section
planes, cross-sections), measure, compare two states side by side, and export a
figure for the paper.

**Teaching and presenting.** Saved views, guided tours, and AR that works on the
students' own phones.

![Checklist of what research 3D sharing needs, grouped by link, inputs, compliance and tools](/static/images/blog/research-3d-sharing-checklist.svg)
*What to check before choosing where to host research models.*

![A paper, a dataset and a 3D model linked together](/static/images/blog/inline/sketchfab-alternative-researchers-a.webp)

## How the options fit

- **General 3D platforms (such as Sketchfab).** Excellent viewer and reach. A good
  fit for portfolios, cultural heritage showcases, and public outreach to a broad
  audience. You convert medical and CAD data yourself first.
- **Research repositories (such as MorphoSource or Zenodo).** Built for archiving
  files with persistent identifiers and metadata. The right place to deposit the
  raw data; less focused on an interactive viewing experience for readers.
- **AcademicAR.** Built for putting interactive 3D *into* publications, posters,
  and teaching. Use it alongside a repository: deposit the data there, and share
  the readable, interactive version here.

## What AcademicAR does differently

- **Stable QR codes and viewer links** that survive replacements, recolours, and
  plan upgrades.
- **Direct uploads** of GLB, STL, OBJ, FBX, **STEP/STP**, **DICOM series** (with
  bone, skin, contrast and custom HU thresholds), and **segmentations** (NIfTI,
  NRRD, DICOM-SEG), with every part or structure as its own layer.
- **Compliance confirmation** on every upload, an extra check for medical data,
  and raw scans deleted after conversion.
- **Research tools in the viewer:** a section plane and
  [2D slice view](/blog/section-plane-slice-view-cross-sections),
  [structure measurements](/blog/measuring-anatomy-3d-layer-metrics), side-by-side
  comparisons, and
  [publication figure export](/blog/publication-ready-3d-figures-comparisons).
- **Teaching:** saved scenes, guided tours, and AR on iPhone and Android with no
  app.
- **Licensing per model**, with institutional contracts for departments that
  publish many models.

![Views and QR scans for a shared model](/static/images/blog/inline/sketchfab-alternative-researchers-b.webp)

## Choosing

If your audience is the general public and the model is already a clean mesh, a
general platform may be all you need. If the model comes from a scanner or CAD,
goes into a paper or a poster, or carries patient data, choose a tool built for
that workflow, and keep depositing your raw data in a repository either way.

## Frequently asked questions

### Is there a free alternative to Sketchfab for academic use?

AcademicAR has a free plan to try a model in the viewer and in AR, and paid
plans are priced per model rather than as a monthly subscription. Research repositories
such as Zenodo are free for depositing files but are not interactive viewers.

### Can I upload DICOM or STEP files instead of a mesh?

Yes. AcademicAR converts DICOM series (as a ZIP), segmentations, and STEP/STP
assemblies on upload, so you don't have to make a mesh in other software first.

### Should I still deposit my 3D data in a repository?

Yes. A repository gives your data a persistent identifier and long-term
archiving. Link to it from the paper, and use an interactive viewer so readers can
actually explore the model.

*[Compare plans](/pricing) or [create a free account](/auth/register) to try your
own model.*
""",
    },
    {
        "slug": "view-3d-model-in-ar-without-app",
        "title": "How to View a 3D Model in AR Without an App (iPhone and Android)",
        "description": "Open a 3D model in augmented reality from a link or QR code with nothing to install: how AR Quick Look and Scene Viewer work, which phones support them, and how to share your own model.",
        "date": "2026-10-22",
        "author": "AcademicAR Team",
        "tags": ["augmented reality", "how-to", "QR codes", "3D viewer"],
        "persona": "Educators, researchers & presenters",
        "read_minutes": 6,
        "body": """\
![A phone placing a 3D heart model on a desk in AR after scanning a QR code](/static/images/blog/covers/view-3d-model-in-ar-without-app.webp)

Ask a room of people to install an app before they can see your model and most
of them won't. The good news is that they don't have to. Both iPhone and Android
ship with an augmented reality viewer built into the operating system, and a web
page can hand a 3D model straight to it. Your audience scans a QR code or taps a
link, then taps **View in AR**, and the model is standing on their desk.

## How web AR works without an app

There are two system viewers, and each wants its own file format:

- **iPhone and iPad: AR Quick Look.** Built into iOS. It opens a **USDZ** file
  from Safari (and Chrome on iOS) and places the model at real-world scale.
- **Android: Scene Viewer.** Part of Google Play Services for AR, which most
  recent Android phones install and update automatically. It opens a **GLB**
  file on any ARCore-supported phone.

A web viewer such as `<model-viewer>` shows the model in 3D in any browser and
picks the right AR route for the device. Nothing else is installed, and the
reader never leaves the page for an app store.

![From a QR code to AR without an app: scan, browser viewer, then AR Quick Look or Scene Viewer](/static/images/blog/ar-without-app-flow.svg)

The catch is the files. You need a GLB **and** a USDZ of the same model, both
light enough for a phone, at the right scale, behind one stable link. Making a
USDZ by hand is where most people stop; our
[GLB to USDZ guide](/blog/glb-to-usdz-iphone-ar) explains why.

## Step by step with AcademicAR

1. **Upload the model.** GLB, STL, OBJ (with its MTL and textures), FBX,
   STEP/STP, a DICOM series as a ZIP, or a segmentation. It's converted in the
   background to an optimized GLB, and a USDZ is generated for iPhone and iPad.
2. **Check the size.** On the model's page, set the real-world longest
   dimension if the file has no units, so a 4 cm vertebra doesn't arrive in AR
   the size of a car.
3. **Copy the link or download the QR code.** Every model gets a viewer link and
   a QR code that keep working if you later replace the file or recolour it.
4. **Scan and tap View in AR.** On a phone the model opens in the browser
   viewer; the AR button hands it to Quick Look or Scene Viewer. On a laptop the
   same button shows a QR code so the reader can continue on their phone.

![The same skull model placed in AR on an iPhone and an Android phone](/static/images/blog/inline/view-3d-model-in-ar-without-app-a.webp)

## Where no-app AR works best

- **Conference posters.** A QR code next to a figure turns a static panel into a
  model visitors can walk around. See our
  [poster QR code guide](/blog/qr-codes-on-academic-posters-guide) for size and
  placement.
- **Teaching.** Students place a skull, heart, or pelvis on their own desk and
  look at it from every side; our
  [anatomy AR guide](/blog/augmented-reality-anatomy-education) has lesson ideas.
- **Museums and collections.** A label with a QR code lets visitors take an
  artefact home on their phone, which is how
  [archaeology projects](/blog/ar-for-archaeology-3d-artifacts) use it.
- **Papers and theses.** A QR code beside a figure lets reviewers open the
  model on the spot. See
  [adding 3D models to research papers](/blog/how-to-add-3d-models-to-research-papers).

![Scanning a museum label QR code to place a fossil in AR](/static/images/blog/inline/view-3d-model-in-ar-without-app-b.webp)

## Tips for a good AR experience

- **Keep the model light.** A file of a few megabytes opens in seconds on a
  phone; a 200 MB scan does not. AcademicAR compresses uploads automatically,
  and our guide on [reducing GLB file size](/blog/reduce-glb-file-size) covers
  what to do before upload.
- **Use real size where it means something.** Anatomy and artefacts read best at
  life size. Tiny structures (a cochlea, a protein) work better enlarged, so set
  a longest dimension that fits on a desk.
- **Print the QR code large enough.** About 3 cm wide is a safe minimum for a
  poster at arm's length; larger if people scan from further away.
- **Give people a surface.** AR needs a textured, well-lit floor or table to
  find. A plain white tablecloth in dim light is the hardest case.

## Troubleshooting

**There's no View in AR button.** You're probably on a laptop, where AR isn't
available; scan the QR code that appears instead. On Android, the phone may not
support ARCore.

**The QR code opens a page but AR won't start.** In-app browsers (inside a
messaging or social app) often block the hand-off to the AR viewer. Open the link
in Safari or Chrome.

**The model is huge or tiny.** The source file had no units or the wrong ones.
Set the longest dimension on the model page; the link and QR code stay the same.

## Frequently asked questions

### Do I need an app to view a 3D model in AR?

No. iPhones and iPads use AR Quick Look and Android phones use Scene Viewer, both
built into the system. A web viewer passes the model to them from the browser,
so readers only scan a QR code or tap a link.

### Does AR work on Android phones?

Yes, on phones that support ARCore with Google Play Services for AR, which covers
most recent mid-range and flagship devices. The browser viewer still shows the
model in 3D on phones without AR support.

### What file formats does phone AR need?

iPhone and iPad need a USDZ file; Android uses a GLB. AcademicAR generates both
from a single upload, so you only manage one model and one link.

### Can I try it for free?

Yes. The free plan lets you upload a model and open it in the viewer and in AR
on your own phone before you decide on a plan.

*[Create a free account](/auth/register) and open your first model in AR on your
phone.*
""",
    },
    {
        "slug": "reduce-glb-file-size",
        "title": "How to Reduce GLB File Size Without Losing Detail",
        "description": "Why GLB files get heavy and how to shrink them: decimation, Draco compression, texture resizing and pruning with free tools, plus the iPhone AR catch, and what AcademicAR optimizes automatically.",
        "date": "2026-10-22",
        "author": "AcademicAR Team",
        "tags": ["3D formats", "how-to", "GLB", "optimization"],
        "persona": "Researchers, 3D artists & web developers",
        "read_minutes": 7,
        "body": """\
![A heavy wireframe bone and a light optimized bone on a balance scale](/static/images/blog/covers/reduce-glb-file-size.webp)

A GLB that takes a minute to load is a GLB nobody looks at. Scans, CT surfaces,
and CAD exports routinely come out at 50 to 500 MB, while a phone on a
conference Wi-Fi is happy with a few megabytes. The good news is that most of
that weight is invisible: triangles you can't see, raw numbers nobody
compressed, and textures far bigger than any screen. Here's how to find it and
remove it.

## First, find out what's heavy

A GLB holds three kinds of data: **geometry** (vertices and triangles),
**textures** (images), and a small amount of scene description. Before changing
anything, look at the split. Drop the file into [gltf.report](https://gltf.report)
in your browser, or run:

```
npx @gltf-transform/cli inspect model.glb
```

If geometry dominates, decimate and compress. If textures dominate, resize them.
Most scans are both.

![What makes a GLB heavy and which fix removes it: decimation, Draco, texture resizing, pruning](/static/images/blog/glb-size-fixes.svg)

## 1. Decimate the mesh

Decimation removes triangles while keeping the shape. It's the biggest win for
scans and medical surfaces, which often carry millions of triangles where a few
hundred thousand look identical on screen.

- **Blender:** add a *Decimate* modifier, set *Collapse* with a ratio such as
  0.2, watch the face count in the status bar, then apply and export glTF
  Binary (.glb).
- **MeshLab:** *Filters › Remeshing, Simplification and Reconstruction ›
  Simplification: Quadric Edge Collapse Decimation*, with *Preserve Boundary*
  and *Preserve Normal* ticked.

Aim for **100,000 to 500,000 triangles** for a single object. Check thin
features (vessels, teeth, fine edges) after each step; stop when they start to
break up.

![A vertebra model going from a dense mesh to a lighter mesh with the same shape](/static/images/blog/inline/reduce-glb-file-size-a.webp)

## 2. Compress the geometry with Draco

Draco stores vertex positions and normals as compact quantized numbers instead
of raw floats. Geometry often shrinks five to ten times with no visible change.

```
npx @gltf-transform/cli draco model.glb model-draco.glb
```

In Blender, tick *Compression* in the glTF exporter. Browsers and
`<model-viewer>` decode Draco without any setup, but some desktop importers
don't, so keep an uncompressed copy for editing.

## 3. Shrink the textures

Photogrammetry exports often carry 8K PNG textures: hundreds of megabytes on
their own. A phone screen never shows more than 2048 pixels of one texture.

```
npx @gltf-transform/cli resize in.glb out.glb --width 2048 --height 2048
```

Use **JPEG** for colour (base colour) maps and keep PNG only where you need
transparency. One catch if your audience uses iPhones: **AR Quick Look can't
read WebP textures**, so a GLB with WebP textures converted to USDZ shows up
magenta or untextured in iPhone AR. WebP is fine for browsers; for AR, stay with
JPEG or PNG.

![A phone loading a brain model quickly after its textures were resized](/static/images/blog/inline/reduce-glb-file-size-b.webp)

## 4. Clean up the rest

Exports also carry duplicate vertices, unused materials, and empty nodes.

```
npx @gltf-transform/cli optimize model.glb model-opt.glb --compress draco
```

`optimize` welds, deduplicates, prunes, lightly simplifies, and compresses in
one go. It also **joins meshes and merges identical materials**, which is what
you want for a single statue but not for a multi-part model: an assembly or a
segmentation would lose its separate, clickable parts. For those, run only
`draco` (step 2).

## What AcademicAR does for you

When you upload a model, the converter runs this pipeline automatically, so you
only need to decimate truly huge meshes first:

- **Draco geometry compression** on every model.
- For single-part models, **welding, pruning, a very light simplification, and
  textures capped at 2048 px**.
- For models with **layers** (assemblies, segmentations), Draco only, so every
  part stays separate in the Layers panel.
- Textures kept in formats that **iPhone AR** can read, and a USDZ generated
  from the same model.

The link and QR code stay the same if you later upload a lighter version with
*Replace model*. Starting from STL or OBJ? See
[STL to GLB](/blog/convert-stl-to-glb) and
[OBJ and FBX to GLB](/blog/convert-obj-fbx-to-glb).

## Frequently asked questions

### What is a good file size for a GLB on the web?

Under about 10 MB loads quickly on most phones and networks, and up to 20 to
30 MB is usually acceptable for a detailed model. Beyond that, decimate the mesh
or shrink the textures before sharing.

### Does Draco compression reduce quality?

Draco quantizes vertex positions, so in theory it is lossy, but at standard
settings the difference is far below what you can see on screen. Keep the
original file for editing, measurement, or 3D printing.

### Why is my model magenta in iPhone AR?

Most often the textures are WebP, which AR Quick Look can't read. Re-export with
JPEG or PNG textures. AcademicAR keeps AR-compatible textures for this reason.

### Can I reduce GLB size online for free?

Yes. gltf.report runs glTF-Transform in your browser, and the gltf-transform
command line tool is free and open source. Uploading to AcademicAR also
compresses the model automatically.

*Have a heavy model? [Upload it to AcademicAR](/auth/register) and let the
converter make it phone-ready.*
""",
    },
    {
        "slug": "nifti-segmentation-to-3d-model",
        "title": "NIfTI to 3D Model: Turn .nii.gz Segmentations into an Interactive Viewer",
        "description": "How to turn a NIfTI (.nii or .nii.gz) segmentation into a 3D model: label maps versus intensity images, TotalSegmentator and 3D Slicer outputs, label names, and a shareable 3D and AR link.",
        "date": "2026-10-22",
        "author": "AcademicAR Team",
        "tags": ["medical imaging", "segmentation", "how-to", "NIfTI"],
        "persona": "Medical imaging researchers",
        "read_minutes": 7,
        "body": """\
![CT slices with coloured segmentations rising into a 3D model of the liver, kidneys and spine](/static/images/blog/covers/nifti-segmentation-to-3d-model.webp)

NIfTI (`.nii` or `.nii.gz`) is the everyday format of neuroimaging and of most
segmentation tools, from TotalSegmentator to nnU-Net. It's also a volume: a 3D
grid of voxels, not a surface. To show it as a 3D model that a colleague can
rotate, or a student can place on their desk in AR, you need to turn the right
kind of NIfTI into meshes. Here's how to tell which kind you have and how to get
from either one to a shareable model.

## Two kinds of NIfTI

Every NIfTI stores numbers on a grid, but what the numbers mean differs:

- **Intensity images.** A T1-weighted MRI, a CT in Hounsfield units, a PET
  volume. Hundreds or thousands of grey values, often negative or fractional.
- **Label maps and masks.** The output of segmentation. Each voxel holds a small
  integer: 0 for background, 1 for liver, 2 for spleen, and so on. A binary
  mask holds only 0 and 1.

Only label maps describe surfaces you can mesh directly. An intensity image has
to be segmented first.

![Which NIfTI do you have: intensity image, label map, or a folder of masks](/static/images/blog/nifti-which-file.svg)

## If you have an intensity image: segment it first

**TotalSegmentator** segments more than 100 structures in CT, and has an MR
task, from the command line:

```
TotalSegmentator -i ct.nii.gz -o segmentations
```

Add `--roi_subset liver spleen kidney_left kidney_right` to keep only what you
need; it's faster and gives a cleaner model. Two other common tools:

- **3D Slicer** (Segment Editor) for manual or semi-automatic work, with
  thresholding, painting, and AI extensions. Our
  [3D Slicer guide](/blog/3d-slicer-export-share) covers exporting from it.
- **ITK-SNAP** for active-contour segmentation and fast manual correction.

Working from a CT in DICOM rather than NIfTI? You can skip this step:
AcademicAR thresholds a DICOM series directly with bone, skin, and vessel
presets. See [DICOM to 3D model](/blog/dicom-to-3d-model-guide) and the
[Hounsfield unit guide](/blog/hounsfield-units-ct-thresholding-3d).

## If you have a label map or a mask: upload it as is

Upload the `.nii` or `.nii.gz` file. Each label value becomes one **layer** with
its own colour, so readers can hide the ribs to see the kidneys, or isolate a
tumour. To give layers real names instead of *Label 1*, *Label 2*, put the
NIfTI in a ZIP together with a label table: an ITK-SNAP label description file,
a 3D Slicer colour table (`.ctbl`), or a JSON file such as
`{"1": "liver", "2": "spleen"}`. Names stored as JSON in the NIfTI header are
read too.

## If you have a folder of masks: zip it

TotalSegmentator's default output is one mask per structure: `liver.nii.gz`,
`spleen.nii.gz`, and so on. Zip the folder and upload the ZIP. Each file name
becomes a layer name, empty masks are skipped, and a CT that happens to sit in
the same folder is ignored rather than meshed. The masks must share the same
grid, which they do when they come from one segmentation run. (The single
multilabel file from `--ml` works as well, but its layers are numbered rather
than named.)

![Segmentation label maps in three slice views, ready to zip and upload](/static/images/blog/inline/nifti-segmentation-to-3d-model-a.webp)

## What you get

The converter turns every structure into a smoothed surface in the scan's real
coordinates and millimetres, and builds one GLB with a layer per structure, plus
a USDZ for iPhone AR. Up to 64 structures are kept; with more, the largest ones
are shown and the conversion notes say so. Then you can:

- **Hide, isolate, and recolour** structures in the Layers panel, and save views
  as [scenes for teaching](/blog/saved-scenes-guided-tours-3d-teaching).
- **Cut through the model** with a section plane or read a
  [2D slice view](/blog/section-plane-slice-view-cross-sections).
- **Measure** each structure's dimensions and the distance between two of them
  with [layer measurements](/blog/measuring-anatomy-3d-layer-metrics).
- **Share** a stable link and QR code that opens in the browser and in AR,
  with no app.

![A 3D model of the spine, ribs and kidneys with a layer list beside it](/static/images/blog/inline/nifti-segmentation-to-3d-model-b.webp)

Need a printable file too? The [DICOM to STL guide](/blog/dicom-to-stl-3d-printing)
covers exporting meshes for 3D printing, and the
[segmentation viewer guide](/blog/medical-image-segmentation-to-3d-viewer) goes
deeper into NRRD and DICOM-SEG.

## Privacy

NIfTI headers carry far fewer identifiers than DICOM, but file names and the
header's description field can still hold a name or an ID, so check them before
sharing. Medical uploads on AcademicAR need an extra confirmation that the data
is de-identified, the uploaded scan is never archived, and it is deleted after
conversion. Only the surface model is kept.

## Frequently asked questions

### Can I upload a raw MRI or CT NIfTI and get a 3D model?

Not directly. A raw intensity volume has no surfaces until it is segmented.
Segment it with TotalSegmentator, 3D Slicer, or ITK-SNAP first, then upload the
label map or masks. For CT in DICOM format, AcademicAR can threshold the series
itself.

### How do I get structure names instead of Label 1, Label 2?

Upload one mask file per structure in a ZIP (file names become layer names), or
put a label table next to a multi-label NIfTI in the ZIP: an ITK-SNAP label file,
a 3D Slicer .ctbl colour table, or a JSON map from label value to name.

### Does TotalSegmentator output work?

Yes. Zip the output folder with one mask per structure and upload it; layers are
named after the files. Use --roi_subset to limit the output to the structures you
want to show.

### Is my scan stored after conversion?

No. The uploaded NIfTI is deleted after conversion, whether it succeeds or fails,
and is never archived. Only the resulting 3D surface model is kept.

*[Create a free account](/auth/register) and turn your first segmentation into an
interactive 3D model.*
""",
    },
    {
        "slug": "protein-structure-3d-ar-chimerax-pymol",
        "title": "Share a Protein Structure in 3D and AR: Export from ChimeraX, Mol* or PyMOL",
        "description": "Turn a PDB, mmCIF or AlphaFold structure into an interactive 3D model readers can rotate in the browser and place in AR: export GLB from ChimeraX or Mol*, OBJ from PyMOL, and share it with a link and QR code.",
        "date": "2026-10-22",
        "author": "AcademicAR Team",
        "tags": ["structural biology", "how-to", "augmented reality", "3D publishing"],
        "persona": "Structural biologists & biochemistry educators",
        "read_minutes": 7,
        "body": """\
![A protein ribbon model floating above a journal page with a QR code and a phone](/static/images/blog/covers/protein-structure-3d-ar-chimerax-pymol.webp)

A protein figure is a carefully chosen view of something that only makes sense in
three dimensions. You picked the representation, the colours, and the angle; the
reader gets one projection of it. Exporting that exact scene as a 3D model lets
them rotate the binding pocket themselves, and on a phone, place the complex on
the table in augmented reality. No molecular viewer to install, no PDB ID to look
up.

## Why export a model instead of linking a PDB ID?

Online structure viewers are excellent, but a PDB ID link shows the default
representation, not yours. An exported model keeps **your** cartoon, surfaces,
colours by chain or by domain, highlighted ligand, and hidden solvent. It also
opens on any phone, works in AR, and stays exactly as you published it.

![Three routes from a structure to a shareable 3D model: ChimeraX, Mol* and PyMOL](/static/images/blog/molecule-export-routes.svg)

## Route 1: ChimeraX (recommended)

ChimeraX saves the scene as a **GLB** with its colours:

```
open 6vxx
delete solvent
cartoon
color bychain
surface #1/A
transparency #1/A 50 target s
save spike.glb
```

For an AlphaFold prediction, `alphafold fetch P69905` and
`color bfactor palette alphafold` colour the model by confidence. Large
assemblies with full surfaces can reach millions of triangles; a lower-detail
surface such as `surface #1 resolution 5` or a simpler representation keeps the
file light.

## Route 2: Mol\\* Viewer

The Mol\\* Viewer (the viewer behind RCSB PDB and PDBe) runs in the browser. Load a
structure, set up the representation and colours, then use **Export Geometry**
and choose glTF. Mol\\* can export the scene as STL or OBJ too, but GLB keeps the
colours.

## Route 3: PyMOL

PyMOL saves geometry with:

```
save model.obj
```

which writes an OBJ and an MTL material file next to it. Upload both together.
PyMOL's OBJ export is good for shape, but colours don't always survive; if your
model comes through grey, rebuild the scene in ChimeraX or Mol\\* and export GLB.

![A coloured protein surface exported from a laptop to a file and opened on a phone](/static/images/blog/inline/protein-structure-3d-ar-chimerax-pymol-a.webp)

## Upload, size, and share

1. **Upload the GLB** (or OBJ + MTL) to a project on AcademicAR. It's compressed
   for the web and a USDZ is generated for iPhone AR.
2. **Set a sensible size.** Molecular programs write ångströms as plain units,
   so a protein arrives at an arbitrary scale. Use *Resize model* and set the
   longest dimension, say 30 cm, so it sits nicely on a desk in AR.
3. **Use the layers.** If the file keeps chains, ligands, or surfaces as separate
   parts, each becomes a layer that readers can hide, isolate, or recolour.
4. **Share the link and QR code.** Put the QR code next to the figure in the
   paper, on the poster, or on a lecture slide. Readers scan it and tap *View in
   AR*; see [how AR works without an app](/blog/view-3d-model-in-ar-without-app).

## Ideas for teaching and papers

- **Lectures:** students hold a ribosome, an antibody, or a membrane channel on
  their own desk and walk around it.
- **Papers and preprints:** a QR code beside a structure figure, as described in
  [adding 3D models to research papers](/blog/how-to-add-3d-models-to-research-papers).
- **Posters:** the binding site, in 3D, next to the panel that discusses it; see
  the [poster QR code guide](/blog/qr-codes-on-academic-posters-guide).
- **Guided views:** save scenes such as *overview*, *active site*, and *ligand
  close-up* and play them as a [tour](/blog/saved-scenes-guided-tours-3d-teaching).

![Two students discussing a protein model shown in AR above a table](/static/images/blog/inline/protein-structure-3d-ar-chimerax-pymol-b.webp)

## Frequently asked questions

### Can I share an AlphaFold model in AR?

Yes. Fetch the prediction in ChimeraX, colour it by confidence with the AlphaFold
palette if you like, save it as GLB, and upload it. Readers see it in 3D in the
browser and in AR on their phone.

### Do readers need ChimeraX or PyMOL to view the model?

No. The model opens in any modern browser on a phone, tablet, or computer, and in
AR on iPhone and Android without an app.

### Is the atomic structure uploaded?

No. Only the exported surface geometry is uploaded: the shapes and colours you
see on screen, not the coordinates file. Deposit the structure itself in the PDB
or a data repository as usual.

### Can I export a cryo-EM map surface?

Yes. Show the map as a surface at the threshold you want in ChimeraX, colour it,
and save it as GLB like any other scene. Keep the surface step size reasonable so
the file stays light.

*[Create a free account](/auth/register) and put your next structure figure into
your readers' hands.*
""",
    },
    {
        "slug": "share-photogrammetry-3d-scan",
        "title": "How to Share a Photogrammetry or LiDAR Scan as a 3D Link and AR Model",
        "description": "Export your Polycam, RealityScan, Metashape or Meshroom scan the right way (GLB, or OBJ with its textures), keep it light, set the real size, and share it with a viewer link and QR code that opens in AR.",
        "date": "2026-10-22",
        "author": "AcademicAR Team",
        "tags": ["photogrammetry", "how-to", "sharing", "augmented reality"],
        "persona": "Archaeologists, museums & field researchers",
        "read_minutes": 7,
        "body": """\
![A phone capturing a clay pot on a turntable and the finished textured 3D model](/static/images/blog/covers/share-photogrammetry-3d-scan.webp)

Photogrammetry and phone LiDAR have made 3D capture almost free. A few hundred
photos of a pot, a fossil, or a sculpture become a textured model in an
afternoon. Sharing it is where things get stuck: the scanning app wants your
viewers to use its own platform, the export comes as a folder of files, and the
model is too heavy for a phone. Here's a clean path from scan to a link and QR
code that anyone can open, in 3D and in AR.

![From photos to a shared scan: capture, reconstruct, export, upload, share](/static/images/blog/photogrammetry-share-steps.svg)

## 1. Capture and reconstruct

Any reconstruction tool works: **Polycam** or **Scaniverse** on a phone,
**RealityScan**, **Agisoft Metashape**, **RealityCapture**, or the open-source
**Meshroom** on a desktop. Before exporting:

- **Crop** the turntable, the floor, and floating fragments.
- **Fill holes** if the tool offers it, especially under the object.
- **Include a scale bar** in the photos when you need true measurements;
  photogrammetry has no inherent scale.

## 2. Export the right format

Choose **GLB** when your tool offers it: one file with geometry and textures
together. Otherwise choose **OBJ**, which comes as three parts:

- `model.obj`, the geometry;
- `model.mtl`, the material file that names the textures;
- one or more texture images (`.jpg` or `.png`).

Upload all of them together. When an OBJ scan shows up grey, the cause is almost
always a missing texture or an MTL that points to a folder on the computer that
made it. Keep the three parts in one folder with their original names before
uploading. More detail in [OBJ and FBX to GLB](/blog/convert-obj-fbx-to-glb).

![Mesh, material and texture files combined into one model shown in a web viewer](/static/images/blog/inline/share-photogrammetry-3d-scan-a.webp)

## 3. Keep it light

Desktop reconstructions often produce several million triangles and 8K textures:
fine for archiving, too heavy for a phone. Export a **web version** next to your
master:

- decimate to roughly **200,000 to 500,000 triangles**;
- bake or keep textures at **2048 or 4096 pixels**, as JPEG.

AcademicAR compresses geometry with Draco and caps texture size on upload, so a
moderately heavy scan still becomes phone-ready. For the details, see
[how to reduce GLB file size](/blog/reduce-glb-file-size). Keep the full-resolution
master in your archive or repository.

## 4. Upload and set the real size

Upload the GLB, or the OBJ with its MTL and textures, to a project. For OBJ you
choose the unit it was made in; for any model you can later set the **real
longest dimension** in centimetres under *Resize model*. Do that whenever the
scan has no scale, so the pot in AR is the size of the pot on the shelf.

## 5. Share it

Every model gets a **viewer link** and a **QR code**:

- Put the QR code on a museum label, an excavation report, or a poster.
- [Embed the viewer](/blog/embed-3d-model-website) on a project website.
- Send the link to colleagues; it opens in the browser, with no account needed
  to view.
- On a phone, *View in AR* places the object at real size; see
  [AR without an app](/blog/view-3d-model-in-ar-without-app).

If you re-scan or clean the model later, *Replace model* keeps the same link and
QR code, so printed labels never go stale.

![A field researcher scanning a fossil and a colleague viewing it remotely through a shared link](/static/images/blog/inline/share-photogrammetry-3d-scan-b.webp)

## Rights and ethics

Check that you may publish a 3D copy. Museum objects, private collections, and
cultural heritage can carry reproduction rights or community restrictions, and
human remains call for particular care and consent. AcademicAR asks you to
confirm rights and ethics responsibility on every upload. For field and
collection workflows, see our
[archaeology AR guide](/blog/ar-for-archaeology-3d-artifacts).

## Frequently asked questions

### Can I share a Polycam scan without a Polycam account?

Yes. Export the scan as GLB or OBJ from Polycam, upload it to AcademicAR, and
share the viewer link or QR code. People who open it don't need an account or
an app.

### Why does my OBJ scan look grey after upload?

The textures didn't come along. Upload the .obj together with its .mtl file and
every texture image it names, keeping the original file names.

### How big should a scan be for the web?

As a rule of thumb, a few hundred thousand triangles and textures of 2048 to
4096 pixels. That loads quickly on phones and still looks sharp; keep the
full-resolution version for archiving.

### Will the scan show at real size in AR?

Yes, once its size is right. Photogrammetry has no built-in scale, so set the
longest dimension in centimetres on the model page; AR then places the object at
that size.

*[Create a free account](/auth/register) and share your first scan as a link and
QR code.*
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


_COVER_RE = re.compile(
    r"!\[[^\]]*\]\((/(?:static|blog-images)/[^)\s]+\.(?:webp|png|jpe?g))\)", re.IGNORECASE
)


def cover_image_path(body: str) -> str | None:
    """Site path of the post's first raster image (its cover), for og:image and
    the BlogPosting schema: a repo image under /static/ or an admin upload under
    /blog-images/. SVG diagrams are skipped: link previews don't render them."""
    match = _COVER_RE.search(body or "")
    return match.group(1) if match else None


def related_posts(post: dict, posts: list[dict], limit: int = 3) -> list[dict]:
    """Up to ``limit`` other posts sharing the most tags with ``post`` (newest
    first on ties), for internal links under an article."""
    tags = {t.casefold() for t in post.get("tags") or []}
    scored = [
        (len(tags & {t.casefold() for t in p.get("tags") or []}), p["date"], p)
        for p in posts
        if p["slug"] != post["slug"]
    ]
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [p for _score, _date, p in scored[:limit]]


_FAQ_HEADING_RE = re.compile(
    r"^##[ \t]+(?:frequently asked questions|faqs?)[ \t]*#*[ \t]*$", re.IGNORECASE | re.MULTILINE
)


def _markdown_to_text(markdown_text: str) -> str:
    """Plain text for structured data: drop images, keep link text, strip
    emphasis/code markers and join lines."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", markdown_text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(\*\*|\*|`)", "", text)
    text = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", text, flags=re.MULTILINE)
    return " ".join(text.split())


def faq_items(body: str) -> list[dict]:
    """Question/answer pairs from the post's "## Frequently asked questions"
    section (each question a ``###`` heading, its answer the text up to the
    next heading), for the FAQPage schema. Empty when the post has none."""
    match = _FAQ_HEADING_RE.search(body or "")
    if not match:
        return []
    section = body[match.end():]
    next_h2 = re.search(r"^##[ \t]", section, re.MULTILINE)
    if next_h2:
        section = section[: next_h2.start()]
    items = []
    for block in re.split(r"^###[ \t]+", section, flags=re.MULTILINE)[1:]:
        question, _, answer = block.partition("\n")
        # A closing call-to-action (an all-italic paragraph) after the last
        # answer is not part of it.
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", answer) if p.strip()]
        while paragraphs and re.fullmatch(r"\*[^*].*[^*]\*", paragraphs[-1], re.DOTALL):
            paragraphs.pop()
        answer = "\n\n".join(paragraphs)
        question, answer = _markdown_to_text(question), _markdown_to_text(answer)
        if question and answer:
            items.append({"question": question, "answer": answer})
    return items
