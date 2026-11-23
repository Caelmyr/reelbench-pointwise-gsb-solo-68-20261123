"""Build one adopted focus-composite working image from two registered scans.

A curled frame is scanned twice, once focused on the middle and once on the
edges.  The composite keeps a single top-to-bottom boundary: every plane row
takes its left side from one scan and its right side from the other, with no
blending across the cut.  The boundary keeps the most in-focus pixels first,
avoids the user-marked tie lines second, and crosses a marked line only where
both scans hold real, unclipped pixels so the texture genuinely connects.
"""

import math

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from .art import ACCENT, INK, SHADOW, base_rgba
from .geometry import DEFAULT_CORNERS, exclusion_mask, polygon_mask, specimen_frame
from .model import (
    DERIVED_ROLES,
    InputError,
    MAX_TILE_IMAGES,
    collection,
    dictionary,
    identity,
    match_slot,
    model_version,
    numeric_pair,
    string,
    timestamp,
)
from .photos import linear_to_srgb, png_bytes, store_capture, uint8_image
from .store import make_id


COMPOSITE_ROLE = "composite"
MAX_TIE_LINES = 12
MIN_LINE_FRACTION = 0.02
MIN_SUPPORT_FRACTION = 0.15
MIN_PLANE_EDGE = 24
NEGATIVE = -1e15

RULE_TEXT = (
    "One top-to-bottom boundary; each side keeps the scan marked sharp there. "
    "The boundary first keeps the most in-focus pixels, then avoids the marked lines, "
    "then holds its course and leans left. It crosses a marked line only where both "
    "scans have real, unclipped pixels, so no break or doubling is hidden. Remaining "
    "ties put the first-listed scan on the left. Excluded marks and missing coverage "
    "stay as holes."
)


def composite_parameters(payload):
    data = dictionary(payload, "Focus composite")
    expected = model_version(data)
    first_id = identity(data.get("first_photo_id"), "First scan")
    second_id = identity(data.get("second_photo_id"), "Second scan")
    if first_id == second_id:
        raise InputError("Choose two different scans for the composite.")
    first_region = identity(data.get("first_region_id"), "First sharp region")
    second_region = identity(data.get("second_region_id"), "Second sharp region")
    name = string(data.get("name"), "Working image name", maximum=80, required=False)
    raw_lines = collection(data.get("lines", []), "Marked lines", maximum=MAX_TIE_LINES)
    lines = []
    for item in raw_lines:
        entry = dictionary(item, "Marked line")
        start = numeric_pair(entry.get("start"), "Line start", lower=0, upper=1)
        end = numeric_pair(entry.get("end"), "Line end", lower=0, upper=1)
        if math.hypot(end[0] - start[0], end[1] - start[1]) < MIN_LINE_FRACTION:
            raise InputError("Each marked line needs endpoints at least 2% of the plane apart.")
        lines.append({"start": start, "end": end})
    return expected, first_id, second_id, first_region, second_region, name, lines


def check_source_photo(tile, photo_id, label):
    photo = match_slot(tile["photos"], photo_id, "photo")
    if photo["role"] in DERIVED_ROLES:
        raise InputError(f"{label} is already a focus composite; choose the original scans.")
    plane = tile.get("plane")
    if plane is None:
        raise InputError("Set a base photo and mark the specimen plane first.")
    if photo["id"] != plane["photo_id"] and photo["id"] not in tile["registrations"]:
        raise InputError(f"Register {label.lower()} in the specimen plane first.")
    return photo


def check_sharp_region(tile, region_id, label):
    region = match_slot(tile["regions"], region_id, "sampling region")
    if region["purpose"] == "ignore":
        raise InputError(f"{label} cannot be an exclusion region.")
    return region


def raster_lines(extent, lines):
    width, height = extent
    masks = []
    for line in lines:
        grid = Image.new("1", (width, height))
        points = [
            (line["start"][0] * width - 0.5, line["start"][1] * height - 0.5),
            (line["end"][0] * width - 0.5, line["end"][1] * height - 0.5),
        ]
        ImageDraw.Draw(grid).line(points, fill=1, width=1)
        masks.append(np.asarray(grid, dtype=bool))
    return masks


def plan_seam(clear_left, clear_right, penalized, blocked):
    """Dynamic-program one 8-connected top-to-bottom boundary.

    The score is lexicographic: retained in-focus pixels dominate, marked-line
    crossings are the tiebreaker.  Blocked cells (marked lines without real
    support in both scans) can never be crossed.
    """
    height, width = clear_left.shape
    scale = height + 1
    cum_left = np.cumsum(clear_left, axis=1, dtype=np.int64)
    cum_right = np.cumsum(clear_right, axis=1, dtype=np.int64)
    padded_left = np.zeros((height, width + 1), dtype=np.int64)
    padded_left[:, 1:] = cum_left
    padded_right = np.zeros((height, width + 1), dtype=np.int64)
    padded_right[:, 1:] = cum_right
    gain = padded_left + (cum_right[:, -1:] - padded_right)
    base = gain.astype(np.float64) * scale
    penalty = np.zeros((height, width + 1), dtype=np.float64)
    penalty[:, :width] = penalized
    base -= penalty
    blocked_full = np.zeros((height, width + 1), dtype=bool)
    blocked_full[:, :width] = blocked
    base[blocked_full] = NEGATIVE
    totals = base[0].copy()
    parents = np.zeros((height, width + 1), dtype=np.int8)
    for row in range(1, height):
        previous = totals
        from_left = np.concatenate(([NEGATIVE], previous[:-1]))
        from_right = np.concatenate((previous[1:], [NEGATIVE]))
        stack = np.stack((previous, from_left, from_right))
        choice = np.argmax(stack, axis=0)
        totals = base[row] + stack[choice, np.arange(width + 1)]
        parents[row] = choice
    best = int(np.argmax(totals))
    if totals[best] <= NEGATIVE / 2:
        raise InputError(
            "The marked lines leave no top-to-bottom crossing where both scans have real "
            "pixels. Adjust the sharp regions or the marked lines."
        )
    columns = np.zeros(height, dtype=np.int64)
    columns[-1] = best
    for row in range(height - 1, 0, -1):
        step = parents[row][columns[row]]
        columns[row - 1] = columns[row] + (0 if step == 0 else -1 if step == 1 else 1)
    return columns, float(totals[best])


def choose_orientation(clear_first, clear_second, penalized, blocked):
    plans = []
    for left_first in (True, False):
        left, right = (clear_first, clear_second) if left_first else (clear_second, clear_first)
        try:
            columns, total = plan_seam(left, right, penalized, blocked)
        except InputError:
            continue
        plans.append((left_first, columns, total))
    if not plans:
        raise InputError(
            "The marked lines leave no top-to-bottom crossing where both scans have real "
            "pixels. Adjust the sharp regions or the marked lines."
        )
    first_left = next((plan for plan in plans if plan[0]), None)
    if first_left is None:
        return plans[0]
    rival = next((plan for plan in plans if not plan[0]), None)
    if rival is not None and rival[2] > first_left[2]:
        return rival
    return first_left


def assemble(frame_left, frame_right, columns, ignore):
    height, width = frame_left.valid.shape
    left_side = np.arange(width)[None, :] < columns[:, None]
    take_left = left_side & frame_left.valid & ~ignore
    take_right = ~left_side & frame_right.valid & ~ignore
    linear = np.zeros((height, width, 3), dtype=np.float32)
    linear[take_left] = frame_left.linear[take_left]
    linear[take_right] = frame_right.linear[take_right]
    support = take_left | take_right
    return linear, support, take_left, take_right


def line_crossings(line_masks, columns):
    height = len(columns)
    width = line_masks[0].shape[1] if line_masks else 1
    results = []
    for mask in line_masks:
        hits = [(row, int(columns[row])) for row in range(height) if columns[row] < width and mask[row, columns[row]]]
        results.append(hits[0] if hits else None)
    return results


def seam_map_picture(frame, composite):
    """Draw the adopted boundary, the marked lines and the source sides."""
    image = base_rgba(frame)
    layer = np.array(image.convert("RGB")).astype(np.float32)
    height, width = frame.valid.shape
    columns = np.asarray(composite["seam_columns"], dtype=np.int64)
    left_side = np.arange(width)[None, :] < columns[:, None]
    teal = np.array((121, 187, 174), dtype=np.float32)
    warm = np.array((233, 147, 106), dtype=np.float32)
    seen = frame.valid
    layer[left_side & seen] = layer[left_side & seen] * 0.80 + teal * 0.20
    layer[~left_side & seen] = layer[~left_side & seen] * 0.80 + warm * 0.20
    image = Image.fromarray(layer.astype(np.uint8)).convert("RGBA")
    draw = ImageDraw.Draw(image)
    seam_points = [(float(columns[row]), float(row)) for row in range(height)]
    draw.line(seam_points, fill=SHADOW, width=6)
    draw.line(seam_points, fill=ACCENT, width=2)
    for line in composite["lines"]:
        points = [
            (line["start"][0] * width - 0.5, line["start"][1] * height - 0.5),
            (line["end"][0] * width - 0.5, line["end"][1] * height - 0.5),
        ]
        color = (248, 207, 108) if line["crossed"] else (103, 231, 200)
        draw.line(points, fill=SHADOW, width=5)
        draw.line(points, fill=color, width=2)
        crossing = line.get("crossing")
        if crossing:
            x, y = crossing[0] * width - 0.5, crossing[1] * height - 0.5
            draw.line((x - 6, y, x + 6, y), fill=SHADOW, width=3)
            draw.line((x, y - 6, x, y + 6), fill=SHADOW, width=3)
    sources = {source["side"]: source for source in composite["sources"]}
    for side, x_anchor in (("left", 10), ("right", width - 10)):
        source = sources.get(side)
        if not source:
            continue
        label = f"{side.upper()} · {source['name'][:30]}"
        anchor_x = x_anchor if side == "left" else x_anchor - draw.textlength(label)
        draw.text((anchor_x, 8), label, fill=INK, stroke_width=2, stroke_fill=SHADOW)
    return image


def build_composite(store, tile_id, payload):
    tile = store.tile(tile_id)
    expected, first_id, second_id, first_region_id, second_region_id, name, lines = composite_parameters(payload)
    store.require_version(tile, expected)
    plane = tile.get("plane")
    if plane is None:
        raise InputError("Set a base photo and mark the specimen plane first.")
    if min(plane["size"]) < MIN_PLANE_EDGE:
        raise InputError("The registered plane is too small for a focus composite.")
    first = check_source_photo(tile, first_id, "First scan")
    second = check_source_photo(tile, second_id, "Second scan")
    first_region = check_sharp_region(tile, first_region_id, "First sharp region")
    second_region = check_sharp_region(tile, second_region_id, "Second sharp region")
    frame_first = specimen_frame(store.blobs, tile, first_id)
    frame_second = specimen_frame(store.blobs, tile, second_id)
    extent = frame_first.extent
    ignore = exclusion_mask(extent, tile["regions"])
    clear_first = polygon_mask(extent, first_region["vertices"]) & frame_first.valid & ~frame_first.clipped & ~ignore
    clear_second = polygon_mask(extent, second_region["vertices"]) & frame_second.valid & ~frame_second.clipped & ~ignore
    if not clear_first.any() or not clear_second.any():
        raise InputError("The sharp regions leave no usable, unexcluded pixels for one of the scans.")
    connectable = (
        frame_first.valid & frame_second.valid & ~frame_first.clipped & ~frame_second.clipped & ~ignore
    )
    line_masks = raster_lines(extent, lines)
    thick = [ndimage.binary_dilation(mask, structure=np.ones((3, 3))) for mask in line_masks]
    line_zone = np.any(thick, axis=0) if thick else np.zeros(extent[::-1], dtype=bool)
    penalized = line_zone & connectable
    blocked = line_zone & ~connectable
    left_is_first, columns, _ = choose_orientation(clear_first, clear_second, penalized, blocked)
    frame_left, frame_right = (frame_first, frame_second) if left_is_first else (frame_second, frame_first)
    photo_left, photo_right = (first, second) if left_is_first else (second, first)
    region_left, region_right = (first_region, second_region) if left_is_first else (second_region, first_region)
    linear, support, take_left, take_right = assemble(frame_left, frame_right, columns, ignore)
    if not take_left.any() or not take_right.any():
        raise InputError("One scan contributes nothing at this boundary; adjust the sharp regions.")
    support_fraction = float(support.mean())
    if support_fraction < MIN_SUPPORT_FRACTION:
        raise InputError("The two scans and their sharp regions cover less than 15% of the plane.")
    crossings = line_crossings(thick, columns)
    height, width = support.shape
    line_records = []
    for line, hit in zip(lines, crossings):
        line_records.append({
            "start": line["start"],
            "end": line["end"],
            "crossed": hit is not None,
            "crossing": [round((hit[1] + 0.5) / width, 5), round((hit[0] + 0.5) / height, 5)] if hit else None,
        })
    image = uint8_image(linear_to_srgb(linear), support.astype(np.float32))
    title = name or f"{tile['name']} focus composite"
    record = store_capture(
        store.blobs,
        png_bytes(image),
        COMPOSITE_ROLE,
        f"{title}.png",
        f"Focus composite: {photo_left['name']} left of one top-to-bottom boundary, {photo_right['name']} right.",
        make_id(),
        synthetic=bool(first.get("synthetic")) and bool(second.get("synthetic")),
    )
    stats = {
        "left_pixels": int(np.count_nonzero(take_left)),
        "right_pixels": int(np.count_nonzero(take_right)),
        "hole_pixels": int(np.count_nonzero(~support)),
        "support_percent": round(100 * support_fraction, 2),
        "lines_crossed": sum(1 for line in line_records if line["crossed"]),
        "lines_kept_whole": sum(1 for line in line_records if not line["crossed"]),
    }
    record["composite"] = {
        "kind": "focus-composite",
        "created_at": timestamp(),
        "rule": RULE_TEXT,
        "sources": [
            {"photo_id": photo_left["id"], "name": photo_left["name"], "side": "left",
             "region_id": region_left["id"], "region_name": region_left["name"]},
            {"photo_id": photo_right["id"], "name": photo_right["name"], "side": "right",
             "region_id": region_right["id"], "region_name": region_right["name"]},
        ],
        "seam_columns": columns.tolist(),
        "plane_size": list(extent),
        "lines": line_records,
        "excluded_regions": [region["name"] for region in tile["regions"] if region["purpose"] == "ignore"],
        "stats": stats,
    }

    def change(current):
        if current["plane"] != plane:
            raise InputError("The specimen plane changed while the composite was built. Retry.")
        for photo in [item for item in current["photos"] if item["role"] in DERIVED_ROLES]:
            if current["plane"] and current["plane"]["photo_id"] == photo["id"]:
                raise InputError("The earlier working image is the base photo; reset the plane before replacing it.")
            current["registrations"].pop(photo["id"], None)
            current["photos"].remove(photo)
        if len(current["photos"]) >= MAX_TILE_IMAGES:
            raise InputError(f"A specimen supports at most {MAX_TILE_IMAGES} photographs.")
        current["photos"].append(record)
        current["registrations"][record["id"]] = {
            "base_photo_id": current["plane"]["photo_id"],
            "corners": DEFAULT_CORNERS,
            "support_percent": stats["support_percent"],
        }

    updated = store.edit_tile(tile_id, expected, change, "capture", f"Built focus composite working image · {record['name']}")
    summary = {
        "photo_id": record["id"],
        "left": {"name": photo_left["name"], "pixels": stats["left_pixels"]},
        "right": {"name": photo_right["name"], "pixels": stats["right_pixels"]},
        "support_percent": stats["support_percent"],
        "lines_crossed": stats["lines_crossed"],
        "lines_kept_whole": stats["lines_kept_whole"],
        "rule": RULE_TEXT,
    }
    return updated, summary
