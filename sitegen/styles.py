"""Style variants: given a site type, ask a model for N visually distinct style directions, then pick one at random.

Variety comes from asking for a spread of variants in ONE call (so the model sees and avoids its own repeats),
not from sampling style axes in code.
"""
import json
import random
from pathlib import Path

from .llm import complete_with_retry

FONTS = [f["family"] for f in json.loads((Path(__file__).resolve().parent.parent / "fonts" / "manifest.json").read_text())]

SYSTEM = ("You are an art director who has seen thousands of real websites across every industry. You write precise, "
          "concrete style directions that another designer can execute without guessing.")


def variants_prompt(site_type: dict, n: int = 5) -> str:
    return f"""We need {n} different visual style directions for a website of this type:

Class: {site_type["class"]}
Subclass: {site_type["subclass"]}

First think about the real range of how websites of this type look in the wild: budget to premium, traditional to
experimental, local to global, young to established. Then write {n} style directions that a real organisation of this
type could plausibly have, spread as widely as possible across that range.

Variety rules:
- Any two directions must differ clearly on at least 3 of: light vs dark base; colour temperature and saturation;
  typography character (serif, grotesk, geometric, monospace, display); layout structure (centred single column,
  asymmetric grid, sidebar, dense multi-column, full-bleed image bands, card grid); density and whitespace; imagery
  treatment (full-bleed photos, cut-out objects, illustration or SVG, almost no images); decoration (rules and borders,
  shadows, gradients, textures, none); era or reference point.
- At most one direction may be the generic "clean modern sans-serif, white background, one blue/brand accent" look.
- Each must still feel right for a {site_type["subclass"]} site, not a costume.
- The site will be judged from static full-page screenshots, so describe only what is visible on load: no hover
  states, accordions, carousels, marquees, animation, cursor effects or embedded feeds.

Each direction must be concrete enough that two designers given it would build visibly similar sites. Fonts must come
from this list only: {", ".join(FONTS)}.

Return ONLY a JSON object, no prose:
{{"variants": [{{
  "name": "2-4 word name",
  "positioning": "one line: what kind of {site_type["subclass"]} would have this look (size, price point, audience, age)",
  "description": "80-150 words, plain text: overall look and mood, layout approach, colour palette with hex codes, typography (which listed fonts for headings/body and how they are used), imagery, shapes and components, notable details",
  "palette": ["#rrggbb", "..."],
  "fonts": {{"headings": "...", "body": "..."}}
}}]}}"""


def _parse(text: str) -> dict:
    s, e = text.find("{"), text.rfind("}")
    return json.loads(text[s:e + 1])


async def generate_variants(site_type: dict, model: str, n: int = 5) -> dict:
    def check(text):
        v = _parse(text)["variants"]
        if len(v) != n:
            raise ValueError(f"expected {n} variants, got {len(v)}")
    p = variants_prompt(site_type, n)
    c = await complete_with_retry(model, SYSTEM, p, 32000, validate=check)
    return {"site_type": site_type, "model": model, "prompt": p, "variants": _parse(c.text)["variants"],
            "usage": {"input_tokens": c.input_tokens, "output_tokens": c.output_tokens, "seconds": round(c.seconds, 1)}}


def pick_variant(variants: list[dict], seed: int) -> int:
    return random.Random(f"variant:{seed}").randrange(len(variants))
