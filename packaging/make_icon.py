from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


def create_icon(output: Path) -> None:
    size = 256
    image = Image.new("RGBA", (size, size), "#151515")
    draw = ImageDraw.Draw(image)
    outer = [(128, 30), (213, 79), (213, 177), (128, 226), (43, 177), (43, 79)]
    inner = [(128, 78), (171, 103), (171, 153), (128, 178), (85, 153), (85, 103)]
    draw.line(outer + [outer[0]], fill="#d7ff64", width=14, joint="curve")
    draw.line(inner + [inner[0]], fill="#d7ff64", width=12, joint="curve")
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    create_icon(args.output)


if __name__ == "__main__":
    main()
