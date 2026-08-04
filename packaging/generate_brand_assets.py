from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

from make_icon import ICO_SIZES, load_logo, render_logo, save_multi_resolution_ico


PNG_SIZES = (16, 20, 24, 32, 40, 44, 48, 64, 72, 88, 96, 128, 150, 256, 300, 512, 720, 1024)


def centered_canvas(
    canvas_size: tuple[int, int],
    logo_size: int,
    alpha: Image.Image,
    brand_color: tuple[int, int, int],
) -> Image.Image:
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    logo = render_logo(alpha, brand_color, logo_size)
    canvas.alpha_composite(
        logo,
        ((canvas_size[0] - logo_size) // 2, (canvas_size[1] - logo_size) // 2),
    )
    return canvas


def generate(source: Path, output_root: Path, desktop_assets: Path) -> None:
    alpha, brand_color = load_logo(source)
    png_root = output_root / "png"
    png_root.mkdir(parents=True, exist_ok=True)
    desktop_assets.mkdir(parents=True, exist_ok=True)

    for size in PNG_SIZES:
        render_logo(alpha, brand_color, size).save(
            png_root / f"agent-farm-{size}x{size}.png",
            format="PNG",
            optimize=True,
        )

    render_logo(alpha, brand_color, 1024, enhance_small=False).save(
        output_root / "agent-farm-master-transparent.png",
        format="PNG",
        optimize=True,
    )
    save_multi_resolution_ico(output_root / "agent-farm.ico", alpha, brand_color, ICO_SIZES)
    save_multi_resolution_ico(desktop_assets / "AppIcon.ico", alpha, brand_color, ICO_SIZES)

    square_assets = {
        "LockScreenLogo.scale-200.png": 48,
        "Square150x150Logo.scale-200.png": 300,
        "Square44x44Logo.scale-200.png": 88,
        "Square44x44Logo.targetsize-24_altform-unplated.png": 24,
        "Square44x44Logo.targetsize-48_altform-lightunplated.png": 48,
        "StoreLogo.png": 50,
    }
    for name, size in square_assets.items():
        render_logo(alpha, brand_color, size).save(
            desktop_assets / name,
            format="PNG",
            optimize=True,
        )

    centered_canvas((620, 300), 240, alpha, brand_color).save(
        desktop_assets / "Wide310x150Logo.scale-200.png",
        format="PNG",
        optimize=True,
    )
    centered_canvas((1240, 600), 256, alpha, brand_color).save(
        desktop_assets / "SplashScreen.scale-200.png",
        format="PNG",
        optimize=True,
    )


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=repo_root
        / "branding"
        / "agent-farm-logo"
        / "agent-farm-source-4096.png",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=repo_root / "branding" / "agent-farm-logo",
    )
    parser.add_argument(
        "--desktop-assets",
        type=Path,
        default=repo_root / "AgentFarm.Desktop" / "Assets",
    )
    args = parser.parse_args()
    generate(args.source, args.output_root, args.desktop_assets)


if __name__ == "__main__":
    main()
