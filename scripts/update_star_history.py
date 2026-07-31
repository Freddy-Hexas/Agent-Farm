from __future__ import annotations

import argparse
import json
import math
import os
import re
from datetime import date
from html import escape
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


DEFAULT_DATA_PATH = Path("docs/star-history.json")
DEFAULT_SVG_PATH = Path("docs/star-history.svg")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def fetch_star_count(
    repository: str,
    *,
    token: str | None = None,
    api_url: str = "https://api.github.com",
) -> int:
    if not REPOSITORY_PATTERN.fullmatch(repository):
        raise ValueError("Repository must use the owner/name form.")

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Agent-Farm-Star-History",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = Request(
        f"{api_url.rstrip('/')}/repos/{repository}",
        headers=headers,
        method="GET",
    )
    with urlopen(request, timeout=30) as response:
        payload = json.load(response)

    stars = payload.get("stargazers_count")
    if not isinstance(stars, int) or stars < 0:
        raise ValueError("GitHub returned an invalid stargazers_count value.")
    return stars


def load_history(path: Path, repository: str, sample_date: str) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": 1,
            "repository": repository,
            "tracking_started": sample_date,
            "samples": [],
        }

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported star-history schema version.")
    if payload.get("repository") != repository:
        raise ValueError("Star-history repository does not match the requested repository.")
    if not isinstance(payload.get("samples"), list):
        raise ValueError("Star-history samples must be a list.")
    return payload


def record_sample(history: dict[str, Any], sample_date: str, stars: int) -> None:
    date.fromisoformat(sample_date)
    if not isinstance(stars, int) or stars < 0:
        raise ValueError("Star count must be a non-negative integer.")

    samples_by_date: dict[str, int] = {}
    for item in history.get("samples", []):
        item_date = item.get("date")
        item_stars = item.get("stars")
        if not isinstance(item_date, str) or not isinstance(item_stars, int):
            raise ValueError("Each star-history sample requires a date and integer stars value.")
        date.fromisoformat(item_date)
        samples_by_date[item_date] = item_stars

    samples_by_date[sample_date] = stars
    history["samples"] = [
        {"date": item_date, "stars": samples_by_date[item_date]}
        for item_date in sorted(samples_by_date)
    ]


def _nice_axis_max(value: int) -> int:
    if value <= 4:
        return 4
    raw_step = value / 4
    magnitude = 10 ** math.floor(math.log10(raw_step))
    normalized = raw_step / magnitude
    if normalized <= 1:
        step = magnitude
    elif normalized <= 2:
        step = 2 * magnitude
    elif normalized <= 5:
        step = 5 * magnitude
    else:
        step = 10 * magnitude
    return int(math.ceil(value / step) * step)


def render_svg(history: dict[str, Any]) -> str:
    repository = str(history["repository"])
    samples = history.get("samples", [])
    if not samples:
        raise ValueError("At least one star-history sample is required to render the chart.")

    parsed = [
        (date.fromisoformat(str(item["date"])), int(item["stars"]))
        for item in samples
    ]
    parsed.sort(key=lambda item: item[0])

    width, height = 960, 480
    left, right, top, bottom = 76, 42, 104, 74
    plot_width = width - left - right
    plot_height = height - top - bottom
    first_date, last_date = parsed[0][0], parsed[-1][0]
    day_span = max(1, (last_date - first_date).days)
    axis_max = _nice_axis_max(max(stars for _, stars in parsed))

    def x_position(item_date: date) -> float:
        if len(parsed) == 1:
            return left + plot_width / 2
        return left + ((item_date - first_date).days / day_span) * plot_width

    def y_position(stars: int) -> float:
        return top + plot_height - (stars / axis_max) * plot_height

    points = [(x_position(item_date), y_position(stars)) for item_date, stars in parsed]
    line_path = " ".join(
        f"{'M' if index == 0 else 'L'} {x:.2f} {y:.2f}"
        for index, (x, y) in enumerate(points)
    )
    area_path = ""
    if len(points) > 1:
        area_path = (
            f"M {points[0][0]:.2f} {top + plot_height:.2f} "
            + " ".join(f"L {x:.2f} {y:.2f}" for x, y in points)
            + f" L {points[-1][0]:.2f} {top + plot_height:.2f} Z"
        )

    grid_lines: list[str] = []
    for index in range(5):
        stars = round(axis_max * index / 4)
        y = y_position(stars)
        grid_lines.append(
            f'<line x1="{left}" y1="{y:.2f}" x2="{left + plot_width}" '
            f'y2="{y:.2f}" class="grid" />'
        )
        grid_lines.append(
            f'<text x="{left - 14}" y="{y + 5:.2f}" class="axis" '
            f'text-anchor="end">{stars}</text>'
        )

    date_labels = [
        f'<text x="{left}" y="{top + plot_height + 34}" class="axis" '
        f'text-anchor="start">{first_date.isoformat()}</text>'
    ]
    if last_date != first_date:
        date_labels.append(
            f'<text x="{left + plot_width}" y="{top + plot_height + 34}" class="axis" '
            f'text-anchor="end">{last_date.isoformat()}</text>'
        )

    latest_stars = parsed[-1][1]
    latest_x, latest_y = points[-1]
    area_markup = (
        f'<path d="{area_path}" fill="url(#area)" />'
        if area_path
        else "<!-- Area fill appears after the second daily sample. -->"
    )
    repository_label = escape(repository)
    tracked_since = escape(str(history["tracking_started"]))

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description">
  <title id="title">{repository_label} GitHub star history</title>
  <desc id="description">Daily aggregate GitHub star counts tracked since {tracked_since}. The latest count is {latest_stars}.</desc>
  <defs>
    <linearGradient id="background" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0d1117" />
      <stop offset="100%" stop-color="#161b22" />
    </linearGradient>
    <linearGradient id="area" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#58a6ff" stop-opacity="0.34" />
      <stop offset="100%" stop-color="#58a6ff" stop-opacity="0.02" />
    </linearGradient>
    <filter id="glow" x="-30%" y="-30%" width="160%" height="160%">
      <feGaussianBlur stdDeviation="4" result="blur" />
      <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
    </filter>
  </defs>
  <style>
    text {{ font-family: Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }}
    .title {{ fill: #f0f6fc; font-size: 24px; font-weight: 700; }}
    .subtitle {{ fill: #8b949e; font-size: 14px; }}
    .count {{ fill: #f0f6fc; font-size: 31px; font-weight: 750; }}
    .count-label {{ fill: #8b949e; font-size: 12px; letter-spacing: 0.08em; }}
    .axis {{ fill: #8b949e; font-size: 12px; }}
    .grid {{ stroke: #30363d; stroke-width: 1; }}
  </style>
  <rect x="1" y="1" width="958" height="478" rx="16" fill="url(#background)" stroke="#30363d" />
  <text x="{left}" y="45" class="title">GitHub Star History</text>
  <text x="{left}" y="70" class="subtitle">{repository_label} · tracked daily from {tracked_since}</text>
  <text x="{width - right}" y="43" class="count" text-anchor="end">★ {latest_stars}</text>
  <text x="{width - right}" y="65" class="count-label" text-anchor="end">CURRENT STARS</text>
  {''.join(grid_lines)}
  {area_markup}
  <path d="{line_path}" fill="none" stroke="#58a6ff" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round" filter="url(#glow)" />
  <circle cx="{latest_x:.2f}" cy="{latest_y:.2f}" r="5" fill="#0d1117" stroke="#79c0ff" stroke-width="3" />
  {''.join(date_labels)}
  <text x="{width / 2}" y="{height - 24}" class="subtitle" text-anchor="middle">Daily aggregate counts · no Stargazer identities are collected</text>
</svg>
'''


def write_history(path: Path, history: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(history, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_svg(path: Path, svg: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8", newline="\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record and render aggregate GitHub star history.")
    parser.add_argument(
        "--repository",
        default=os.environ.get("GITHUB_REPOSITORY", "Freddy-Hexas/Agent-Farm"),
        help="GitHub repository in owner/name form.",
    )
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_SVG_PATH)
    parser.add_argument("--date", dest="sample_date", default=date.today().isoformat())
    parser.add_argument("--stars", type=int, help="Use an explicit count instead of GitHub API.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sample_date = date.fromisoformat(args.sample_date).isoformat()
    stars = args.stars
    if stars is None:
        stars = fetch_star_count(
            args.repository,
            token=os.environ.get("GITHUB_TOKEN"),
            api_url=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
        )

    history = load_history(args.data, args.repository, sample_date)
    record_sample(history, sample_date, stars)
    write_history(args.data, history)
    write_svg(args.output, render_svg(history))
    print(f"Recorded {stars} stars for {args.repository} on {sample_date}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
