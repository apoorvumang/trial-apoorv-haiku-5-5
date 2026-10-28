"""Prompts for site generation: brief -> design system -> pages, plus QC-driven repair.

Deliberately minimal: the site type, the style direction and the technical constraints of the renderer and grader.
Design decisions (layout, chrome, density, copy) are left to the generator; rendering defects are caught by QC and
fixed by the repair prompts.
"""
import json

SYSTEM = ("You are a senior web designer and front-end developer. You build complete, realistic websites in "
          "hand-written HTML and CSS.")

RULES = """Technical constraints (the site is rendered offline in Chromium at 1440px wide and judged from static
full-page screenshots):
- Static HTML and CSS only: no JavaScript, no external resources (CDNs, web fonts, remote images).
- Fonts: only these families from fonts/fonts.css, which is already linked: {fonts}.
- Raster images: only the provided files in images/. Icons and decorations: inline SVG or CSS.
- Everything must be visible on load: no animations, no position: sticky or fixed, no collapsed or hidden content.
- Write it as the real site would. Never mention that it is fictional, generated or a demo."""


def _rules(fonts: list[str]) -> str:
    return RULES.format(fonts=", ".join(fonts))


def _type_line(spec: dict) -> str:
    return f'{spec["subclass"]} (class: {spec["class"]})'


def _style_block(spec: dict) -> str:
    s = spec["style"]
    return (f'Style direction "{s["name"]}" ({s["positioning"]}):\n{s["description"]}\n'
            f'Palette: {", ".join(s["palette"])}. Fonts: headings {s["fonts"]["headings"]}, body {s["fonts"]["body"]}.')


def brief_prompt(spec: dict) -> str:
    return f"""Plan the website of a believable, fictional {_type_line(spec)}.

{_style_block(spec)}

Choose 5 to 8 pages that a real site of this type has; the first is "index.html", the others short kebab-case .html
names. Give each page its concrete content: real-looking names, numbers, prices, dates, text. Put facts that several
pages repeat in "facts" so the pages agree. Each page should be about 3 to 6 screens tall at 1440x900.

Plan the raster images the site uses (at most 30): for each a filename, its display size in px (at most 1600x1200),
the page it is on, and a prompt for an image generator. Images contain no text or logos. Also give one "image_style"
line so they look like one set.

Return ONLY a JSON object (no prose, no code fence) with this shape:
{{
  "org": {{"name": "...", "tagline": "...", "summary": "..."}},
  "facts": {{}},
  "image_style": "...",
  "nav": [{{"label": "...", "file": "index.html"}}],
  "pages": [{{"file": "index.html", "title": "...", "purpose": "...", "sections": [{{"name": "...", "content": "..."}}]}}],
  "images": [{{"file": "images/example.jpg", "width": 1200, "height": 800, "page": "index.html", "prompt": "..."}}]
}}"""


def design_system_prompt(spec: dict, brief: dict, fonts: list[str]) -> str:
    return f"""Create the shared design system of this {_type_line(spec)} website.

{_style_block(spec)}

Site brief:
{json.dumps(brief, indent=2)}

{_rules(fonts)}

Return three files: styles.css (shared by every page), header.html (markup that goes before each page's <main>) and
footer.html (markup that goes after it; may be empty). Use the nav's hrefs exactly.

<file name="styles.css">
...css...
</file>
<file name="header.html">
...markup...
</file>
<file name="footer.html">
...markup...
</file>"""


def page_prompt(spec: dict, brief: dict, page: dict, styles_css: str, header: str, footer: str,
                fonts: list[str]) -> str:
    images = [im for im in brief.get("images", []) if im.get("page") == page["file"]]
    return f"""Build the page "{page["title"]}" ({page["file"]}) of this {_type_line(spec)} website.

{_style_block(spec)}

Site brief:
{json.dumps(brief, indent=2)}

styles.css (already linked):
<styles.css>
{styles_css}
</styles.css>

Inserted automatically before and after your <main>:
{header}
{footer}

{_rules(fonts)}

Planned content:
{json.dumps(page["sections"], indent=2)}

Images for this page (use each once):
{json.dumps(images, indent=2) if images else "none"}

Return two files: page.css (styles for this page only; may be empty) and main.html.

<file name="page.css">
...css...
</file>
<file name="main.html">
<main>...</main>
</file>"""


CHECKS = """What each check means:
- low_contrast: the text colour is too close to the colour actually behind it in the render ("ratio"; aim for at least
  4.5:1 for body text and 3:1 for large text). Over a photo, add a solid or scrim backing or move the text.
- occluded_text: another element ("by") is painted on top of this text and hides part of it.
- text_collision: this text overlaps another piece of text or a badge-like box ("other_el").
- clipped_text: the text is cut off by a clipping ancestor ("by": overflow hidden/auto or a fixed size) or by the edge
  of the viewport.
- broken_token: a word, number, email, URL or code token is split across two lines. Keep it whole by changing that
  token only (white-space: nowrap with a slightly smaller size, or shorter wording); never rely on overflow-wrap:
  anywhere, and never widen columns or change the grid to fit it.
- orphan_fragment: a lone character or punctuation mark dropped onto its own line, away from the text before it
  ("other_text"), usually because a broad selector made an inline element display: block. Keep them on one line.
- wrapped_code: a code block soft-wraps its lines. Use white-space: pre, overflow-x: auto and a size that fits.
- ua_default: a browser default leaks through (for example dd's 40px indent, or a visible skip link). Reset or remove it.
- misaligned_row: a row of buttons or items is left-aligned inside a centred block. Centre it (justify-content: center).
- uneven_row: cards in one row are vertically centred against each other, so their tops do not line up. Align them to
  the top or stretch them to equal height.
- placeholder_image: this image could not be generated and shows an abstract placeholder. Remove the image slot (and
  any caption describing it) and rebalance the layout without it.
- meta_copy: this text talks about the website itself (fictional, demo, preview, static, build rules). Rewrite or remove
  it so every word reads as the organisation speaking to its visitors.
- type_drift: this page's body text or code is smaller or tighter than on the rest of the site ("detail" gives both).
  Restore the site's sizes and line-heights.
- horizontal_overflow: the page is wider than 1440px.
- over_height_budget: the page is taller than the budget. Trim or condense content (fewer items, shorter copy) or use a
  denser layout (more columns). Never shrink font sizes, line-heights, paddings or gaps below what the rest of the
  site uses, and do not drop planned sections.
- sticky_or_fixed: position: sticky/fixed is not allowed; make the element static in place.
- interactive_control: range sliders and collapsed <details> are not allowed; show their content statically.
- broken_images, external_requests, fonts, has_script, dead_links, near_blank: the page breaks the technical constraints."""


def _defects(findings: list[dict]) -> str:
    return ("Defects (el = the element as tag#id.class, y = px from the top of the page):\n"
            + json.dumps(findings, indent=1) + "\n\n" + CHECKS)


def repair_page_prompt(spec: dict, page: dict, styles_css: str, header: str, footer: str, page_css: str,
                       main_html: str, findings: list[dict], fonts: list[str]) -> str:
    return f"""You built the main content of the page "{page["title"]}" ({page["file"]}) of this {_type_line(spec)}
website. An automatic QA pass rendered it in Chromium at 1440px wide and found the defects listed below. Fix every one
of them with the smallest change that fully resolves it. Fix each defect on the element concerned: do not change grid
templates, column widths, image sizes or section order to fix one element. Keep the design, layout, content and
everything else as it is.

{_style_block(spec)}

Shared stylesheet styles.css (already linked; you cannot change it, but page.css may override it for this page):
<styles.css>
{styles_css}
</styles.css>

Shared chrome around your <main> (you cannot change it):
{header}
{footer}

{_rules(fonts)}

{_defects(findings)}

Current page.css:
<page.css>
{page_css}
</page.css>

Current main.html:
<main.html>
{main_html}
</main.html>

Return both complete, corrected files in this format and nothing else:
<file name="page.css">
...page-specific css (may be empty)...
</file>
<file name="main.html">
<main>...</main>
</file>"""


def repair_design_prompt(spec: dict, styles_css: str, header: str, footer: str, findings: list[dict],
                         fonts: list[str]) -> str:
    return f"""You built the shared design system (styles.css plus the header and footer chrome on every page) of this
{_type_line(spec)} website. An automatic QA pass rendered every page in Chromium at 1440px wide and found the defects
listed below in the shared chrome (outside <main>; "pages" says where each one appeared). Fix every one of them with the
smallest change that fully resolves it, on the element concerned (do not change grid templates or column widths to
fix one element). Keep every class name, token and selector the pages rely on, and keep the design as it is.

{_style_block(spec)}

{_rules(fonts)}

{_defects(findings)}

Current styles.css:
<styles.css>
{styles_css}
</styles.css>

Current header.html:
<header.html>
{header}
</header.html>

Current footer.html:
<footer.html>
{footer}
</footer.html>

Return all three complete, corrected files in this format and nothing else:
<file name="styles.css">
...css...
</file>
<file name="header.html">
...markup...
</file>
<file name="footer.html">
...markup (may be empty)...
</file>"""
