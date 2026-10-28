"""Site-type taxonomy (class -> subclass) and the only random sampling in the pipeline.

v1 classifies by organisation type (taxonomy.json); v2 by site genre, i.e. what kind of interface the site is
(taxonomy_v2.json; the sources behind it are summarised in out/ontology_research.json).
"""
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
TAXONOMIES = {"v1": json.loads((HERE / "taxonomy.json").read_text()),
              "v2": json.loads((HERE / "taxonomy_v2.json").read_text())}
TAXONOMY = TAXONOMIES["v1"]


def sample_site_type(seed: int, version: str = "v1") -> dict:
    """Class uniformly, then subclass uniformly within it, so big classes don't swamp the long tail.

    v1 draws each seed's class independently. v2 draws classes without replacement: seeds are cut into blocks of
    len(classes) consecutive seeds, and each block is one shuffled pass over all classes, so any run of consecutive
    seeds within a block never repeats a class."""
    classes = TAXONOMIES[version]["classes"]
    rng = random.Random(f"site_type:{seed}")
    if version == "v1":
        cls = rng.choice(classes)
    else:
        order = list(range(len(classes)))
        random.Random(f"classes:{version}:{seed // len(classes)}").shuffle(order)
        cls = classes[order[seed % len(classes)]]
    sub = rng.choice(cls["subclasses"])
    out = {"seed": seed, "taxonomy": version, "class_id": cls["id"], "class": cls["name"],
           "subclass_id": sub["id"], "subclass": sub["name"]}
    if sub.get("description"):
        out["subclass_description"] = sub["description"]
    return out


if __name__ == "__main__":
    import sys
    version = sys.argv[2] if len(sys.argv) > 2 else "v1"
    for s in range(int(sys.argv[1]) if len(sys.argv) > 1 else 10):
        t = sample_site_type(s, version)
        print(s, f"{t['class']} / {t['subclass']}")
