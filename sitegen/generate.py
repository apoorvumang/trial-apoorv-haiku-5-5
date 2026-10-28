"""Generate one multi-page site from a spec (site type + style direction):
brief -> images (parallel with design system) -> pages (parallel) -> assembled HTML.

Everything needed to rebuild a site is saved under <out>/meta: spec, generator model, every prompt and response, and
the parts each page is assembled from (meta/parts: shared header/footer, per-page page.css + main.html), so the
repair step can regenerate single parts and reassemble.
"""
import asyncio
import json
import re
import shutil
from pathlib import Path

from . import prompts
from .assets import generate_photo, make_image
from .llm import Completion, complete_with_retry

ROOT = Path(__file__).resolve().parent.parent
FONTS_DIR = ROOT / "fonts"
FONTS = [f["family"] for f in json.loads((FONTS_DIR / "manifest.json").read_text())]

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="fonts/fonts.css">
<link rel="stylesheet" href="styles.css">
{page_css}</head>
<body class="page-{slug}">
{header}
{main}
{footer}
</body>
</html>
"""


def parse_files(text: str) -> dict[str, str]:
    return {m.group(1): m.group(2).strip() + "\n"
            for m in re.finditer(r'<file name="([^"]+)">\s*\n?(.*?)</file>', text, re.S)}


def parse_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def _need(*names):
    def check(text):
        files = parse_files(text)
        missing = [n for n in names if n not in files]
        if missing:
            raise ValueError(f"missing files in response: {missing}")
    return check


def _check_brief(text):
    b = parse_json(text)
    files = [p["file"] for p in b["pages"]]
    if not 5 <= len(files) <= 8:
        raise ValueError(f"brief has {len(files)} pages, need 5-8")
    if files[0] != "index.html" or len(set(files)) != len(files):
        raise ValueError(f"bad page files: {files}")
    if not all(re.fullmatch(r"[a-z0-9-]+\.html", f) for f in files):
        raise ValueError(f"page files must be kebab-case .html: {files}")


def assemble_site(out: Path) -> None:
    """Write site/<page>.html for every page in the brief from meta/parts."""
    parts = out / "meta" / "parts"
    brief = json.loads((out / "meta" / "brief.json").read_text())
    header, footer = (parts / "header.html").read_text().strip(), (parts / "footer.html").read_text().strip()
    for i, page in enumerate(brief["pages"]):
        stem = page["file"].removesuffix(".html")
        page_css = (parts / f"{stem}.page.css").read_text().strip()
        (out / "site" / page["file"]).write_text(PAGE_TEMPLATE.format(
            title=brief["org"]["name"] if i == 0 else f"{page['title']} | {brief['org']['name']}",
            page_css=f"<style>\n{page_css}\n</style>\n" if page_css else "",
            slug=stem,
            header=mark_current(header, page["file"]),
            main=(parts / f"{stem}.main.html").read_text().strip(),
            footer=mark_current(footer, page["file"]),
        ))


def mark_current(chrome: str, file: str) -> str:
    """Add aria-current="page" to nav links pointing at this page (so nav state is per-page, not per-site)."""
    return re.sub(rf'(<a\b[^>]*href="{re.escape(file)}")', r'\1 aria-current="page"', chrome)


class Log:
    def __init__(self, meta: Path):
        self.meta, self.calls = meta, []

    def save(self, stage: str, prompt: str, c: Completion):
        (self.meta / "calls").mkdir(parents=True, exist_ok=True)
        (self.meta / "calls" / f"{stage}.prompt.txt").write_text(prompt)
        (self.meta / "calls" / f"{stage}.response.txt").write_text(c.text)
        self.calls.append({"stage": stage, "model": c.model, "input_tokens": c.input_tokens,
                           "output_tokens": c.output_tokens, "seconds": round(c.seconds, 1)})


async def photo(path: Path, prompt: str, width: int, height: int, attempts: int = 3) -> dict:
    """generate_photo with retries. The image model sometimes returns nothing, and its safety filter rejects some
    harmless long prompts (content_safety_violation), so the last attempt sends only the subject sentence."""
    for i in range(attempts):
        p = prompt if i < attempts - 1 else prompt.split(". ")[0].rstrip(".") + ". No text, letters, logos or watermarks."
        try:
            info = await generate_photo(path, p, width, height)
            return info if p == prompt else {**info, "prompt_used": p}
        except Exception:  # noqa: BLE001 - retried, then the caller falls back
            if i == attempts - 1:
                raise
            await asyncio.sleep(5 * (i + 1))


async def make_images(brief: dict, site: Path, seed: int) -> list[dict]:
    style = brief.get("image_style", "")
    palette = [h for h in re.findall(r"#[0-9a-fA-F]{6}", json.dumps(brief))][:6]

    async def one(im):
        name = Path(im["file"]).name
        if not name.endswith(".jpg"):
            name = Path(name).stem + ".jpg"
        im["file"] = f"images/{name}"
        prompt = (f"{im['prompt']}. {style}. No text, letters, numbers, logos or watermarks anywhere in the image, "
                  "including on clothing, packaging and signs. No borders, white outlines or sticker-style cut-out edges.")
        try:
            info = await photo(site / "images" / name, prompt, im.get("width", 1200), im.get("height", 800))
            return {"file": im["file"], "source": "model", "prompt": prompt, **info}
        except Exception as e:  # noqa: BLE001 - one failed image falls back, it must not fail the site
            make_image(site / "images" / name, im.get("width", 1200), im.get("height", 800), palette, seed, name)
            return {"file": im["file"], "source": "procedural_fallback", "prompt": prompt, "error": str(e)}

    return await asyncio.gather(*(one(im) for im in brief.get("images", [])))


async def generate_site(spec: dict, model: str, out: Path) -> dict:
    site, meta = out / "site", out / "meta"
    if out.exists():
        shutil.rmtree(out)
    (site / "images").mkdir(parents=True)
    meta.mkdir(parents=True)
    (meta / "spec.json").write_text(json.dumps({**spec, "generator": model}, indent=2))
    log = Log(meta)

    # 1. brief
    p = prompts.brief_prompt(spec)
    c = await complete_with_retry(model, prompts.SYSTEM, p, 40000, validate=_check_brief)
    log.save("1_brief", p, c)
    brief = parse_json(c.text)
    brief["images"] = brief.get("images", [])[:30]
    shutil.copytree(FONTS_DIR, site / "fonts", ignore=shutil.ignore_patterns("manifest.json"))

    # 2. images and design system in parallel (both only need the brief)
    async def design_system():
        p = prompts.design_system_prompt(spec, brief, FONTS)
        c = await complete_with_retry(model, prompts.SYSTEM, p, 64000,
                                      validate=_need("styles.css", "header.html", "footer.html"))
        log.save("2_design_system", p, c)
        return parse_files(c.text)

    images, ds = await asyncio.gather(make_images(brief, site, spec["seed"]), design_system())
    (meta / "brief.json").write_text(json.dumps(brief, indent=2))
    (meta / "images.json").write_text(json.dumps(images, indent=2))
    (site / "styles.css").write_text(ds["styles.css"])
    parts = meta / "parts"
    parts.mkdir()
    (parts / "header.html").write_text(ds["header.html"])
    (parts / "footer.html").write_text(ds["footer.html"])

    # 3. pages, in parallel
    async def one(i: int, page: dict):
        p = prompts.page_prompt(spec, brief, page, ds["styles.css"], ds["header.html"], ds["footer.html"], FONTS)
        c = await complete_with_retry(model, prompts.SYSTEM, p, 64000, validate=_need("main.html"))
        log.save(f"3_page_{i}_{page['file'].removesuffix('.html')}", p, c)
        f = parse_files(c.text)
        stem = page["file"].removesuffix(".html")
        (parts / f"{stem}.page.css").write_text(f.get("page.css", ""))
        (parts / f"{stem}.main.html").write_text(f["main.html"])

    await asyncio.gather(*(one(i, pg) for i, pg in enumerate(brief["pages"])))
    assemble_site(out)

    image_cost = sum(i.get("cost") or 0 for i in images)
    summary = {"model": model, "org": brief["org"]["name"], "pages": [p["file"] for p in brief["pages"]],
               "images": len(images), "images_fallback": sum(i["source"] != "model" for i in images),
               "image_cost": round(image_cost, 3), "calls": log.calls,
               "output_tokens": sum(c["output_tokens"] for c in log.calls),
               "input_tokens": sum(c["input_tokens"] for c in log.calls)}
    (meta / "generation.json").write_text(json.dumps(summary, indent=2))
    return summary
