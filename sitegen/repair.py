"""QC-driven repair: render the site, send the defects QC found back to the generator, reassemble, re-render.

Defects in the shared chrome (outside <main>) are fixed once in the design system, the rest per page. At most
`rounds` rounds; if the last round left more defects than an earlier one, the best version is restored. Each call
is one run appended to meta/repairs.json.
"""
import asyncio
import json
from pathlib import Path

from . import prompts
from .generate import FONTS, Log, _need, assemble_site, parse_files, photo
from .llm import complete_with_retry
from .render import render_site

WARN_ONLY = {"nondeterministic_render", "duplicate_image"}  # reported, but not worth a regeneration
# Squeezing a page that is modestly over the 7-screen budget means deleting content (verified on s0007, where it
# gutted a reference page), so pages up to 9 screens are only reported; taller ones are still repaired.
REPAIR_MAX_HEIGHT = 9 * 900


def to_fix(report: dict) -> tuple[list[dict], dict[str, list[dict]]]:
    """Split findings into shared-chrome defects (deduplicated across pages) and per-page defects."""
    chrome, pages = {}, {}
    for name, p in report["pages"].items():
        for f in p["findings"]:
            if f["check"] in WARN_ONLY or (f["check"] == "over_height_budget" and f["height"] <= REPAIR_MAX_HEIGHT):
                continue
            if f["where"] == "chrome":
                key = (f["check"], f.get("el"), f.get("text"))
                chrome.setdefault(key, {**f, "pages": []})["pages"].append(name)
            else:
                pages.setdefault(name, []).append(f)
    return list(chrome.values()), pages


def count(report: dict) -> int:
    chrome, pages = to_fix(report)
    return len(chrome) + sum(len(fs) for fs in pages.values())


async def regenerate_placeholders(out: Path) -> list[str]:
    """Retry the image model for every image that fell back to procedural art; returns the files fixed."""
    path = out / "meta" / "images.json"
    images = json.loads(path.read_text())
    brief = json.loads((out / "meta" / "brief.json").read_text())
    size = {im["file"]: (im.get("width", 1200), im.get("height", 800)) for im in brief.get("images", [])}

    fixed = []
    for im in images:
        if im.get("source") == "model":
            continue
        try:
            info = await photo(out / "site" / im["file"], im["prompt"], *size.get(im["file"], (1200, 800)))
        except Exception:  # noqa: BLE001 - still a placeholder; QC reports it and the page repair removes the slot
            continue
        im.update({"source": "model", "regenerated": True, **info})
        im.pop("error", None)
        fixed.append(im["file"])
    path.write_text(json.dumps(images, indent=2))
    return fixed


def _snapshot(out: Path) -> dict[Path, str]:
    return {p: p.read_text() for p in [out / "site" / "styles.css", *sorted((out / "meta" / "parts").glob("*"))]}


async def repair_site(spec: dict, model: str, out: Path, rounds: int = 2) -> dict:
    site, meta, parts = out / "site", out / "meta", out / "meta" / "parts"
    brief = json.loads((meta / "brief.json").read_text())
    log = Log(meta)
    runs = json.loads((meta / "repairs.json").read_text())["runs"] if (meta / "repairs.json").exists() else []
    run = len(runs) + 1
    regenerated = await regenerate_placeholders(out)
    report = await render_site(site, out / "shots")
    history = [{"round": 0, "regenerated_images": regenerated, "to_fix": count(report), "issues": report["issues"]}]
    best = (count(report), _snapshot(out))

    for r in range(1, rounds + 1):
        chrome, pages = to_fix(report)
        if not chrome and not pages:
            break
        if chrome:
            p = prompts.repair_design_prompt(spec, (site / "styles.css").read_text(), (parts / "header.html").read_text(),
                                             (parts / "footer.html").read_text(), chrome, FONTS)
            c = await complete_with_retry(model, prompts.SYSTEM, p, 64000,
                                          validate=_need("styles.css", "header.html", "footer.html"))
            log.save(f"4_repair{run}.{r}_design", p, c)
            f = parse_files(c.text)
            (site / "styles.css").write_text(f["styles.css"])
            (parts / "header.html").write_text(f["header.html"])
            (parts / "footer.html").write_text(f["footer.html"])
        styles, header, footer = ((site / "styles.css").read_text(), (parts / "header.html").read_text(),
                                  (parts / "footer.html").read_text())

        async def one(name: str, findings: list[dict]):
            page = next(pg for pg in brief["pages"] if pg["file"] == name)
            stem = name.removesuffix(".html")
            p = prompts.repair_page_prompt(spec, page, styles, header, footer, (parts / f"{stem}.page.css").read_text(),
                                           (parts / f"{stem}.main.html").read_text(), findings, FONTS)
            c = await complete_with_retry(model, prompts.SYSTEM, p, 64000, validate=_need("page.css", "main.html"))
            log.save(f"4_repair{run}.{r}_page_{stem}", p, c)
            f = parse_files(c.text)
            (parts / f"{stem}.page.css").write_text(f["page.css"])
            (parts / f"{stem}.main.html").write_text(f["main.html"])

        await asyncio.gather(*(one(name, fs) for name, fs in pages.items()))
        assemble_site(out)
        report = await render_site(site, out / "shots")
        history.append({"round": r, "repaired": {"chrome": len(chrome), "pages": sorted(pages)},
                        "to_fix": count(report), "issues": report["issues"]})
        if count(report) < best[0]:
            best = (count(report), _snapshot(out))

    if count(report) > best[0]:
        for path, text in best[1].items():
            path.write_text(text)
        assemble_site(out)
        report = await render_site(site, out / "shots")
        history.append({"restored_best": best[0], "to_fix": count(report), "issues": report["issues"]})
    runs.append({"history": history, "calls": log.calls})
    (meta / "repairs.json").write_text(json.dumps({"runs": runs}, indent=2))
    return {"report": report, "history": history, "calls": log.calls}
