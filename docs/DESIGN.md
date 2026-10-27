# Design: an RL environment recipe for design-to-code

Status: **draft v0**, written before any pipeline code. It records the decisions and the reasons behind them, so later changes can point back to what they replace.

## 1. The task, stated precisely

An agent (Claude Code + Haiku 5.5 inside a Harbor sandbox) gets **screenshots of every page of a generated multi-page website**, plus the raster assets those pages use. It must write static **HTML + CSS** that reproduces the design. Functionality is out of scope. A verifier renders the agent's pages under the same conditions as the reference and returns a **continuous reward in [0, 1]**.

The rest of this doc covers three questions: where the sites come from (section 2), how we grade (section 3, the part that matters most), and how we show that the grade can be trusted (section 4).

---

## 2. Site generation

### 2.1 Requirements

- **No crawling.** Every site is generated from scratch.
- **At least 5 pages per site**, and the pages must look like one site: shared header and footer, one design system.
- **Diversity we control and can measure**, not whatever one model happens to produce.
- **Deterministic rendering.** The same HTML must give the same pixels every time. Otherwise the reward is noisy before the agent does anything.

### 2.2 Pipeline

```
seed ──► spec sampler ──► site brief (LLM) ──► design system ──► pages ──► render + filter ──► Harbor task
          (structured)     (brand, copy,        (CSS tokens,     (5–8 HTML)  (Playwright,       (instruction,
                            sitemap)             components)                  quality gates)     env, tests)
```

**1. Structured spec sampler (code, seeded).** Diversity comes from explicit axes we sample, not from asking a model to "be creative":

| Axis | Examples |
|---|---|
| Site archetype | SaaS marketing, e-commerce, restaurant, portfolio, news/magazine, docs, nonprofit, agency, event/conference, real estate, local services, education, personal blog |
| Visual style | Swiss minimal, editorial/serif, brutalist, playful/rounded, corporate, dark tech, retro, luxury, glassy/gradient, illustrated |
| Palette strategy | Monochrome + accent, complementary, dark mode, pastel, high-contrast |
| Type pairing | From a fixed font set baked into the image (see 2.4) |
| Density / complexity tier | Low, medium, high: number of sections, grid complexity, component variety |
| Page set | 5–8 pages drawn from home, listing/grid, detail, about, pricing, contact, article, FAQ, team, careers, menu, gallery, dashboard-like |
| Layout motifs | Asymmetric hero, bento grid, sidebar, card masonry, split screens, long-form article, tables |

Some combinations need rules (a restaurant site has a menu page, docs sites have a sidebar), so we use per-archetype page templates plus weighted sampling. Every axis value is saved with the task as metadata. That lets us report the distribution and slice results by axis later.

**2. Brief (LLM).** Turns the sampled spec into a concrete brand: name, tone, real-sounding copy, and a sitemap with what each page contains. Copy is written here once, so it's consistent across pages.

**3. Design system, then pages (LLM, multi-model pool).** The generator first writes a shared `styles.css`: tokens, components, header and footer. It then writes each page against that file. That's how 5+ pages end up looking like one site and not five unrelated pages.

**Generator pool.** We use several top models from Design Arena (2026-10-27): Claude Opus 5.5 / Sonnet 5.5, GPT-6 Astra, Gemini 4 Argon, Kimi K3, Muse Wonder. The generator is sampled per site and recorded. There are two reasons:
- *Style diversity.* Every model has a house style. A single-generator dataset teaches the policy to copy that one look.
- *Same-family bias.* Haiku may find Claude-made sites easier because they match its own defaults. A mixed pool lets us **measure** that (score by generator family) instead of baking it in.

### 2.3 Quality gates (automatic filtering)

Each candidate site is rendered and rejected if:
- a page fails to render, is mostly blank, or overflows horizontally at the target viewport;
- it makes any external network request (the sites must be self-contained);
- internal nav links are broken or pages are near-duplicates (visual similarity too high);
- it's too simple for its complexity tier (DOM node / text-block / distinct-color counts below the tier minimum);
- a VLM quality check rates it implausible or broken as a professional site (an LLM is used only as a filter here, not for reward).

The rejection rate per generator gets reported. It's a useful signal in its own right.

### 2.4 Stability decisions

- **Fonts.** The generator may only use a fixed set of about 20 open-source font families, installed in both the generation and verification images. The agent is told which fonts are available. Without this, font fallback differences add noise that has nothing to do with skill.
- **Images.** Photos can't be rebuilt from a screenshot, and asking the agent to try would just add noise. Raster images (generated or procedural) ship with the task as named assets, and the agent is told to use them. Icons and decorations are inline SVG/CSS, which the agent *is* expected to reproduce.
- **Rendering.** Pinned Chromium (Playwright), fixed viewport (1440px wide; full-page captures), `deviceScaleFactor=1`, animations and transitions disabled, caret hidden, web fonts awaited before capture. Rendering the same page twice must give identical pixels; that's a CI check.
- **Viewport scope.** Desktop only for v0. Mobile/responsive is a natural axis to add later; it needs its own screenshots and changes how hard the task is.

### 2.5 Harbor task layout

```
task/
  task.toml               # metadata: archetype, style, tier, generator, page count
  instruction.md          # what to build, page filenames, fonts, assets, constraints
  environment/
    Dockerfile            # pinned Chromium + fonts; screenshots + assets copied into /app
  tests/                  # uploaded only AFTER the agent finishes: the agent never sees the reference
    reference/            # reference HTML/CSS + DOM/geometry dumps
    test.sh  grade.py     # render candidate, score, write /logs/verifier/reward.json
```

The agent's container also has Chromium, so the agent **can render and screenshot its own pages to check them**, which is how a human would work. Whether Haiku actually does this, and whether it helps, is one of the behaviors we want to observe.

---

## 3. Grading (the core of the recipe)

### 3.1 What the reward has to do

1. **Monotonic in real quality.** A better replica should score higher, across the whole range, not just pass/fail.
2. **Dense.** Partial credit for partial progress, so RL has a gradient.
3. **Hard to hack.** The policy will optimize whatever we measure. Anything gameable will get gamed.
4. **Deterministic and cheap.** It has to run thousands of times without an LLM in the loop.

### 3.2 Why not just pixels or a VLM judge

- **Pixel metrics alone (MSE/SSIM on full screenshots)** are brittle. A near-perfect page that's 12px taller has every pixel below that point shifted, and the score collapses. They also over-reward "a big beige rectangle" on pages with lots of background.
- **A VLM judge as the reward** is noisy, non-deterministic and expensive. It's also exploitable (policies learn what judges like) and weak at fine detail (8px vs 16px padding, #1F2937 vs #111827). We use a VLM only to **validate** the grader (section 4), never as the reward.
- **Our edge:** we *generated* the reference, so we have its exact DOM, computed styles and element geometry, not just a picture. The grader uses that.

### 3.3 Score components (per page)

The reference and the candidate are rendered under identical conditions. We extract **visual blocks** from each: text runs, images, and boxes with visible backgrounds or borders, each with its bounding box and computed style.

| Component | What it measures | How |
|---|---|---|
| **Text content** | Is the right text there? | Block matching via text similarity + Hungarian assignment; recall weighted by text area |
| **Layout / position** | Is it in the right place at the right size? | For matched blocks: normalized center distance + size ratio, read relative to the page's own landmarks so one shift doesn't spread to everything below it |
| **Typography** | Font family, size, weight, line height | Compared on matched text blocks |
| **Color** | Text and background colors | CIEDE2000 on matched blocks, plus a palette comparison over the page |
| **Visual similarity (global)** | Everything the blocks miss: gradients, borders, shadows, SVG decorations | Perceptual similarity on height-aligned, downscaled screenshots (multi-scale SSIM, possibly a learned perceptual metric) |

Page score = weighted combination of the components. Site score = mean over pages, plus a small **site-coherence** term: shared header and footer rendered consistently, and nav links pointing at the right page files. That's a static link check, not functionality. All components go into `reward.json` so we can diagnose failures. The headline reward is one number in [0, 1].

The block extraction and matching follow the low-level metrics of **Design2Code** (Si et al., 2024), adapted for multi-page sites, with extra handling so a single vertical shift doesn't dominate the score.

### 3.4 Anti-hacking gates

- **Screenshots can't be embedded.** Any reference to a provided screenshot, or any large raster not among the given assets, zeroes the reward. Text must exist as real DOM text, which the text component enforces anyway.
- **HTML + CSS only.** No `<canvas>` drawing and no JS that builds the page. A small JS allowance may be added later for Part 3's frameworks.
- **Missing pages score 0** for that page. There's no partial credit for leaving pages out.

---

## 4. Proving the grader (validation plan)

The brief asks: *do higher grades correspond to better replications?* Our answer is a set of experiments, not an argument:

1. **Anchors.** The reference itself scores about 1.0. An empty page, a lorem-ipsum page and a "wrong site" (another task's reference) score low.
2. **Controlled perturbations.** Take references and apply degradations of increasing severity: shift and resize sections, perturb colors by ΔE, swap fonts, change spacing, drop sections, scramble text. Check that the score **drops monotonically** with severity (Spearman ρ per perturbation type), and see which components react to which degradation.
3. **Agreement with judgment.** For pairs of real Haiku attempts on the same task, ask a strong VLM judge (and hand-label a subset) which one is the better replica. Report how often the grader agrees. This is where an LLM earns its place: offline, as the check on our grader.
4. **Stability.** Re-grading the same output gives the same score. We also report run-to-run variance across Haiku's 10 attempts per task.

---

## 5. Experiments and deliverables

- **10+ showcase tasks** chosen to cover the archetype, style, tier and generator axes.
- **Claude Code + Haiku 5.5, 10 runs per task:** score distributions, component breakdowns, per-axis slices (style, tier, generator family) and examples at low, mid and high scores side by side with the reference.
- **Behavior analysis:** what Haiku gets wrong most often. Hypotheses to test: spacing and vertical rhythm, exact colors, font sizes, complex grids, keeping the header consistent across pages, running out of budget before finishing every page, and whether it ever renders its own output to check it.

## 6. Open questions

- Weights between components: start from the perturbation study and adjust until agreement with the judge is highest. Then freeze them, so we don't tune the grader against the model we're evaluating.
- Whether to add a learned perceptual metric to the verifier image (heavier) or stay with SSIM-family metrics.
- Image generation for assets: a real image model, or procedural/abstract images (cheaper, fully reproducible).
