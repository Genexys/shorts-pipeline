"""Builds a thumbnail from a finished video.

YouTube otherwise picks a frame at random, which for a long video is the single
biggest lever on whether anyone clicks it at all.
"""

import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from logstream import log
from utils import FONTS_DIR

THUMBNAIL_WIDTH = 1280
THUMBNAIL_HEIGHT = 720

# Frames are sampled across the middle of the video: the opening is often a
# fade and the closing frame is often the least representative.
FRAME_SAMPLE_POINTS = (0.12, 0.28, 0.44, 0.60, 0.76)

TEXT_MAX_CHARS_PER_LINE = 18
TEXT_MAX_LINES = 3
TEXT_FILL = "#FFFFFF"
TEXT_STROKE = "#000000"
TEXT_STROKE_WIDTH = 12
SCRIM_OPACITY = 110


def wrap_title(
    title: str,
    max_chars: int = TEXT_MAX_CHARS_PER_LINE,
    max_lines: int = TEXT_MAX_LINES,
) -> List[str]:
    """Breaks a title into short upper-case lines for a thumbnail.

    Thumbnails are read at a glance and often at a couple of centimetres wide,
    so a full sentence is worse than a fragment. Words longer than the line
    budget get their own line rather than being split.
    """
    words = (title or "").upper().split()
    lines: List[str] = []
    current = ""

    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_chars:
            lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
        else:
            current = candidate

    if current and len(lines) < max_lines:
        lines.append(current)
    return lines[:max_lines]


def score_frame(image: Image.Image) -> float:
    """How usable a frame is as a thumbnail. Higher is better.

    Rewards contrast and rejects frames that are nearly black or blown out —
    both common in stock footage and both useless behind text.
    """
    grey = image.convert("L").resize((64, 36))
    pixels = list(grey.getdata())
    mean = sum(pixels) / len(pixels)
    variance = sum((value - mean) ** 2 for value in pixels) / len(pixels)

    if mean < 25 or mean > 235:
        return 0.0
    # Mid-brightness frames take text best; contrast keeps them from looking flat.
    brightness_fit = 1.0 - abs(mean - 120) / 120
    return variance ** 0.5 * max(brightness_fit, 0.1)


def extract_candidates(
    video_path: str, duration: float, out_dir: Path, ffmpeg: str = "ffmpeg"
) -> List[Path]:
    """Pulls one frame per sample point."""
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: List[Path] = []
    for index, fraction in enumerate(FRAME_SAMPLE_POINTS):
        target = out_dir / f"candidate_{index}.png"
        try:
            subprocess.run(
                [
                    ffmpeg, "-v", "error", "-y",
                    "-ss", f"{duration * fraction:.3f}",
                    "-i", video_path,
                    "-frames:v", "1", str(target),
                ],
                check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError:
            continue
        if target.exists():
            frames.append(target)
    return frames


def pick_best_frame(frames: List[Path]) -> Optional[Path]:
    """The highest scoring candidate, or None when there is nothing usable."""
    scored: List[Tuple[float, Path]] = []
    for frame in frames:
        try:
            with Image.open(frame) as image:
                scored.append((score_frame(image), frame))
        except OSError:
            continue
    if not scored:
        return None
    best_score, best_frame = max(scored, key=lambda pair: pair[0])
    return best_frame if best_score > 0 else scored[0][1]


def compose(frame_path: Path, lines: List[str], output_path: str) -> str:
    """Crops the frame to 16:9, darkens it, and lays the title over it."""
    with Image.open(frame_path) as source:
        image = source.convert("RGB")

    target_ratio = THUMBNAIL_WIDTH / THUMBNAIL_HEIGHT
    width, height = image.size
    if width / height > target_ratio:
        new_width = int(height * target_ratio)
        left = (width - new_width) // 2
        image = image.crop((left, 0, left + new_width, height))
    else:
        new_height = int(width / target_ratio)
        top = (height - new_height) // 2
        image = image.crop((0, top, width, top + new_height))
    image = image.resize((THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), Image.LANCZOS)

    # Text over raw footage is unreadable often enough that a scrim is cheaper
    # than getting it wrong.
    scrim = Image.new("RGBA", image.size, (0, 0, 0, SCRIM_OPACITY))
    image = Image.alpha_composite(image.convert("RGBA"), scrim).convert("RGB")

    if lines:
        draw = ImageDraw.Draw(image)
        font_size = 132 if len(lines) < 3 else 108
        font = ImageFont.truetype(str(FONTS_DIR / "bold_font.ttf"), font_size)
        spacing = int(font_size * 0.12)
        heights = [
            draw.textbbox((0, 0), line, font=font, stroke_width=TEXT_STROKE_WIDTH)[3]
            for line in lines
        ]
        block = sum(heights) + spacing * (len(lines) - 1)
        y = (THUMBNAIL_HEIGHT - block) // 2
        for line, line_height in zip(lines, heights):
            box = draw.textbbox((0, 0), line, font=font, stroke_width=TEXT_STROKE_WIDTH)
            draw.text(
                ((THUMBNAIL_WIDTH - box[2]) // 2, y),
                line,
                font=font,
                fill=TEXT_FILL,
                stroke_width=TEXT_STROKE_WIDTH,
                stroke_fill=TEXT_STROKE,
            )
            y += line_height + spacing

    image.save(output_path, "JPEG", quality=88)
    return output_path


def build_thumbnail(
    video_path: str,
    title: str,
    output_path: str,
    duration: float,
    work_dir: Path,
    ffmpeg: str = "ffmpeg",
) -> Optional[str]:
    """Picks a frame and writes a thumbnail. None when no frame could be read."""
    frames = extract_candidates(video_path, duration, work_dir, ffmpeg)
    best = pick_best_frame(frames)
    if best is None:
        log("[!] No usable frame for a thumbnail.", "warning")
        return None
    compose(best, wrap_title(title), output_path)
    log(f"[+] Thumbnail written to {output_path}", "success")
    return output_path
