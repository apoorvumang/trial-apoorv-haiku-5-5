"""Generate + render sites: sample site type -> generate style variants -> pick one -> build site -> render + repair.

    python -m sitegen.cli --seeds 0 1 2
    python -m sitegen.cli --seeds 0 --models claude-opus-5-5
    python -m sitegen.cli --seeds 1 2 3 --specs-only      # review specs (out/specs/) before building
    python -m sitegen.cli --render-only out/sites/s0000
    python -m sitegen.cli --repair-only out/sites/s0000   # QC repair rounds on an existing site
"""
import argparse
import asyncio
import json
import traceback
from pathlib import Path

from .generate import ROOT, generate_site
from .llm import GENERATOR_POOL
from .render import render_site
from .repair import repair_site
from .styles import generate_variants, pick_variant
from .taxonomy import sample_site_type

VARIANT_MODEL = "claude-opus-5-5"


def pick_model(seed: int) -> str:
    return GENERATOR_POOL[seed % len(GENERATOR_POOL)]


async def make_spec(seed: int, model: str, out_root: Path) -> dict:
    """Site type -> 5 style variants -> pick one. Cached in <out>/specs/ so a reviewed spec is exactly what gets built."""
    path = out_root.parent / "specs" / f"s{seed:04d}.json"
    if path.exists():
        return json.loads(path.read_text())
    site_type = sample_site_type(seed)
    v = await generate_variants(site_type, VARIANT_MODEL)
    idx = pick_variant(v["variants"], seed)
    spec = {**site_type, "variant_index": idx, "style": v["variants"][idx], "viewport": [1440, 900],
            "generator": model, "variants": v}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec, indent=2))
    return spec


async def build(seed: int, model: str, out_root: Path, sem: asyncio.Semaphore, specs_only: bool = False) -> dict:
    out = out_root / f"s{seed:04d}"
    async with sem:
        try:
            full = await make_spec(seed, model, out_root)
            spec = {k: v for k, v in full.items() if k not in ("variants", "generator")}
            model = full["generator"]
            print(f"[s{seed:04d}] {spec['class']} / {spec['subclass']} -> variant {spec['variant_index'] + 1} "
                  f"\"{spec['style']['name']}\", generator {model}", flush=True)
            if specs_only:
                return {"seed": seed, "model": model, "ok": True}
            summary = await generate_site(spec, model, out)
            (out / "meta" / "variants.json").write_text(json.dumps(full["variants"], indent=2))
            rep = await repair_site(spec, model, out)
            report, fixes = rep["report"], [h["to_fix"] for h in rep["history"]]
            print(f"[s{seed:04d}] done: {summary['org']}, {len(summary['pages'])} pages, {summary['images']} images "
                  f"({summary['images_fallback']} fallback), {summary['output_tokens']} out tokens, "
                  f"defects to fix per round {fixes}, {len(report['issues'])} issues left", flush=True)
            return {"seed": seed, "model": model, "ok": True, **summary, "defects_per_round": fixes,
                    "issues": report["issues"]}
        except Exception as e:  # noqa: BLE001 - one bad site must not kill the batch
            traceback.print_exc()
            return {"seed": seed, "model": model, "ok": False, "error": str(e)}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="*", default=[0])
    ap.add_argument("--models", nargs="*", help="one per seed (round-robin); default: sampled from the pool")
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "sites")
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--render-only", type=Path, nargs="*")
    ap.add_argument("--repair-only", type=Path, nargs="*")
    ap.add_argument("--specs-only", action="store_true", help="only sample types + style variants, no site build")
    a = ap.parse_args()

    if a.render_only:
        for d in a.render_only:
            r = await render_site(d / "site", d / "shots")
            print(d.name, json.dumps(r["issues"], indent=1))
        return
    if a.repair_only:
        async def one(d):
            spec = json.loads((d / "meta" / "spec.json").read_text())
            rep = await repair_site(spec, spec["generator"], d)
            print(d.name, json.dumps([{k: h[k] for k in h if k != "issues"} for h in rep["history"]]),
                  json.dumps(rep["report"]["issues"], indent=1), flush=True)
        await asyncio.gather(*(one(d) for d in a.repair_only))
        return

    sem = asyncio.Semaphore(a.concurrency)
    models = [a.models[i % len(a.models)] if a.models else pick_model(s) for i, s in enumerate(a.seeds)]
    results = await asyncio.gather(*(build(s, m, a.out, sem, a.specs_only) for s, m in zip(a.seeds, models)))
    if a.specs_only:
        return
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / f"batch_summary_{'-'.join(map(str, a.seeds))}.json").write_text(json.dumps(results, indent=2))
    for r in results:
        print(json.dumps({k: r.get(k) for k in ("seed", "model", "ok", "org", "output_tokens", "issues", "error")}))


if __name__ == "__main__":
    asyncio.run(main())
