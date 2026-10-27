"""Download the bundled font set from Fontsource and write fonts/fonts.css + fonts/manifest.json.

Run once; the output is committed so every site, agent sandbox and verifier uses identical font files.
"""
import json
import urllib.request
from pathlib import Path

FAMILIES = [
    # sans
    "inter", "dm-sans", "manrope", "space-grotesk", "ibm-plex-sans", "work-sans",
    "outfit", "sora", "nunito", "syne", "archivo",
    # serif
    "playfair-display", "fraunces", "lora", "libre-baskerville", "cormorant-garamond",
    "dm-serif-display", "source-serif-4",
    # display / mono
    "bebas-neue", "space-mono", "jetbrains-mono", "ibm-plex-mono",
]
WEIGHTS = [300, 400, 500, 600, 700, 800]
OUT = Path(__file__).resolve().parent.parent / "fonts"


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "design2code-env/0.1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    manifest, css = [], []
    for fid in FAMILIES:
        meta = json.loads(get(f"https://api.fontsource.org/v1/fonts/{fid}"))
        family, version = meta["family"], meta["version"]
        entry = {"id": fid, "family": family, "category": meta["category"],
                 "version": version, "license": meta["license"], "weights": [], "italic": []}
        for w in [w for w in WEIGHTS if w in meta["weights"]]:
            for style in ("normal", "italic"):
                if style == "italic" and (style not in meta["styles"] or w not in (400, 700)):
                    continue
                v = meta["variants"].get(str(w), {}).get(style, {}).get("latin")
                if not v:
                    continue
                # pin the exact version instead of @latest
                url = v["url"]["woff2"].replace("@latest", f"@{meta['npmVersion']}")
                fname = f"{fid}-{w}-{style}.woff2"
                (OUT / fname).write_bytes(get(url))
                (entry["italic"] if style == "italic" else entry["weights"]).append(w)
                css.append(
                    f"@font-face {{ font-family: '{family}'; font-style: {style}; font-weight: {w}; "
                    f"font-display: block; src: url('{fname}') format('woff2'); }}"
                )
        manifest.append(entry)
        print(f"{family:22s} {meta['category']:11s} weights={entry['weights']} italic={entry['italic']}")
    (OUT / "fonts.css").write_text("\n".join(css) + "\n")
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
