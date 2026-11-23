"""Decide whether a scanned blemish travels with the film or with the glass.

The bench deliberately separates two registrations for every capture:

* the carrier (glass) corners register the capture onto one shared inspection
  plane, so a position in that plane means the same physical place on the
  scanner glass in every scan;
* the film's own four corners register the strip itself, so the same physical
  piece of emulsion can be followed even when the strip is turned over.

A spot seen at the same *film* place in both scans belongs to the film.  A spot
seen at the same *glass* place belongs to the glass.  One comparison spot may
support only one observation.  Where both descriptions land on the same mark,
or where the second scan does not show the relevant place, the observation is
left undetermined.
"""

import math

import numpy as np

from .geometry import DEFAULT_CORNERS, homography
from .model import InputError


FILM_STATUSES = ("film", "glass", "undetermined")
MIN_MATCH_PX = 6.0


def apply_transform(matrix, points):
    """Map homogeneous 2D points through a 3×3 projective matrix."""
    values = np.atleast_2d(np.asarray(points, dtype=np.float64))
    stacked = np.column_stack((values, np.ones(len(values))))
    mapped = stacked @ np.asarray(matrix, dtype=np.float64).T
    denominator = mapped[:, 2:3]
    if np.any(np.abs(denominator) < 1e-12):
        raise InputError("A marked corner arrangement cannot map these positions.")
    return mapped[:, :2] / denominator


def invert_matrix(matrix):
    try:
        return np.linalg.inv(np.asarray(matrix, dtype=np.float64))
    except np.linalg.LinAlgError as error:
        raise InputError("The marked film corners cannot define a stable transform.") from error


def film_matrix(tile, photo_id):
    record = (tile.get("film_corners") or {}).get(photo_id)
    if not record:
        raise InputError("Mark the film's own four corners on every compared capture first.")
    return homography(DEFAULT_CORNERS, record["corners"])


def frame_matrix(frame):
    return np.asarray(frame.transform, dtype=np.float64).reshape(3, 3)


def transform_summary(matrix, extent):
    """Describe the film-to-film motion in plane terms for the operator."""
    width, height = extent
    basis = np.asarray([[0.5, 0.5], [0.55, 0.5], [0.5, 0.55]], dtype=np.float64)
    mapped = apply_transform(matrix, basis)
    first = (mapped[1] - mapped[0]) * [width, height]
    second = (mapped[2] - mapped[0]) * [width, height]
    determinant = float(first[0] * second[1] - first[1] * second[0])
    angle = math.degrees(math.atan2(float(first[1]), float(first[0])))
    axis = None
    fixed = np.linalg.svd(np.asarray(matrix) - np.eye(3))[2][-1]
    if abs(fixed[2]) > 1e-9:
        candidate = fixed[:2] / fixed[2]
        residual = np.linalg.norm((np.asarray(matrix) @ fixed)[:2] - fixed[:2])
        if residual < 0.02 and np.all(np.abs(candidate) < 1.5):
            axis = [round(float(candidate[0]), 6), round(float(candidate[1]), 6)]
    return {
        "angle_deg": round(angle, 3),
        "proper": determinant > 0,
        "determinant": round(determinant, 8),
        "fixed_point": axis,
    }


def pixel_distance(first, second, extent):
    width, height = extent
    return math.hypot((first[0] - second[0]) * width, (first[1] - second[1]) * height)


def observation_tolerance(marker, extent):
    """Match radius in plane pixels, generous enough for long scratches."""
    width, height = extent
    x0, y0, x1, y1 = marker["bounds"]
    span = max((x1 - x0) * width, (y1 - y0) * height)
    cap = max(8.0, 0.12 * min(width, height))
    return min(max(MIN_MATCH_PX, 0.55 * span + 3.0), cap)


def observed_at(frame, point, mask=None):
    if point is None:
        return False
    x, y = point
    height, width = frame.valid.shape
    if not (0.0 <= x < 1.0 and 0.0 <= y < 1.0):
        return False
    row = min(height - 1, max(0, int(math.floor(y * height))))
    col = min(width - 1, max(0, int(math.floor(x * width))))
    visible = frame.valid if mask is None else (frame.valid & mask)
    return bool(visible[row, col] and not frame.clipped[row, col])


def nearest_marker(prediction, markers, extent):
    best = None
    for index, marker in enumerate(markers):
        distance = pixel_distance(prediction, marker["normalized_center"], extent)
        if distance <= observation_tolerance(marker, extent) and (
            best is None or distance < best[1]
        ):
            best = (index, distance)
    return best


def assign_one_to_one(edges):
    """Greedy nearest-first assignment keeping every compared mark used once."""
    edges = sorted(edges, key=lambda entry: (entry[0], entry[1], entry[2]))
    claim = {}
    owner = {}
    for _, primary_index, hypothesis, comparison_index in edges:
        if comparison_index in owner or (primary_index, hypothesis) in claim:
            continue
        claim[(primary_index, hypothesis)] = comparison_index
        owner[comparison_index] = primary_index
    return claim, owner


def match_observation_provenance(tile, primary_frame, comparison_frame, primary, comparison):
    """Attach film/glass provenance to each primary defect observation."""
    extent = primary_frame.extent
    if comparison_frame.extent != extent:
        raise InputError("Both defect scans must share the same registered inspection plane.")
    primary_photo = primary_frame.photo["id"]
    comparison_photo = comparison_frame.photo["id"]
    glass_a = frame_matrix(primary_frame)
    glass_b = frame_matrix(comparison_frame)
    film_a = film_matrix(tile, primary_photo)
    film_b = film_matrix(tile, comparison_photo)

    glass_to_film = invert_matrix(film_a) @ glass_a
    film_to_glass_b = invert_matrix(glass_b) @ film_b
    between_scans = film_to_glass_b @ glass_to_film

    markers = primary["observations"]
    others = comparison["observations"]
    points = np.asarray([marker["normalized_center"] for marker in markers], dtype=np.float64)
    film_points = apply_transform(glass_to_film, points) if len(points) else np.empty((0, 2))
    predicted_film = apply_transform(between_scans, points) if len(points) else np.empty((0, 2))

    edges = []
    nearest = []
    for index, marker in enumerate(markers):
        glass_place = marker["normalized_center"]
        film_place = predicted_film[index].tolist()
        film_hit = nearest_marker(film_place, others, extent)
        glass_hit = nearest_marker(glass_place, others, extent)
        nearest.append((film_hit, glass_hit, film_place))
        if film_hit is not None:
            edges.append((film_hit[1], index, "film", film_hit[0]))
        if glass_hit is not None:
            edges.append((glass_hit[1], index, "glass", glass_hit[0]))
    claim, owner = assign_one_to_one(edges)

    items = []
    counts = {status: 0 for status in FILM_STATUSES}
    for index, marker in enumerate(markers):
        glass_place = marker["normalized_center"]
        film_place = predicted_film[index].tolist()
        tolerance = observation_tolerance(marker, extent)
        film_hit, glass_hit, _ = nearest[index]
        film_claim = claim.get((index, "film"))
        glass_claim = claim.get((index, "glass"))
        film_observed = observed_at(comparison_frame, film_place)
        glass_observed = observed_at(comparison_frame, glass_place)
        film_mark = film_hit is not None
        glass_mark = glass_hit is not None
        film_contested = film_mark and film_claim is None and owner.get(film_hit[0]) not in (None, index)
        glass_contested = glass_mark and glass_claim is None and owner.get(glass_hit[0]) not in (None, index)

        if film_claim is not None and glass_claim is not None:
            status = "undetermined"
            reason = (
                "both placements explain the same mark"
                if film_claim == glass_claim
                else "both placements hold separate marks"
            )
        elif film_contested or glass_contested:
            status = "undetermined"
            reason = "the other placement's mark is claimed by another observation"
        elif film_claim is not None:
            if glass_mark:
                status = "undetermined"
                reason = (
                    "both placements explain the same mark"
                    if glass_hit is not None and glass_hit[0] == film_claim
                    else "the glass placement also holds a mark"
                )
            elif not glass_observed:
                status = "undetermined"
                reason = "the glass placement is not visible in the second scan"
            else:
                status = "film"
                reason = "the film placement holds a mark and the glass placement is blank"
        elif glass_claim is not None:
            if film_mark:
                status = "undetermined"
                reason = (
                    "both placements explain the same mark"
                    if film_hit is not None and film_hit[0] == glass_claim
                    else "the film placement also holds a mark"
                )
            elif not film_observed:
                status = "undetermined"
                reason = "the film placement is not visible in the second scan"
            else:
                status = "glass"
                reason = "the glass placement holds a mark and the film placement is blank"
        elif not film_observed or not glass_observed:
            status = "undetermined"
            reason = "one placement is not visible in the second scan"
        else:
            status = "undetermined"
            reason = "no counterpart in either placement"

        film_center = film_points[index].tolist() if len(film_points) else None
        matched = film_claim if film_claim is not None else glass_claim
        matched_key = others[matched]["key"] if matched is not None else None
        matched_distance = None
        if matched is not None:
            base = film_place if matched == film_claim else glass_place
            matched_distance = round(pixel_distance(others[matched]["normalized_center"], base, extent), 3)
        item = {
            "key": marker["key"],
            "status": status,
            "reason": reason,
            "film": status == "film",
            "glass": status == "glass",
            "film_observed": film_observed,
            "glass_observed": glass_observed,
            "film_position": [round(float(value), 6) for value in film_place],
            "glass_position": [round(float(value), 6) for value in glass_place],
            "film_center": [round(float(value), 6) for value in film_center] if film_center else None,
            "matched_key": matched_key,
            "match_distance_px": matched_distance,
            "tolerance_px": round(tolerance, 3),
        }
        marker["provenance"] = {
            "status": status,
            "reason": reason,
            "film": status == "film",
            "glass": status == "glass",
            "matched_key": matched_key,
        }
        items.append(item)
        counts[status] += 1

    summary = transform_summary(between_scans, extent)
    return {
        "method": "two-registration placement match",
        "primary_photo_id": primary_photo,
        "comparison_photo_id": comparison_photo,
        "film_corners": {
            "primary": (tile["film_corners"][primary_photo])["corners"],
            "comparison": (tile["film_corners"][comparison_photo])["corners"],
        },
        "glass_to_film": [round(float(value), 9) for value in glass_to_film.ravel()],
        "between_scans": [round(float(value), 9) for value in between_scans.ravel()],
        "motion": summary,
        "items": items,
        "counts": counts,
        "comparison_observations": comparison["total_observations"],
        "note": (
            "Film placements follow the marked film corners; glass placements use the shared "
            "carrier plane. Each mark in the second scan can support one observation only. "
            "Spots that both descriptions can explain, or whose counterpart is not visible, "
            "stay undetermined."
        ),
    }

