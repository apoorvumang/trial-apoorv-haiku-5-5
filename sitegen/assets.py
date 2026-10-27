"""Image assets for generated sites.

Primary: photos from an image model (MAI-Image 2.6 Flash via OpenRouter; it is available under our zero-data-
retention policy, ~$0.02 and ~12s per image). Fallback: procedural abstract art, deterministic from (seed, name).
Either way the agent receives the exact files, so images never have to be reproduced from pixels.
"""
import asyncio
import base64
import hashlib
import io
import os
import random

import numpy as np
from openai import AsyncOpenAI
from PIL import Image, ImageDraw, ImageFilter

IMAGE_MODEL = "microsoft/mai-image-2.6-flash"
ASPECTS = {"1:1": 1.0, "4:3": 4 / 3, "3:4": 3 / 4, "3:2": 1.5, "2:3": 2 / 3, "16:9": 16 / 9, "9:16": 9 / 16,
           "4:5": 0.8, "5:4": 1.25, "21:9": 21 / 9}
MAX_W, MAX_H = 1600, 1200

_client: AsyncOpenAI | None = None
_sem = asyncio.Semaphore(6)


def clamp_size(width, height) -> tuple[int, int]:
    w, h = max(64, int(width)), max(64, int(height))
    s = min(1.0, MAX_W / w, MAX_H / h)
    return round(w * s), round(h * s)


def _fit(img: Image.Image, width: int, height: int) -> Image.Image:
    """Centre-crop to the target aspect ratio, then resize to exactly width x height."""
    target, cur = width / height, img.width / img.height
    if cur > target:
        nw = round(img.height * target)
        img = img.crop(((img.width - nw) // 2, 0, (img.width - nw) // 2 + nw, img.height))
    else:
        nh = round(img.width / target)
        img = img.crop((0, (img.height - nh) // 2, img.width, (img.height - nh) // 2 + nh))
    return img.convert("RGB").resize((width, height), Image.Resampling.LANCZOS)


async def generate_photo(path, prompt: str, width: int, height: int) -> dict:
    global _client
    if _client is None:
        _client = AsyncOpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.environ["OPENROUTER_API_KEY"],
                              max_retries=3, timeout=300)
    width, height = clamp_size(width, height)
    aspect = min(ASPECTS, key=lambda k: abs(np.log(ASPECTS[k] / (width / height))))
    async with _sem:
        r = await _client.chat.completions.create(
            model=IMAGE_MODEL, messages=[{"role": "user", "content": prompt}],
            extra_body={"modalities": ["image"], "image_config": {"aspect_ratio": aspect}})
    images = (r.choices[0].message.model_extra or {}).get("images") or []
    if not images:
        raise RuntimeError("image model returned no image")
    raw = base64.b64decode(images[0]["image_url"]["url"].split(",", 1)[1])
    _fit(Image.open(io.BytesIO(raw)), width, height).save(path, "JPEG", quality=88)
    cost = (r.usage.model_extra or {}).get("cost") if r.usage else None
    return {"model": IMAGE_MODEL, "aspect": aspect, "size": [width, height], "cost": cost}


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def make_image(path, width: int, height: int, palette: list[str], seed: int, name: str) -> None:
    """Fallback: abstract blurred shapes in the site palette with film grain."""
    h = int(hashlib.sha256(f"{seed}:{name}".encode()).hexdigest()[:12], 16)
    rng = random.Random(h)
    width, height = clamp_size(width, height)
    cols = [_hex(c) for c in palette] or [(200, 200, 200), (60, 60, 60)]
    rng.shuffle(cols)

    a, b = np.array(cols[0], float), np.array(cols[1 % len(cols)], float)
    yy, xx = np.mgrid[0:height, 0:width]
    ang = rng.uniform(0, np.pi)
    t = (xx * np.cos(ang) + yy * np.sin(ang))
    t = (t - t.min()) / max(1e-6, t.max() - t.min())
    img = Image.fromarray((a * (1 - t[..., None]) + b * t[..., None]).astype(np.uint8))

    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(rng.randint(4, 9)):
        c = rng.choice(cols)
        r = rng.uniform(0.1, 0.45) * max(width, height)
        cx, cy = rng.uniform(0, width), rng.uniform(0, height)
        if rng.random() < 0.7:
            d.ellipse([cx - r, cy - r * rng.uniform(0.5, 1), cx + r, cy + r], fill=(*c, rng.randint(90, 200)))
        else:
            d.rectangle([cx - r, cy - r / 4, cx + r, cy + r / 4], fill=(*c, rng.randint(70, 160)))
    layer = layer.filter(ImageFilter.GaussianBlur(radius=max(width, height) * rng.uniform(0.02, 0.06)))
    img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")

    noise = np.random.default_rng(h).normal(0, 6, (height, width, 1))
    arr = np.clip(np.asarray(img, float) + noise, 0, 255).astype(np.uint8)
    Image.fromarray(arr).save(path, "JPEG", quality=88)
