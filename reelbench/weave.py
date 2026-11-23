"""Measure directional lamina spacing from supported image texture."""

import math

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from .art import ACCENT, SHADOW, base_rgba
from .defects import CONNECTIVITY, gaussian_supported
from .model import InputError
from .photos import luminance


def axial_difference(first, second):
    return (first - second + 90) % 180 - 90


def spectrum(signal, support, scale):
    height, width = signal.shape
    window = np.outer(np.hanning(height), np.hanning(width))
    transformed = np.fft.fftshift(np.fft.fft2(signal * window * support))
    fx = np.fft.fftshift(np.fft.fftfreq(width, scale[0]))
    fy = np.fft.fftshift(np.fft.fftfreq(height, scale[1]))
    xx, yy = np.meshgrid(fx, fy)
    frequency = np.hypot(xx, yy)
    angle = (np.degrees(np.arctan2(yy, xx)) + 90) % 180
    return np.abs(transformed) ** 2, frequency, angle


def peak_in_sector(power, frequencies, angles, direction, period_range, tolerance):
    low, high = period_range
    sector = ((frequencies >= 1 / high) & (frequencies <= 1 / low)
              & (np.abs(axial_difference(angles, direction)) <= tolerance))
    readings = power[sector]
    if readings.size < 8:
        raise InputError("The supported area is too small to resolve a peak in the selected spacing range.")
    peak_index = np.unravel_index(np.argmax(np.where(sector, power, -1)), power.shape)
    noise = float(np.median(readings))
    ratio = float(power[peak_index]) / max(noise, 1e-9)
    if ratio < 7 or power[peak_index] <= max(float(readings.sum()) * .012, 1e-9):
        return None
    return {
        "angle_degrees": round(float(angles[peak_index]), 2),
        "pitch": round(1 / float(frequencies[peak_index]), 4),
        "peak_to_background": round(ratio, 2),
    }


def observation_picture(frame, region, support, readings, center, span):
    overlay = base_rgba(frame)
    layer = np.array(overlay)
    mask = region & ~support
    layer[mask, :3] = (layer[mask, :3] * .63 + np.array([227, 156, 91]) * .37).astype(np.uint8)
    image = Image.fromarray(layer)
    draw = ImageDraw.Draw(image)
    sx, sy = frame.scale or (1.0, 1.0)
    for reading, color in zip(readings, ((103, 231, 200), (248, 207, 108))):
        if not reading:
            continue
        theta = math.radians(reading["angle_degrees"])
        delta = (math.cos(theta) * span / sx, math.sin(theta) * span / sy)
        first = tuple(center[i] - delta[i] for i in range(2))
        last = tuple(center[i] + delta[i] for i in range(2))
        draw.line((first, last), fill=(16, 31, 29), width=6)
        draw.line((first, last), fill=color, width=3)
    return image


def window_weave(gray, visible, scale, settings):
    """Measure the two lamina families inside one continuously supported window."""
    supported_rows, supported_cols = np.nonzero(visible)
    if len(supported_rows) < 400:
        raise InputError("Choose a larger unobscured core region.")
    ys = slice(int(supported_rows.min()), int(supported_rows.max()) + 1)
    xs = slice(int(supported_cols.min()), int(supported_cols.max()) + 1)
    support = visible[ys, xs]
    height, width = support.shape
    if min(height, width) < 32 or support.mean() < .65:
        raise InputError("This coated area needs at least 32 pixels per side and 65% image support.")
    min_px, max_px = settings["minimum_pitch"], settings["maximum_pitch"]
    if min_px >= max_px:
        raise InputError("Maximum pitch must exceed minimum pitch.")
    lower, upper = min_px * min(scale), max_px * max(scale)
    window = gray[ys, xs]
    background, weight = gaussian_supported(window, support, max(3.0, max_px / 2))
    trusted = support & (weight > .63)
    if trusted.mean() < .55:
        raise InputError("The selected window has too much incomplete support for a lamina reading.")
    power, frequencies, angles = spectrum(window - background, trusted, scale)
    reference = settings["reference_axis"]
    candidates = [
        peak_in_sector(power, frequencies, angles, direction, (lower, upper), settings["tolerance"])
        for direction in (reference, (reference + 90) % 180)
    ]
    first, second = candidates
    crossing = abs(float(axial_difference(first["angle_degrees"], second["angle_degrees"]))) if first and second else None
    rows = [{"family": family, **(row or {"angle_degrees": None, "pitch": None, "peak_to_background": None})}
            for family, row in zip(("A", "B"), candidates)]
    return {
        "rows": rows,
        "candidates": candidates,
        "trusted": trusted,
        "window": (ys, xs),
        "axis_a_pitch": first["pitch"] if first else None,
        "axis_b_pitch": second["pitch"] if second else None,
        "crossing_angle": round(crossing, 2) if crossing is not None else None,
        "supported_pixels": int(np.count_nonzero(trusted)),
        "span": min(width * scale[0], height * scale[1]) * .19,
    }


def largest_component(mask):
    labels, count = ndimage.label(mask, CONNECTIVITY)
    if count <= 1:
        return mask
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    return labels == int(np.argmax(sizes))


def composite_weave_picture(frame, region, seam, entries):
    overlay = base_rgba(frame)
    layer = np.array(overlay)
    covered = np.zeros(region.shape, dtype=bool)
    for entry in entries:
        covered |= entry["component"]
    mask = region & ~covered
    layer[mask, :3] = (layer[mask, :3] * .63 + np.array([227, 156, 91]) * .37).astype(np.uint8)
    image = Image.fromarray(layer)
    draw = ImageDraw.Draw(image)
    height, width = region.shape
    seam_points = [(float(seam[row]), float(row)) for row in range(height)]
    draw.line(seam_points, fill=SHADOW, width=6)
    draw.line(seam_points, fill=ACCENT, width=2)
    sx, sy = frame.scale or (1.0, 1.0)
    for entry in entries:
        rows_idx, cols_idx = np.nonzero(entry["component"])
        box = (int(cols_idx.min()), int(rows_idx.min()), int(cols_idx.max()) + 1, int(rows_idx.max()) + 1)
        measured = entry.get("outcome") is not None
        draw.rectangle(box, outline=SHADOW, width=4)
        draw.rectangle(box, outline=(103, 231, 200) if measured else (158, 154, 146), width=2)
        if not measured:
            continue
        center = (float(np.median(cols_idx)), float(np.median(rows_idx)))
        span = entry["outcome"]["span"]
        for reading, color in zip(entry["outcome"]["candidates"], ((103, 231, 200), (248, 207, 108))):
            if not reading:
                continue
            theta = math.radians(reading["angle_degrees"])
            delta = (math.cos(theta) * span / sx, math.sin(theta) * span / sy)
            first = tuple(center[i] - delta[i] for i in range(2))
            last = tuple(center[i] + delta[i] for i in range(2))
            draw.line((first, last), fill=(16, 31, 29), width=6)
            draw.line((first, last), fill=color, width=3)
    return image


def composite_weave(frame, linear, region, settings, composite):
    """Measure each side of the focus boundary on its own scan only.

    A region that crosses the boundary is split along it; every side is read
    from the longest continuous run it holds on one source scan.  Sides without
    a long enough run are left blank, and the two sides are never combined
    into a single peak.
    """
    height, width = frame.valid.shape
    seam = np.asarray(composite["seam_columns"], dtype=np.int64)
    if seam.shape[0] != height or composite.get("plane_size") != [width, height]:
        raise InputError("The focus composite no longer matches the current specimen plane.")
    left_side = np.arange(width)[None, :] < seam[:, None]
    sources = {source["side"]: source for source in composite["sources"]}
    scale = tuple(frame.scale or (1.0, 1.0))
    gray = luminance(linear)
    segments = []
    entries = []
    for side_name, side in (("left", left_side), ("right", ~left_side)):
        visible = region & side & frame.valid & ~frame.clipped
        if not np.any(visible):
            continue
        component = largest_component(visible)
        source = sources.get(side_name) or {}
        rows_idx, cols_idx = np.nonzero(component)
        segment = {
            "side": side_name,
            "source_photo_id": source.get("photo_id"),
            "source_name": source.get("name"),
            "pixels": int(np.count_nonzero(component)),
            "bounds": [
                round(int(cols_idx.min()) / width, 5),
                round(int(rows_idx.min()) / height, 5),
                round((int(cols_idx.max()) + 1) / width, 5),
                round((int(rows_idx.max()) + 1) / height, 5),
            ],
        }
        entry = {"component": component, "outcome": None}
        try:
            outcome = window_weave(gray, component, scale, settings)
        except InputError as error:
            segment["status"] = "insufficient"
            segment["reason"] = str(error)
            segment["rows"] = [
                {"family": family, "angle_degrees": None, "pitch": None, "peak_to_background": None}
                for family in ("A", "B")
            ]
            segment["axis_a_pitch"] = segment["axis_b_pitch"] = segment["crossing_angle"] = None
            segment["trusted_pixels"] = None
        else:
            entry["outcome"] = outcome
            segment.update(
                status="measured",
                reason=None,
                rows=outcome["rows"],
                axis_a_pitch=outcome["axis_a_pitch"],
                axis_b_pitch=outcome["axis_b_pitch"],
                crossing_angle=outcome["crossing_angle"],
                trusted_pixels=outcome["supported_pixels"],
            )
        segments.append(segment)
        entries.append(entry)
    if not segments:
        raise InputError("Choose a larger unobscured core region.")
    if len(segments) == 1 and segments[0]["status"] != "measured":
        raise InputError(segments[0]["reason"])
    metrics = {
        "unit": "mm" if frame.scale else "px",
        "segments": segments,
        "seam_crossed": len(segments) > 1,
        "supported_pixels": sum(segment["pixels"] for segment in segments),
        "axis_a_pitch": None,
        "axis_b_pitch": None,
        "crossing_angle": None,
        "note": "The focus boundary splits the window; each side is measured only on the scan that holds it. "
                "Sides without a long enough continuous run are left blank, and the two sides are never combined into one peak.",
    }
    return metrics, composite_weave_picture(frame, region, seam, entries)


def measure_weave(frame, linear, region, settings):
    composite = frame.photo.get("composite")
    if composite:
        return composite_weave(frame, linear, region, settings, composite)
    visible = region & frame.valid & ~frame.clipped
    scale = tuple(frame.scale or (1.0, 1.0))
    gray = luminance(linear)
    outcome = window_weave(gray, visible, scale, settings)
    ys, xs = outcome["window"]
    full_support = np.zeros_like(visible)
    full_support[ys, xs] = outcome["trusted"]
    supported_rows, supported_cols = np.nonzero(visible)
    center = (float(np.median(supported_cols)), float(np.median(supported_rows)))
    metrics = {
        "rows": outcome["rows"],
        "unit": "mm" if frame.scale else "px",
        "axis_a_pitch": outcome["axis_a_pitch"],
        "axis_b_pitch": outcome["axis_b_pitch"],
        "crossing_angle": outcome["crossing_angle"],
        "supported_pixels": outcome["supported_pixels"],
        "note": "The two sectors follow the indicated lamina directions. Missing peaks are left blank. Colored axes indicate the supported local directions, not individual layer boundaries.",
    }
    return metrics, observation_picture(frame, region, full_support, outcome["candidates"], center, outcome["span"])
