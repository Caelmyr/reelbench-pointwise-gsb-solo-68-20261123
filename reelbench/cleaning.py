"""Choose one cleaning step and verify it against a later scan.

Only a blemish whose placement is settled may enter a cleaning step.  The
bench holds one adopted plan per defect measurement: either the scan glass or
the film is cleaned this round, never both.  Marked damaged emulsion is a hard
constraint, so a film step never crosses it while another step can clear a
settled mark instead.

Every position below lives in the shared carrier plane of the specimen unless
it is explicitly a film-square coordinate.  Film-square coordinates name a
physical place on the strip, so they survive the strip being turned.
"""

import numpy as np

from .geometry import DEFAULT_CORNERS, homography, specimen_frame
from .model import InputError, model_version
from .provenance import apply_transform, observed_at, observation_tolerance, pixel_distance

STATUS_REMOVED = "removed"
STATUS_REMAINING = "remaining"
STATUS_UNSEEN = "unseen"
STATUSES = (STATUS_REMOVED, STATUS_REMAINING, STATUS_UNSEEN)
DAMAGE_MARGIN = 0.02


def settled_marks(record):
    """Spots that may be cleaned: settled placement, not dismissed."""
    provenance = record["metrics"].get("provenance")
    if not provenance:
        raise InputError("Compare this census with a turned scan before planning cleaning.")
    lookup = {item["key"]: item for item in provenance["items"]}
    decisions = record.get("decisions", {})
    usable, excluded = [], []
    for observation in record["metrics"]["observations"]:
        decision = decisions.get(observation["key"], {}).get("decision", "review")
        item = lookup.get(observation["key"])
        if decision == "dismiss":
            excluded.append({**observation, "reason": "dismissed in review"})
            continue
        if item is None or item["status"] == "undetermined":
            excluded.append({
                **observation,
                "reason": (item or {}).get("reason", "placement was not settled"),
            })
            continue
        usable.append({
            "key": observation["key"],
            "type": observation["type"],
            "status": item["status"],
            "area_pixels": observation["area_pixels"],
            "glass_position": item["glass_position"],
            "film_position": item["film_position"],
            "film_center": item["film_center"],
            "reason": item["reason"],
        })
    return usable, excluded


def inside_or_near(position, region, margin):
    u, v = position
    block = list(region["vertices"])
    left = min(x for x, _ in block) - margin
    right = max(x for x, _ in block) + margin
    top = min(y for _, y in block) - margin
    bottom = max(y for _, y in block) + margin
    return left <= u <= right and top <= v <= bottom


def damaged_regions(tile):
    return [region for region in tile["regions"] if region["purpose"] == "emulsion"]


def step_risk(tile, marks):
    """A film step may only touch the strip where the emulsion is intact."""
    hazards = damaged_regions(tile)
    touched = [
        mark["key"] for mark in marks
        if any(inside_or_near(mark["glass_position"], region, DAMAGE_MARGIN) for region in hazards)
    ]
    return {
        "regions": [{"id": region["id"], "name": region["name"]} for region in hazards],
        "mark_keys": touched,
        "contact": bool(touched),
    }


def step_record(object_name, marks, tile):
    risk = step_risk(tile, marks) if object_name == "film" else {
        "regions": [], "mark_keys": [], "contact": False,
    }
    return {
        "object": object_name,
        "space": "carrier_plane" if object_name == "glass" else "film_square",
        "label": "Clean the scan glass" if object_name == "glass" else "Clean the film",
        "clears": len(marks),
        "spots": [
            {
                "key": mark["key"],
                "type": mark["type"],
                "area_pixels": mark["area_pixels"],
                "glass_position": mark["glass_position"],
                "film_position": mark["film_position"],
                "film_center": mark["film_center"],
            }
            for mark in marks
        ],
        "emulsion": risk,
        "touches_damaged_emulsion": risk["contact"],
    }


def choose_step(candidates):
    """Pick one step, with damaged emulsion ahead of the extra marks."""
    available = [entry for entry in candidates if entry["clears"] > 0]
    if not available:
        return None, "No settled mark is left to clean."
    safe = [entry for entry in available if not entry["touches_damaged_emulsion"]]
    if safe:
        best = max(safe, key=lambda entry: (entry["clears"], entry["object"] == "glass"))
        blocked = [entry for entry in available if entry not in safe]
        if blocked:
            worst = max(blocked, key=lambda entry: entry["clears"])
            if worst["clears"] > best["clears"]:
                return best, (
                    f"{worst['label'].lower()} would clear {worst['clears']} settled marks but "
                    f"crosses marked damaged emulsion at {len(worst['emulsion']['mark_keys'])} mark(s); "
                    f"the safer step goes first."
                )
        return best, f"{best['label']} clears the most settled marks without crossing damaged emulsion."
    best = min(available, key=lambda entry: (len(entry["emulsion"]["mark_keys"]), entry["clears"], entry["object"] != "glass"))
    return best, (
        "Every step able to clear a settled mark crosses marked damaged emulsion; "
        "the step crossing the fewest damaged marks goes first."
    )


def snapshot_observations(record):
    return [dict(observation) for observation in record["metrics"]["observations"]]


def record_settings(record):
    if record.get("detector"):
        return dict(record["detector"])
    options = record["parameters"]["options"]
    return {
        "sensitivity": options["sensitivity"],
        "spot_radius": options["spot_radius"],
        "polarity": options["polarity"],
        "minimum_area": options["minimum_area"],
        "maximum_area": options["maximum_area"],
    }


def build_plan(tile, record):
    if record["kind"] != "defects":
        raise InputError("A cleaning plan starts from Dust and scratch observations.")
    provenance = record["metrics"].get("provenance")
    if not provenance:
        raise InputError("Compare this census with a second scan at another angle before planning.")
    marks, excluded = settled_marks(record)
    glass_marks = [mark for mark in marks if mark["status"] == "glass"]
    film_marks = [mark for mark in marks if mark["status"] == "film"]
    candidates = [step_record("glass", glass_marks, tile), step_record("film", film_marks, tile)]
    adopted, reason = choose_step(candidates)
    return {
        "tile_id": tile["id"],
        "tile_version": tile["version"],
        "measurement_id": record["id"],
        "measurement_version": record["version"],
        "source_version": record["source_version"],
        "counts": provenance["counts"],
        "placement": {item["key"]: item["status"] for item in provenance["items"]},
        "settled_marks": len(marks),
        "excluded": excluded,
        "candidates": candidates,
        "adopted_object": adopted["object"] if adopted else None,
        "adopted": adopted,
        "reason": reason,
        "basis": (
            "Dismissed marks and marks whose placement is undetermined are left alone. "
            "One step only: the adopted object is the one this plan cleans."
        ),
        "motion": provenance["motion"],
        "pictures": dict(record["pictures"]),
        "observations": snapshot_observations(record),
        "decisions": record.get("decisions", {}),
        "capture": record["provenance"]["photo"],
        "paired_capture": record["provenance"]["paired_photo"],
        "region": record["provenance"]["region"],
        "options": record_settings(record),
        "comparison_photo_id": record["provenance"]["paired_photo"]["id"] if record["provenance"]["paired_photo"] else None,
    }


def create_plan(store, measurement_id, payload):
    record = store.result(measurement_id)
    tile = store.tile(record["tile_id"])
    existing = store.plan_for_measurement(measurement_id)
    if existing is not None:
        store.require_version(existing, model_version(payload))
    document = build_plan(tile, record)
    return store.save_plan(document, existing)


def film_place_in_followup(record, tile, film_center):
    """Carry a film-square coordinate into the follow-up scan's carrier plane."""
    corners = (tile.get("film_corners") or {}).get(record["provenance"]["photo"]["id"])
    if not corners:
        return None
    photo_point = apply_transform(homography(DEFAULT_CORNERS, corners["corners"]), [film_center])
    plane_to_capture = np.asarray(
        record["provenance"]["photo"]["plane_to_capture"], dtype=np.float64,
    ).reshape(3, 3)
    try:
        capture_to_plane = np.linalg.inv(plane_to_capture)
    except np.linalg.LinAlgError as error:
        raise InputError("The follow-up scan's plane transform is not stable.") from error
    return apply_transform(capture_to_plane, photo_point)[0].tolist()


def position_on_object(plan, spot, record, tile):
    """Where a planned spot sits in the follow-up scan's own carrier plane."""
    if plan["adopted_object"] == "glass":
        return spot["glass_position"]
    if not spot.get("film_center"):
        return None
    return film_place_in_followup(record, tile, spot["film_center"])


def verify_object(plan, record, tile, frame):
    """Compare a follow-up census with the planned marks at their object places."""
    extent = record["support"]["registered_image_extent"]
    floor = max(6.0, 0.6 * plan["options"]["spot_radius"])
    observations = record["metrics"]["observations"]
    results = []
    for spot in plan["adopted"]["spots"]:
        point = position_on_object(plan, spot, record, tile)
        rounded = [round(float(value), 6) for value in point] if point is not None else None
        if point is None or not observed_at(frame, point):
            results.append({
                **spot, "state": STATUS_UNSEEN, "matched_key": None,
                "distance_px": None, "followup_position": rounded,
            })
            continue
        match = None
        for observation in observations:
            distance = pixel_distance(observation["normalized_center"], point, extent)
            radius = max(floor, observation_tolerance(observation, extent))
            if distance <= radius and (match is None or distance < match[1]):
                match = (observation["key"], distance)
        results.append({
            **spot,
            "state": STATUS_REMAINING if match else STATUS_REMOVED,
            "matched_key": match[0] if match else None,
            "distance_px": round(match[1], 3) if match else None,
            "followup_position": rounded,
        })
    counts = {status: 0 for status in STATUSES}
    for entry in results:
        counts[entry["state"]] += 1
    return results, counts


def comparison_notes(plan, record, tile):
    """What would make this rescan comparison less trustworthy."""
    notes = []
    options = record["parameters"]["options"]
    changed = [
        key for key in ("sensitivity", "spot_radius", "polarity", "minimum_area", "maximum_area")
        if options[key] != plan["options"][key]
    ]
    if changed:
        notes.append(
            "The follow-up census used different detection settings (" + ", ".join(changed) + ")."
        )
    if record["provenance"]["region"] and plan["region"]:
        if record["provenance"]["region"]["name"] != plan["region"]["name"]:
            notes.append("The follow-up census used a different sampling region.")
    if record["source_version"] != tile["version"]:
        notes.append("The specimen changed after this follow-up census was saved.")
    return notes


def verify_positions(plan, record, tile, frame):
    """Classify each planned spot against a later, differently posed scan."""
    if record["tile_id"] != plan["tile_id"]:
        raise InputError("The follow-up scan belongs to a different specimen.")
    if record["id"] == plan["measurement_id"]:
        raise InputError("Choose a scan made after cleaning, not the original census.")
    if any(capture["id"] == record["provenance"]["photo"]["id"] for capture in (plan["capture"], plan["paired_capture"])):
        raise InputError("The follow-up scan is one of the scans that produced this plan.")
    if tuple(frame.extent) != tuple(record["support"]["registered_image_extent"]):
        raise InputError("The follow-up plane changed after its census. Re-run the census.")
    results, counts = verify_object(plan, record, tile, frame)
    notes = comparison_notes(plan, record, tile)
    if record["support"]["available_percent"] < 100:
        notes.append(
            f"The follow-up scan is supported over {record['support']['available_percent']}% of the "
            "plane; spots outside that support are left unseen."
        )
    return {
        "plan_id": plan["id"],
        "plan_version": plan["version"],
        "original_measurement_id": plan["measurement_id"],
        "original_measurement_version": plan["measurement_version"],
        "object": plan["adopted_object"],
        "measurement_id": record["id"],
        "measurement_version": record["version"],
        "source_version": record["source_version"],
        "counts": counts,
        "items": results,
        "pictures": {
            "original_source": plan["pictures"]["source"],
            "original_overlay": plan["pictures"]["overlay"],
            "followup_source": record["pictures"]["source"],
            "followup_overlay": record["pictures"]["overlay"],
        },
        "capture": record["provenance"]["photo"],
        "notes": notes,
        "note": (
            "Each planned spot was checked at its place on the adopted object. "
            "Spots outside the follow-up scan's visible support stay unseen and blank."
        ),
    }


def use_census_region(plan, record):
    """Refuse a comparison whose census covered a different area."""
    region = plan.get("region")
    followup = record["provenance"].get("region")
    if (region or {}).get("id") != (followup or {}).get("id"):
        raise InputError("Run the follow-up census over the same sampling region as the plan.")


def save_check(store, plan_id, payload):
    plan = store.plan(plan_id)
    store.require_version(plan, payload.get("version"))
    measurement_id = payload.get("measurement_id")
    if not isinstance(measurement_id, str):
        raise InputError("Choose the scan made after cleaning.")
    record = store.result(measurement_id)
    if record["kind"] != "defects":
        raise InputError("Run Dust and scratch observations on the follow-up scan first.")
    use_census_region(plan, record)
    tile = store.tile(plan["tile_id"])
    frame = specimen_frame(store.blobs, tile, record["provenance"]["photo"]["id"])
    document = verify_positions(plan, record, tile, frame)
    return store.save_plan_check(plan["tile_id"], plan_id, document)


def plan_summary(record):
    adopted = record.get("adopted")
    return {
        "id": record["id"],
        "tile_id": record["tile_id"],
        "measurement_id": record["measurement_id"],
        "version": record["version"],
        "object": record["adopted_object"],
        "label": adopted["label"] if adopted else "No settled mark to clean",
        "clears": adopted["clears"] if adopted else 0,
        "settled_marks": record["settled_marks"],
        "counts": record["counts"],
        "reason": record["reason"],
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
    }
