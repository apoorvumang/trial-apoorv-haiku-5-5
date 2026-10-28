# Design-to-code RL environments

A pipeline that generates realistic multi-page websites from scratch and turns them into Harbor tasks: an agent gets
screenshots of every page plus the image assets, rebuilds the site in HTML and CSS, and is graded continuously.

## Generating sites

```bash
uv sync && uv run playwright install chromium
# sample site types and style directions only, to review before building
uv run python -m sitegen.cli --taxonomy v2 --out out/v2/sites --seeds 1 2 3 --specs-only
# build: brief -> images + design system -> pages -> render + QC -> repair
uv run python -m sitegen.cli --taxonomy v2 --out out/v2/sites --seeds 1 2 3
uv run python scripts/contact_sheet.py out/v2/sites      # one overview image per site
```

API keys go in `.env` (Anthropic for Opus, OpenRouter for Sol and the image model). Outputs go under `out/`, which is
not committed.

## Docs

- [docs/TAXONOMY_V2.md](docs/TAXONOMY_V2.md): how sites are sampled (site genre → subtype → 5 likely styles → pick
  one), what changed from v1 and why, the minimal build prompts, and the first build.
- [docs/DESIGN.md](docs/DESIGN.md): the original design draft for the whole recipe (generation, grading, validation).

## Layout

- `sitegen/`: generation pipeline (taxonomy, styles, prompts, generate, render + QC, repair, CLI)
- `scripts/`: font fetcher, contact sheets
- `fonts/`: the self-hosted font set every site and agent uses
