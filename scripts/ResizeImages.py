# -*- coding: utf-8 -*-
"""
Script to save the images in the right size for the Roku app
"""
# pip install pillow
import os
from PIL import Image, ImageOps

INPUT_DIR = "/path/to/Videos/images"     # change this
OUTPUT_DIR = os.path.join(INPUT_DIR, "resized")  # subdirectory for results
TARGET_W, TARGET_H = 440, 350

os.makedirs(OUTPUT_DIR, exist_ok=True)

def resize_cover_center_crop(img: Image.Image, tw: int, th: int) -> Image.Image:
    # Respect EXIF orientation
    img = ImageOps.exif_transpose(img)
    w, h = img.size

    # Scale to cover the target (no letterboxing), maintain aspect ratio
    scale = max(tw / w, th / h)
    new_w, new_h = int(round(w * scale)), int(round(h * scale))
    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    # Center crop to exact target size
    left   = (new_w - tw) // 2
    top    = (new_h - th) // 2
    right  = left + tw
    bottom = top + th
    return img.crop((left, top, right, bottom))

# Process images
for name in os.listdir(INPUT_DIR):
    src = os.path.join(INPUT_DIR, name)
    if not os.path.isfile(src):
        continue

    # Only handle common image types
    lower = name.lower()
    if not lower.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff")):
        continue

    try:
        with Image.open(src) as im:
            out_im = resize_cover_center_crop(im, TARGET_W, TARGET_H)

            # Save as PNG (or keep original extension if you prefer)
            base, _ = os.path.splitext(name)
            dst = os.path.join(OUTPUT_DIR, f"{base}.png")
            # Preserve transparency if present; otherwise save as RGB
            if out_im.mode not in ("RGB", "RGBA"):
                out_im = out_im.convert("RGBA" if "A" in out_im.getbands() else "RGB")
            out_im.save(dst, format="PNG", optimize=True)
            print(f"Saved: {dst}")
    except Exception as e:
        print(f"Skipping {name}: {e}")

