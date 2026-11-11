"""Build the always-on-top desktop-pet assets from the mini avatar artwork.

The source artwork is a full illustrated scene, not a cutout, so the background
cannot be chroma-keyed away.  Instead the character's face is located, a crisp
circular cameo is framed around her, and the result is rendered as a badge with
a soft outer ring.

Everything is drawn on a flat transparency-key colour so the Tk window can use
``-transparentcolor`` for a truly see-through, always-on-top pet window.  Only
hard edges are used, which keeps the key clean and avoids colour fringing.

The build is cached on disk and keyed by ``BUILD_VERSION`` so changing the
artwork pipeline regenerates the frames automatically.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

DISPLAY_SIZE = 168
PROBE_SIZE = 220
TOLERANCE = 74
MIN_CUTOUT_COVERAGE = 0.05
MAX_CUTOUT_COVERAGE = 0.94
BUILD_VERSION = 5

# Pure, unlikely-to-appear colours used as the Windows transparency key.
KEY_CANDIDATES = [
    (255, 0, 255),
    (0, 255, 0),
    (0, 255, 255),
    (255, 255, 0),
    (0, 0, 255),
]

RING_COLOUR = (255, 252, 253)
ACCENT_COLOUR = (196, 58, 116)

BUBBLE_FILL = (255, 252, 253)
BUBBLE_EDGE = (196, 58, 116)
BUBBLE_TEXT = (93, 34, 64)

# (angle, vertical bob) per frame for the two animation sets.
IDLE_FRAMES = [
    (0.0, 0), (-1.2, -1), (-2.4, -3), (-1.6, -4),
    (0.8, -3), (2.0, -1), (1.2, 0), (-0.8, 1),
    (-1.8, 2), (-0.6, 1), (0.6, 0), (0.0, 0),
]
WORK_FRAMES = [
    (-4.0, 0), (-6.0, -2), (-4.5, -4), (-2.0, -2),
    (1.0, 1), (4.0, 3), (6.0, 1), (3.0, -2),
    (-1.0, -4), (-4.0, -2), (-2.0, 0), (0.0, 0),
]


def _harden(mask: Image.Image, threshold: int = 128) -> Image.Image:
    return mask.point(lambda value: 255 if value >= threshold else 0)


def _channel_distance(rgb: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    """Per-pixel maximum absolute channel difference against a flat colour."""
    r, g, b = ImageChops.difference(rgb, Image.new("RGB", rgb.size, color)).split()
    return ImageChops.lighter(ImageChops.lighter(r, g), b)


def _border_samples(rgb: Image.Image, count: int = 48) -> list[tuple[int, int, int]]:
    width, height = rgb.size
    samples: list[tuple[int, int, int]] = []
    for index in range(count):
        offset = int(index * (max(width, height) - 1) / max(1, count - 1))
        x = min(offset, width - 1)
        y = min(offset, height - 1)
        samples.append(rgb.getpixel((x, 0)))
        samples.append(rgb.getpixel((x, height - 1)))
        samples.append(rgb.getpixel((0, y)))
        samples.append(rgb.getpixel((width - 1, y)))
    return list(dict.fromkeys(samples))


def _background_like(rgb: Image.Image, samples: list[tuple[int, int, int]]) -> Image.Image:
    """Binary mask (255 = background-like) of pixels close to any border colour."""
    combined: Image.Image | None = None
    for color in samples:
        distance = _channel_distance(rgb, color)
        near = distance.point(lambda value: 255 if value <= TOLERANCE else 0)
        combined = near if combined is None else ImageChops.lighter(combined, near)
    return combined if combined is not None else Image.new("L", rgb.size, 0)


def _components_touching_border(mask: Image.Image) -> Image.Image:
    """Keep only connected background regions that reach the image border."""
    width, height = mask.size
    pixels = mask.load()
    seen = bytearray(width * height)
    background = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque()

    for x in range(width):
        for y in (0, height - 1):
            if pixels[x, y] and not seen[y * width + x]:
                seen[y * width + x] = 1
                queue.append((x, y))
    for y in range(height):
        for x in (0, width - 1):
            if pixels[x, y] and not seen[y * width + x]:
                seen[y * width + x] = 1
                queue.append((x, y))

    while queue:
        x, y = queue.popleft()
        background[y * width + x] = 1
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < width and 0 <= ny < height:
                index = ny * width + nx
                if pixels[nx, ny] and not seen[index]:
                    seen[index] = 1
                    queue.append((nx, ny))

    return Image.frombytes("L", (width, height), bytes(background))


def _find_face(rgb: Image.Image) -> tuple[int, int, int] | None:
    """Locate the character's face as (centre_x, centre_y, radius) in pixels.

    Skin tone is a warm, moderately saturated and bright colour, and the face is
    the largest such region in the upper part of the frame.
    """
    small = rgb.convert("HSV").resize((320, 320), Image.LANCZOS)
    pixels = small.load()
    width, height = small.size
    mask = Image.new("L", small.size, 0)
    mask_pixels = mask.load()

    for y in range(int(height * 0.72)):
        for x in range(width):
            hue, saturation, value = pixels[x, y]
            warm = hue <= 46 or hue >= 234
            if warm and 25 <= saturation <= 130 and value >= 165:
                mask_pixels[x, y] = 255

    mask = mask.filter(ImageFilter.MedianFilter(3))
    mask = mask.filter(ImageFilter.MaxFilter(3))
    mask_values = mask.load()
    seen = bytearray(width * height)
    best: tuple[int, tuple[int, int, int, int]] | None = None

    for start_y in range(height):
        for start_x in range(width):
            index = start_y * width + start_x
            if not mask_values[start_x, start_y] or seen[index]:
                continue
            queue: deque[tuple[int, int]] = deque([(start_x, start_y)])
            seen[index] = 1
            count = 0
            sum_x = 0
            sum_y = 0
            min_x = min_y = 10**9
            max_x = max_y = -1
            while queue:
                x, y = queue.popleft()
                count += 1
                sum_x += x
                sum_y += y
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if 0 <= nx < width and 0 <= ny < height:
                        neighbour = ny * width + nx
                        if mask_values[nx, ny] and not seen[neighbour]:
                            seen[neighbour] = 1
                            queue.append((nx, ny))
            if count < 150:
                continue
            box = (min_x, min_y, max_x, max_y)
            if best is None or count > best[0]:
                best = (count, box)

    if best is None:
        return None

    min_x, min_y, max_x, max_y = best[1]
    scale = rgb.width / float(width)
    centre_x = (min_x + max_x) / 2.0 * scale
    centre_y = (min_y + max_y) / 2.0 * scale
    face_width = (max_x - min_x) * scale
    radius = int(face_width * 1.15)
    radius = max(radius, int(rgb.width * 0.17))
    radius = min(radius, int(rgb.width * 0.45))
    return int(centre_x), int(centre_y), radius


def _pick_key(sprite_rgb: Image.Image, sprite_alpha: Image.Image) -> tuple[int, int, int]:
    """Choose the transparency colour that appears least inside the artwork."""
    opaque = sprite_alpha.point(lambda value: 255 if value >= 128 else 0)
    pixels = sprite_rgb.load()
    alpha_pixels = opaque.load()
    width, height = sprite_rgb.size
    best = KEY_CANDIDATES[0]
    best_score = -1
    for candidate in KEY_CANDIDATES:
        hits = 0
        nearest = 255 * 3
        for y in range(0, height, 2):
            for x in range(0, width, 2):
                if not alpha_pixels[x, y]:
                    continue
                r, g, b = pixels[x, y]
                distance = max(abs(r - candidate[0]), abs(g - candidate[1]), abs(b - candidate[2]))
                if distance == 0:
                    hits += 1
                nearest = min(nearest, distance)
        score = nearest * 1000 - hits
        if score > best_score:
            best_score = score
            best = candidate
    return best


def _build_badge(source: Image.Image, size: int) -> tuple[Image.Image, Image.Image, str]:
    """Build the badge artwork and its hard alpha mask.

    Returns (rgb, alpha, mode).  ``mode`` is "cutout" when the artwork had a
    plain background that could be removed, otherwise "cameo".
    """
    rgb = source.convert("RGB")
    probe = rgb.resize((PROBE_SIZE, PROBE_SIZE), Image.LANCZOS)
    samples = _border_samples(probe)
    background = _components_touching_border(_background_like(probe, samples))
    background = background.resize(rgb.size, Image.BILINEAR)
    background = background.filter(ImageFilter.MedianFilter(3))
    background = _harden(background, 110)

    alpha = ImageChops.invert(background)
    alpha = alpha.filter(ImageFilter.MedianFilter(3))
    coverage = sum(1 for value in alpha.getdata() if value >= 128) / float(alpha.width * alpha.height)

    if MIN_CUTOUT_COVERAGE <= coverage <= MAX_CUTOUT_COVERAGE:
        mode = "cutout"
        bbox = alpha.getbbox()
        if bbox:
            rgb = rgb.crop(bbox)
            alpha = alpha.crop(bbox)
        return _fit_badge(rgb, alpha, size), mode

    # Illustrated background: frame her face in a circular cameo badge.
    face = _find_face(rgb)
    if face is None:
        side = min(rgb.size)
        left = (rgb.width - side) // 2
        top = (rgb.height - side) // 2
        crop = rgb.crop((left, top, left + side, top + side))
        mask = Image.new("L", (side, side), 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((0, 0, side - 1, side - 1), fill=255)
        return _fit_badge(crop, mask, size), "cameo"

    centre_x, centre_y, radius = face
    margin = int(radius * 0.16)
    box_left = max(0, centre_x - radius - margin)
    box_top = max(0, centre_y - radius - margin)
    box_right = min(rgb.width, centre_x + radius + margin)
    box_bottom = min(rgb.height, centre_y + radius + margin)
    crop = rgb.crop((box_left, box_top, box_right, box_bottom))
    crop_side = crop.width

    inner = crop_side - margin * 2
    offset = (crop_side - inner) // 2
    mask = Image.new("L", (crop_side, crop_side), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((offset, offset, offset + inner - 1, offset + inner - 1), fill=255)

    # Paint the ring fully inside the visible circle so it never gets clipped.
    ring_width = max(3, int(inner * 0.06))
    draw = ImageDraw.Draw(crop)
    ring_box = (
        offset + ring_width,
        offset + ring_width,
        offset + inner - 1 - ring_width,
        offset + inner - 1 - ring_width,
    )
    draw.ellipse(ring_box, outline=RING_COLOUR, width=ring_width)

    accent_width = max(2, int(inner * 0.02))
    accent_box = (
        ring_box[0] + ring_width + accent_width,
        ring_box[1] + ring_width + accent_width,
        ring_box[2] - ring_width - accent_width,
        ring_box[3] - ring_width - accent_width,
    )
    if accent_box[2] > accent_box[0]:
        draw.ellipse(accent_box, outline=ACCENT_COLOUR, width=accent_width)

    return _fit_badge(crop, mask, size), "cameo"


def _fit_badge(rgb: Image.Image, alpha: Image.Image, size: int) -> tuple[Image.Image, Image.Image]:
    """Scale the badge to the display size, keeping the alpha mask aligned."""
    canvas = Image.new("RGB", (size, size), (0, 0, 0))
    mask = Image.new("L", (size, size), 0)
    rgb = rgb.copy()
    alpha = alpha.copy()
    rgb.thumbnail((size, size), Image.LANCZOS)
    alpha.thumbnail((size, size), Image.LANCZOS)
    offset_x = (size - rgb.width) // 2
    offset_y = size - rgb.height
    canvas.paste(rgb, (offset_x, offset_y))
    mask.paste(alpha, (offset_x, offset_y))
    return canvas, mask


def _composite(rgb: Image.Image, alpha: Image.Image, key: tuple[int, int, int]) -> Image.Image:
    """Flatten a hard-alpha sprite onto the transparency key colour."""
    hard = _harden(alpha)
    field = Image.new("RGB", rgb.size, key)
    return Image.composite(rgb, field, hard)


def _rotate_badge(rgb: Image.Image, alpha: Image.Image, angle: float, bob: int) -> tuple[Image.Image, Image.Image]:
    """Tilt the badge and bob it vertically, keeping a clean hard edge."""
    if bob:
        shifted = Image.new("L", alpha.size, 0)
        shifted.paste(alpha, (0, -bob))
        alpha = shifted
    if abs(angle) < 0.01:
        return rgb, alpha

    centre = (alpha.width / 2.0, alpha.height / 2.0)
    rotated_rgb = rgb.rotate(angle, resample=Image.BICUBIC, center=centre, fillcolor=(0, 0, 0))
    rotated_alpha = alpha.rotate(angle, resample=Image.BICUBIC, center=centre, fillcolor=0)
    return rotated_rgb, _harden(rotated_alpha)


def build_pet_assets(source_path: Path, assets_dir: Path, size: int = DISPLAY_SIZE) -> dict:
    """Build (and cache) the pet badge and animation frames."""
    assets_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = assets_dir / "pet-frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = assets_dir / "pet-manifest.json"

    source = Image.open(source_path)
    (rgb, alpha), mode = _build_badge(source, size)
    key = _pick_key(rgb, alpha)

    sprite_path = assets_dir / "pet-sprite.png"
    _composite(rgb, alpha, key).save(sprite_path)

    frames: dict[str, list[str]] = {"idle": [], "work": []}
    for set_name, table in (("idle", IDLE_FRAMES), ("work", WORK_FRAMES)):
        for index, (angle, bob) in enumerate(table):
            frame_rgb, frame_alpha = _rotate_badge(rgb, alpha, angle, bob)
            frame = _composite(frame_rgb, frame_alpha, key)
            path = frames_dir / f"{set_name}_{index:02d}.png"
            frame.save(path)
            frames[set_name].append(path.name)

    coverage = sum(1 for value in alpha.getdata() if value >= 128) / float(alpha.width * alpha.height)
    manifest = {
        "key": list(key),
        "size": size,
        "coverage": round(coverage, 4),
        "mode": mode,
        "sprite": sprite_path.name,
        "frames": frames,
        "build_version": BUILD_VERSION,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def ensure_pet_assets(source_path: Path, assets_dir: Path, size: int = DISPLAY_SIZE) -> dict:
    """Return the pet manifest, rebuilding it when the cache is stale."""
    manifest_path = assets_dir / "pet-manifest.json"
    if manifest_path.exists() and source_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = None
        if manifest and manifest.get("size") == size and manifest.get("build_version") == BUILD_VERSION:
            sprite = assets_dir / manifest.get("sprite", "pet-sprite.png")
            frames_dir = assets_dir / "pet-frames"
            complete = sprite.exists()
            for names in manifest.get("frames", {}).values():
                for name in names:
                    if not (frames_dir / name).exists():
                        complete = False
                        break
            if complete:
                return manifest
    return build_pet_assets(source_path, assets_dir, size)


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        Path(r"C:\Windows\Fonts\segoeui.ttf"),
        Path(r"C:\Windows\Fonts\tahoma.ttf"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
    ):
        if candidate.exists():
            try:
                return ImageFont.truetype(str(candidate), size)
            except OSError:
                continue
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines[:4]


def render_bubble(text: str, key: tuple[int, int, int], size: int = 15, max_width: int = 230) -> Image.Image:
    """Render a speech bubble (already keyed for transparency) as an RGB image."""
    font = _load_font(size)
    scratch = Image.new("RGB", (10, 10), key)
    draw = ImageDraw.Draw(scratch)
    lines = _wrap(draw, text, font, max_width) if text else []
    if not lines:
        return Image.new("RGB", (1, 1), key)

    line_height = size + 6
    text_width = max(int(draw.textlength(line, font=font)) for line in lines)
    pad_x = 14
    pad_y = 10
    tail = 9
    width = text_width + pad_x * 2
    height = line_height * len(lines) + pad_y * 2 + tail

    image = Image.new("RGB", (width, height), key)
    draw = ImageDraw.Draw(image)
    body_bottom = height - tail
    draw.rounded_rectangle(
        (0, 0, width - 1, body_bottom),
        radius=14,
        fill=BUBBLE_FILL,
        outline=BUBBLE_EDGE,
        width=2,
    )
    draw.polygon(
        [
            (width // 2 - 9, body_bottom - 1),
            (width // 2, height - 1),
            (width // 2 + 9, body_bottom - 1),
        ],
        fill=BUBBLE_FILL,
    )
    draw.line(
        [(width // 2 - 9, body_bottom - 1), (width // 2, height - 1)],
        fill=BUBBLE_EDGE,
        width=2,
    )

    y = pad_y
    for line in lines:
        draw.text((pad_x, y), line, font=font, fill=BUBBLE_TEXT)
        y += line_height
    return image
