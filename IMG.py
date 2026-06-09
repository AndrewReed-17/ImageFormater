#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Optional
import hashlib

from PIL import Image, ImageOps, ExifTags

# Pillow 9+ compatibility
try:
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:  # pragma: no cover
    RESAMPLE = Image.LANCZOS

SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".heic"
}

EXIF_TAGS = {v: k for k, v in ExifTags.TAGS.items()}
TAG_DATETIME_ORIGINAL = EXIF_TAGS.get("DateTimeOriginal")
TAG_DATETIME = EXIF_TAGS.get("DateTime")
TAG_MODEL = EXIF_TAGS.get("Model")
TAG_MAKE = EXIF_TAGS.get("Make")


def sanitize(text: str) -> str:
    text = text.strip()
    text = re.sub(r"[^\w\-\.]+", "_", text, flags=re.UNICODE)
    text = re.sub(r"_+", "_", text)
    return text.strip("._-")


def shorten(text: str, max_len: int = 64) -> str:
    text = sanitize(text)
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip("._-")


def get_exif_text(img: Image.Image) -> dict[str, str]:
    out: dict[str, str] = {}

    try:
        exif = img.getexif()
    except Exception:
        exif = None

    if not exif:
        return out

    def read(tag_id: Optional[int]) -> str:
        if tag_id is None:
            return ""
        try:
            value = exif.get(tag_id, "")
            return str(value).strip()
        except Exception:
            return ""

    dt = read(TAG_DATETIME_ORIGINAL) or read(TAG_DATETIME)
    make = read(TAG_MAKE)
    model = read(TAG_MODEL)

    if dt:
        # 2026:06:08 12:34:56 -> 20260608_123456
        out["datetime"] = sanitize(dt.replace(":", "", 2).replace(":", "").replace(" ", "_"))
    if make:
        out["make"] = shorten(make, 24)
    if model:
        out["model"] = shorten(model, 32)

    return out


def build_output_name(src_path: Path, img: Image.Image, use_metadata_name: bool) -> str:
    stem = sanitize(src_path.stem) or "image"

    if not use_metadata_name:
        return stem + ".jpg"

    meta = get_exif_text(img)
    parts = []

    if "datetime" in meta:
        parts.append(meta["datetime"])
    if "make" in meta:
        parts.append(meta["make"])
    if "model" in meta:
        parts.append(meta["model"])

    parts.append(stem)

    name = "_".join([p for p in parts if p])
    name = sanitize(name) or "image"
    return name + ".jpg"

def md5sum(filepath):
    h = hashlib.md5()

    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)

    return h.hexdigest()

def process_image(
    src_path: Path,
    dst_path: Path,
    quality: int,
    max_side: int,
) -> None:
    with Image.open(src_path) as img:
        img = ImageOps.exif_transpose(img)

        # Convert to grayscale
        if img.mode != "L":
            img = img.convert("L")

        # Resize so the longest side is max_side, preserving ratio
        img.thumbnail((max_side, max_side), RESAMPLE)

        # JPEG export strips metadata if we do not pass EXIF/XMP chunks
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(
            dst_path,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
        )

        #Computing and writting the checksum file.
        checksum = md5sum(dst_path)

        with open(f"{str(dst_path).rstrip(dst_path.name)}.{dst_path.name}.md5", "w") as f:
            f.write(f"{checksum} | {dst_path.name}\n")
        f.close()


def iter_images(src_root: Path):
    for path in src_root.rglob("*"):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            yield path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convertit un dossier d'images en JPEG grayscale 720px max, qualité 60, sans métadonnées."
    )
    parser.add_argument("src", type=Path, help="Dossier source")
    parser.add_argument("dst", type=Path, help="Dossier de sortie")
    parser.add_argument("--quality", type=int, default=60, help="Qualité JPEG (défaut: 60)")
    parser.add_argument("--max-side", type=int, default=720, help="Taille maximale du grand côté (défaut: 720)")
    parser.add_argument(
        "--use-metadata-name",
        action="store_true",
        help="Renomme les fichiers exportés avec des métadonnées EXIF si disponibles",
    )
    parser.add_argument(
        "--flatten",
        action="store_true",
        help="N'applique pas la structure des sous-dossiers dans la sortie",
    )

    args = parser.parse_args()

    src_root: Path = args.src.resolve()
    dst_root: Path = args.dst.resolve()

    if not src_root.exists() or not src_root.is_dir():
        raise SystemExit(f"Dossier source invalide: {src_root}")

    dst_root.mkdir(parents=True, exist_ok=True)

    count_ok = 0
    count_fail = 0

    for src_path in iter_images(src_root):
        try:
            rel_dir = Path("") if args.flatten else src_path.parent.relative_to(src_root)

            with Image.open(src_path) as img_probe:
                out_name = build_output_name(src_path, img_probe, args.use_metadata_name)

            dst_path = dst_root / rel_dir / out_name

            # Collision avoidance
            if dst_path.exists():
                base = dst_path.stem
                suffix = dst_path.suffix
                n = 1
                while True:
                    candidate = dst_path.with_name(f"{base}_{n}{suffix}")
                    if not candidate.exists():
                        dst_path = candidate
                        break
                    n += 1

            process_image(
                src_path=src_path,
                dst_path=dst_path,
                quality=args.quality,
                max_side=args.max_side,
            )
            count_ok += 1
            print(f"[OK] {src_path} -> {dst_path}")
        except Exception as e:
            count_fail += 1
            print(f"[FAIL] {src_path} :: {e}")

    print(f"\nTerminé. Succès: {count_ok} | Échecs: {count_fail}")
    return 0 if count_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
