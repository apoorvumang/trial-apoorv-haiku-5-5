"""Review images for a batch: one sheet per site with every page's full-length overview side by side."""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

COL_W, MAX_H, PAD = 300, 2400, 16


def site_sheet(d: Path) -> Path | None:
    spec = json.loads((d / "meta" / "spec.json").read_text())
    gen = json.loads((d / "meta" / "generation.json").read_text())
    full = sorted((d / "shots" / "full").glob("*.png"), key=lambda p: (p.stem != "index", p.stem))
    if not full:
        return None
    thumbs = []
    for p in full:
        im = Image.open(p).convert("RGB")
        im = im.resize((COL_W, round(im.height * COL_W / im.width)), Image.Resampling.LANCZOS)
        thumbs.append((p.stem, im.crop((0, 0, COL_W, min(im.height, MAX_H)))))
    h = max(t.height for _, t in thumbs)
    sheet = Image.new("RGB", (PAD + len(thumbs) * (COL_W + PAD), 70 + h + PAD), "white")
    d_ = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=18)
    small = ImageFont.load_default(size=13)
    d_.text((PAD, 10), f"{d.name}  {gen['org']}  |  {spec['class']} / {spec['subclass']}  |  "
            f"style: {spec['style']['name']}  |  {spec['generator']}", fill="black", font=font)
    for i, (name, t) in enumerate(thumbs):
        x = PAD + i * (COL_W + PAD)
        d_.text((x, 44), name, fill="#555", font=small)
        sheet.paste(t, (x, 64))
        d_.rectangle([x - 1, 63, x + COL_W, 64 + t.height], outline="#ccc")
    out = d / "sheet.png"
    sheet.save(out)
    return out


if __name__ == "__main__":
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "out/sites")
    for d in sorted(p for p in root.iterdir() if (p / "meta" / "generation.json").exists()):
        print(site_sheet(d))
