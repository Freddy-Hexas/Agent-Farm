from __future__ import annotations

import argparse
import io
import struct
from collections import Counter
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter


ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def load_logo(source: Path) -> tuple[Image.Image, tuple[int, int, int]]:
    """Convert the vector-export canvas to clean alpha with stable brand color."""
    image = Image.open(source).convert("RGBA")
    red, green, blue, source_alpha = image.split()
    minimum_rgb = ImageChops.darker(ImageChops.darker(red, green), blue)
    foreground_alpha = ImageChops.invert(minimum_rgb)
    foreground_alpha = ImageChops.multiply(source_alpha, foreground_alpha).point(
        lambda value: 0 if value <= 8 else 255 if value >= 247 else value
    )

    opaque_colors: Counter[tuple[int, int, int]] = Counter()
    for count, (r, g, b, a) in image.getcolors(maxcolors=image.width * image.height) or []:
        if a > 0 and min(r, g, b) < 245 and max(r, g, b) - min(r, g, b) > 30:
            opaque_colors[(r, g, b)] += count
    if not opaque_colors:
        raise ValueError(f"No colored logo pixels were found in {source}")

    brand_color = opaque_colors.most_common(1)[0][0]
    return foreground_alpha, brand_color


def render_logo(
    alpha: Image.Image,
    brand_color: tuple[int, int, int],
    size: int,
    *,
    enhance_small: bool = True,
) -> Image.Image:
    if size <= 0:
        raise ValueError("Logo size must be positive")

    if enhance_small and size <= 48:
        working_size = size * 4
        resized_alpha = alpha.resize((working_size, working_size), Image.Resampling.LANCZOS)
        # The terminal rings and connector strokes become sub-pixel at Windows
        # title-bar sizes. Slightly expand them before the final downsample, then
        # compress the antialiasing ramp so the mark stays crisp instead of gray.
        resized_alpha = resized_alpha.filter(ImageFilter.MaxFilter(7 if size <= 24 else 5))
        resized_alpha = resized_alpha.resize((size, size), Image.Resampling.LANCZOS)
        resized_alpha = resized_alpha.point(
            lambda value: 0
            if value <= 12
            else 255
            if value >= 220
            else round((((value - 12) / 208) ** 0.72) * 255)
        )
    else:
        resized_alpha = alpha.resize((size, size), Image.Resampling.LANCZOS)

    solid = Image.new("RGBA", (size, size), (*brand_color, 255))
    solid.putalpha(resized_alpha)
    return solid


def save_multi_resolution_ico(
    output: Path,
    alpha: Image.Image,
    brand_color: tuple[int, int, int],
    sizes: tuple[int, ...] = ICO_SIZES,
) -> None:
    """Write PNG-backed ICO frames so every small frame uses tuned rendering."""
    frames: list[tuple[int, bytes]] = []
    for size in sizes:
        buffer = io.BytesIO()
        render_logo(alpha, brand_color, size).save(buffer, format="PNG", optimize=True)
        frames.append((size, buffer.getvalue()))

    header_size = 6 + 16 * len(frames)
    offset = header_size
    directory = bytearray(struct.pack("<HHH", 0, 1, len(frames)))
    payload = bytearray()
    for size, png in frames:
        dimension = 0 if size == 256 else size
        directory.extend(
            struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(png), offset)
        )
        payload.extend(png)
        offset += len(png)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(directory + payload)


def create_icon(output: Path, source: Path | None = None) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    source = source or repo_root / "branding" / "agent-farm-logo" / "agent-farm-source-4096.png"
    alpha, brand_color = load_logo(source)
    save_multi_resolution_ico(output, alpha, brand_color)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    create_icon(args.output, args.source)


if __name__ == "__main__":
    main()
