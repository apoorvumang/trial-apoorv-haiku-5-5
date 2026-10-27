"""Site-type taxonomy (class -> subclass) and the only random sampling in the pipeline."""
import json
import random
from pathlib import Path

TAXONOMY = json.loads((Path(__file__).resolve().parent / "taxonomy.json").read_text())


def sample_site_type(seed: int) -> dict:
    """Class uniformly, then subclass uniformly within it, so big classes don't swamp the long tail."""
    rng = random.Random(f"site_type:{seed}")
    cls = rng.choice(TAXONOMY["classes"])
    sub = rng.choice(cls["subclasses"])
    return {"seed": seed, "class_id": cls["id"], "class": cls["name"], "subclass_id": sub["id"], "subclass": sub["name"]}


if __name__ == "__main__":
    import sys
    for s in range(int(sys.argv[1]) if len(sys.argv) > 1 else 10):
        t = sample_site_type(s)
        print(s, f"{t['class']} / {t['subclass']}")
