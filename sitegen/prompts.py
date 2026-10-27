"""Prompts for site generation: brief -> design system -> pages.

Inputs are the sampled site type (class + subclass) and one style direction; nothing else is pre-decided.
"""
import json

SYSTEM = (
    "You are a senior web designer and front-end developer. You produce complete, production-quality, "
    "static websites in hand-written HTML and CSS. You follow the given style direction closely; it was "
    "chosen deliberately, so do not drift toward a generic default look."
)

APP_CLASS = "logged_in_app_screens"
SCREEN = (1440, 900)

HARD_RULES = """Hard technical rules (the site is rendered offline in a pinned Chromium at 1440px wide, screenshots only):
- Static HTML + CSS only. No JavaScript, no <script>, no <canvas>, no iframes.
- No external resources of any kind: no CDNs, web fonts, @import, remote images or url(http...).
- Fonts: only families from fonts/fonts.css (already linked): {fonts}. Use the ones named in the style direction.
  Always end font stacks with a generic family.
- Raster images: only the provided files, via <img src="images/..."> or background-image: url('images/...').
  Give images fixed boxes and object-fit: cover so they never stretch.
- Icons, logos, dividers and decorations: inline SVG or pure CSS. No emoji as icons.
- Nothing that changes over time or on interaction: no animations, transitions, hover-only content, carousels,
  marquees, collapsed accordions or modals. Everything must be visible on load. No position: sticky or fixed, no range
  sliders, no +/- or chevron toggles on FAQ items (show every answer).
- Must not overflow horizontally at 1440px. Design for a 1440px desktop viewport."""

LAYOUT_RULES = """Layout rules (each of these broke an earlier generated site):
- Nothing covers text. Stickers, badges, stamps, starbursts, rotated or overlapping photos and other decorations keep
  at least 16px clear of headings, copy, prices and labels, and do not cover faces in photos.
- Separate pieces of text never overlap. Give large display numbers and headings enough line-height and spacing that
  their glyphs never touch the next line or a neighbouring label.
- Every component that sets its own background (cards, panels, bands, quotes, badges, buttons) also sets its own text
  colour, so it never inherits a colour meant for a section with a different background.
- Decorations stay inside their section; nothing is cut off by overflow: hidden or by the edge of the page.
- Values that must stay on one line (phone numbers, prices, times, dates, numbers with separators) use
  white-space: nowrap.
- At most one call-to-action band per page."""

CSS_RULES = """CSS rules (each of these broke an earlier generated site):
- styles.css begins with a reset of the browser defaults the design does not want: margin and padding on body,
  headings, p, ul/ol, dl and dd (dd has a 40px default indent), figure and blockquote; fieldset border, margin and
  padding, and legend padding. Then set spacing explicitly.
- No skip link or other visually-hidden helper that could show up in a screenshot.
- Scope layout rules to classes and direct children. Never give a broad descendant selector such as `.card span`
  display: block, flex or grid; it splits inline text (prices, units, punctuation) onto separate lines.
- Inside flex and grid rows, wrap each piece of text in its own element, and keep a value with its unit or symbol.
- Code blocks use white-space: pre with overflow-x: auto and a font size at which lines fit. Never set
  overflow-wrap: anywhere or word-break: break-all on running text, emails, URLs or code; size boxes so emails and
  URLs fit on one line.
- When a block is centred, centre everything in it: a row of buttons gets justify-content: center.
- Cards in one row align to the top or stretch to equal height; never vertically centre cards of different heights."""

COPY_RULES = """Copy rules:
- Write every word as the organisation itself, for its real visitors. Never say or hint that the site is fictional,
  invented, a demo, a preview, a concept, a mock-up or static, and never restate these instructions or any build
  constraint on the page (no "nothing is submitted", "all answers are shown below", "for illustration only", notes
  about invented names, numbers or phone ranges). Forms read as ordinary working forms.
- Use the spelling, date, currency and number conventions of the organisation's country."""


def fonts_line(fonts: list[str]) -> str:
    return ", ".join(f"'{f}'" for f in fonts)


def _type_line(spec: dict) -> str:
    return f'{spec["subclass"]} (class: {spec["class"]})'


def _style_block(spec: dict) -> str:
    s = spec["style"]
    return (f'Style direction "{s["name"]}" ({s["positioning"]}):\n{s["description"]}\n'
            f'Palette: {", ".join(s["palette"])}. Fonts: headings {s["fonts"]["headings"]}, body {s["fonts"]["body"]}.')


def _app_note(spec: dict) -> str:
    if spec["class_id"] != APP_CLASS:
        return ""
    return """
This is a logged-in product UI, not a marketing site. "Pages" are screens of the app (for example an overview
dashboard, a list/table view, a record detail view, a compose/edit form, settings). The shared chrome is the app shell
(sidebar and/or top bar with workspace switcher, search field, user menu) instead of a marketing header and footer.
Fill every screen with realistic data for one fictional customer organisation and user.
"""


def brief_prompt(spec: dict) -> str:
    return f"""Invent a fictional but completely believable {_type_line(spec)} and plan its website.

{_style_block(spec)}
{_app_note(spec)}
Plan it like a real site of this type would be built:
- Organisation: name, place, people with names and roles, concrete offerings with real-looking prices, dates,
  numbers. Specific details, never placeholders or lorem ipsum. Avoid cliched names (no "Acme", "Lumina", "Nexus").
- Pages: choose 5 to 7 pages that a real {spec["subclass"]} site would have. The first is the home page, file
  "index.html"; other files are short kebab-case names ending in .html. At least one page must be information-dense
  in the way this type of site naturally is (for example a full menu, price table, schedule, catalogue grid, long
  article, booking or application form, data table).
- Length: each page is 3 to 6 screens tall at {SCREEN[0]}x{SCREEN[1]} (about {3 * SCREEN[1]}-{6 * SCREEN[1]}px); the
  one dense page may reach 7 screens. Plan sections to fit.
- Copy: concise and specific, like a professionally designed site. Headlines, short paragraphs, labels, lists and
  numbers. Balance text with visual components (images, cards, stats, icons, tables). Only an article-type page
  should have long running text. Every repeated fact (people, roles, counts, prices, dates) goes in "facts" once and
  pages reuse it exactly, so pages never contradict each other.

{COPY_RULES}
- Images: plan them the way a real site of this type and style would use imagery (a store shows a photo for every
  product card; a text-led studio may use few). Every planned image must be used. At most 20 images in total.
  For each: a filename (images/<kebab-name>.jpg), its display size in px (width <= 1600, height <= 1200), the page
  it appears on, and a detailed prompt for an image generator describing the subject, composition and framing.
  Also give one site-wide "image_style" line (lighting, colour grading, background, photo vs illustration) that
  matches the style direction, so all images look like one shoot. Every image is a distinct shot (never plan the
  same subject twice) and fits the organisation's location, season and clientele. Images must never contain text,
  logos or watermarks, including printed text or graphics on clothing, packaging and signs.

Return ONLY a JSON object (no prose, no code fence) with this shape:
{{
  "org": {{"name": "...", "tagline": "...", "summary": "...", "location": "...", "founded": "..."}},
  "facts": {{ ...structured specifics the pages draw on: offerings, prices, people, stats... }},
  "image_style": "...",
  "nav": [{{"label": "...", "file": "index.html"}}],
  "pages": [{{"file": "index.html", "title": "...", "purpose": "...", "sections": [{{"name": "...", "content": "..."}}]}}],
  "images": [{{"file": "images/hero.jpg", "width": 1440, "height": 720, "page": "index.html", "prompt": "..."}}]
}}"""


def design_system_prompt(spec: dict, brief: dict, fonts: list[str]) -> str:
    app = spec["class_id"] == APP_CLASS
    chrome = ("the app shell: header.html holds the persistent sidebar and/or top bar; footer.html may be empty or a "
              "thin status bar" if app else
              "header.html is the site header with navigation; footer.html is a substantial footer (columns, contact "
              "details, small print)")
    return f"""Create the shared design system for this {_type_line(spec)} website: one stylesheet plus the shared
chrome that appears on every page.

{_style_block(spec)}

Site brief:
{json.dumps(brief, indent=2)}

{HARD_RULES.format(fonts=fonts_line(fonts))}

Requirements:
- styles.css starts with a :root block of design tokens (colours, type scale, spacing, radii, borders, shadows) taken
  from the style direction, then base element styles, layout helpers, and the reusable components the pages need
  (buttons, cards, tables, form fields, badges, section headers, ...).
- Shared chrome: {chrome}. It links to every page in the brief's nav, using its relative hrefs exactly.
- Each page's <body> has exactly three children in this order: the header.html markup, one <main>, the footer.html
  markup. Style body accordingly (for example a grid when the navigation is a sidebar).
- The look must strongly express the style direction. Do not set a fixed height on html/body.

{LAYOUT_RULES}

{CSS_RULES}

{COPY_RULES}

Return exactly three files in this format and nothing else:
<file name="styles.css">
...css...
</file>
<file name="header.html">
...markup...
</file>
<file name="footer.html">
...markup (may be empty)...
</file>"""


def page_prompt(spec: dict, brief: dict, page: dict, styles_css: str, header: str, footer: str,
                fonts: list[str]) -> str:
    images = [im for im in brief.get("images", []) if im.get("page") == page["file"]]
    return f"""Build the main content of one page of this {_type_line(spec)} website.

{_style_block(spec)}
{_app_note(spec)}
Site brief (shared by all pages):
{json.dumps(brief, indent=2)}

Shared stylesheet styles.css (already linked on every page; reuse its tokens and components):
<styles.css>
{styles_css}
</styles.css>

Shared chrome (inserted automatically around your <main>; do not repeat it):
{header}
{footer}

{HARD_RULES.format(fonts=fonts_line(fonts))}

{LAYOUT_RULES}

{CSS_RULES}

{COPY_RULES}

Now build the page "{page["title"]}" ({page["file"]}). Purpose: {page.get("purpose", "")}
Planned sections:
{json.dumps(page["sections"], indent=2)}

Images planned for this page (use each one exactly once, at roughly the planned size):
{json.dumps(images, indent=2) if images else "none"}

Requirements:
- Implement every planned section with its concrete content. No placeholders.
- Keep the page 3 to 6 screens tall at {SCREEN[0]}x{SCREEN[1]} (7 at most for a dense page). Concise copy; let layout,
  imagery and components carry the design.
- Reuse styles.css tokens and components. Put page-specific CSS in page.css, using the var(--...) tokens.
- Make the page visually rich and true to the style direction while staying consistent with the rest of the site.

Return exactly two files in this format and nothing else:
<file name="page.css">
...page-specific css (may be empty)...
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
- broken_images, external_requests, fonts, has_script, dead_links, near_blank: the page breaks the hard rules above."""


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

{HARD_RULES.format(fonts=fonts_line(fonts))}

{LAYOUT_RULES}

{CSS_RULES}

{COPY_RULES}

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

{HARD_RULES.format(fonts=fonts_line(fonts))}

{LAYOUT_RULES}

{CSS_RULES}

{COPY_RULES}

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
