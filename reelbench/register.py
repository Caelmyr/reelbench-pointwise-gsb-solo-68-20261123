"""Continuous frame numbering across overlapping scans of one reel."""
from statistics import median

from PIL import ImageDraw

from .art import base_rgba, stored_preview
from .film import GRID, HIGHLIGHT, WARNING
from .geometry import specimen_frame
from .model import (
    InputError,
    MissingError,
    collection,
    dictionary,
    enumeration,
    identity,
    integer,
    model_version,
    scalar,
    string,
    timestamp,
)
from .store import make_id


POLICY = {
    "name": "most_complete_scan_at_splice",
    "summary": "At a splice, adopt one scan per overlap: the one that keeps more complete pictures.",
    "rule": (
        "Where two scans overlap, the register adopts pictures from one of them. Measure how complete "
        "the repeated pictures are in each scan and adopt the higher median; if the medians differ by "
        "2% or less, adopt the earlier scan. Matching picture details decide whether two views are "
        "really one picture, but their clarity never changes the adopted scan: it is recorded as "
        "evidence on both views. One adoption is kept per splice. A picture whose identity is not "
        "settled stays blank. Numbers are never derived from perforation pitch or hole spacing."
    ),
}
CROP_PAD = 0.04
ADOPTION_TIE = 0.02
MIN_LANDMARKS = 2
LANDMARK_TOLERANCE = 0.05
PAIRING_WINDOW = 5
GAP_REASON = (
    "This picture's identity is not settled, so its frame number stays blank. Later numbers wait "
    "until it is joined, separated, or otherwise explained."
)


def crop_window(extent, roi, pad=CROP_PAD):
    width, height = extent
    left, top, right, bottom = (float(value) for value in roi)
    margin = max(3.0, pad * min((right - left) * width, (bottom - top) * height))
    x0 = max(0, int(left * width - margin))
    y0 = max(0, int(top * height - margin))
    x1 = min(width, int(right * width + margin + 0.5))
    y1 = min(height, int(bottom * height + margin + 0.5))
    if x1 - x0 < 8 or y1 - y0 < 8:
        raise InputError("One detected picture is too small to keep a reference view.")
    return x0, y0, x1, y1, [x0 / width, y0 / height, x1 / width, y1 / height]


def gate_lookup(register):
    segments = {segment["id"]: segment for segment in register["segments"]}
    gates = {
        (segment["id"], gate["index"]): gate
        for segment in register["segments"]
        for gate in segment["gates"]
    }
    return segments, gates


def active_entries(register):
    return [entry for entry in register["entries"] if entry["active"]]


def find_entry(register, entry_id):
    entry = next((item for item in active_entries(register) if item["id"] == entry_id), None)
    if entry is None:
        raise InputError("This picture is no longer active in the register.")
    return entry


def observation_parts(register, entry):
    segments, gates = gate_lookup(register)
    parts = []
    for observation in entry["observations"]:
        key = (observation["segment_id"], observation["gate_index"])
        if key in gates:
            parts.append({"observation": observation, "segment": segments[key[0]], "gate": gates[key]})
    return parts


def earliest_key(register, entry):
    parts = observation_parts(register, entry)
    if not parts:
        raise InputError("A picture has no available scan observation.")
    return min((part["segment"]["order"], part["gate"]["index"]) for part in parts)


def ordered_entries(register):
    return sorted(active_entries(register), key=lambda entry: earliest_key(register, entry))


def entry_segments(register, entry):
    return {part["segment"]["id"] for part in observation_parts(register, entry)}


def junction_between(register, first_id, second_id):
    wanted = {first_id, second_id}
    for junction in register["junctions"]:
        if {junction["earlier_segment_id"], junction["later_segment_id"]} == wanted:
            return junction
    return None


def nominal_width(register):
    bounded = [
        gate["width_px"]
        for segment in register["segments"]
        for gate in segment["gates"]
        if gate["condition"] == "bounded"
    ]
    if bounded:
        return float(median(bounded))
    widths = [gate["width_px"] for segment in register["segments"] for gate in segment["gates"]]
    return float(median(widths)) if widths else 1.0


def score_completeness(register):
    nominal = nominal_width(register)
    for segment in register["segments"]:
        for gate in segment["gates"]:
            gate["completeness"] = round(min(1.0, gate["width_px"] / nominal), 5)
        segment["completeness"] = round(float(median(gate["completeness"] for gate in segment["gates"])), 5)
        segment["nominal_width_px"] = round(nominal, 4)


def build_segment(store, subset, result, order):
    if result["kind"] != "gate":
        raise InputError("A frame register is built from Frame gate surveys.")
    if result["tile_id"] != subset["id"]:
        raise InputError("That frame gate survey does not belong to the specimen it names.")
    photo = result["provenance"]["photo"]
    frame = specimen_frame(store.blobs, subset, photo["id"])
    image = base_rgba(frame)
    extent = list(frame.extent)
    rows = result["metrics"].get("rows") or []
    if not rows:
        raise InputError("This frame gate survey found no picture spans to register.")
    bounded = [row["width_px"] for row in rows if row["condition"] == "bounded"]
    reference = float(median(bounded or [row["width_px"] for row in rows])) or 1.0
    gates = []
    for row in rows:
        x0, y0, x1, y1, crop_roi = crop_window(extent, row["roi"])
        crop = image.crop((x0, y0, x1, y1))
        gates.append({
            "index": row["gate"],
            "range": [float(row["range"][0]), float(row["range"][1])],
            "roi": list(row["roi"]),
            "width_px": int(row["width_px"]),
            "width_mm": row["width_mm"],
            "condition": row["condition"],
            "completeness": 1.0 if row["condition"] == "bounded" else round(min(1.0, row["width_px"] / reference), 5),
            "crop_roi": crop_roi,
            "crop_size": list(crop.size),
            "crop_digest": stored_preview(crop, store.blobs),
        })
    return {
        "id": make_id(),
        "order": order,
        "specimen_id": subset["id"],
        "specimen_name": subset["name"],
        "result_id": result["id"],
        "result_version": result["version"],
        "source_version": result["source_version"],
        "photo": {
            "id": photo["id"],
            "name": photo["display_name"],
            "role": photo["role"],
            "original_digest": photo["original_digest"],
        },
        "extent": extent,
        "reference_width_px": round(reference, 4),
        "completeness": 1.0,
        "bounded_count": sum(gate["condition"] == "bounded" for gate in gates),
        "overlay_digest": result["pictures"]["overlay"],
        "gates": gates,
    }


def attach_entries(register, segment):
    for gate in segment["gates"]:
        register["entries"].append({
            "id": make_id(),
            "observations": [{"segment_id": segment["id"], "gate_index": gate["index"]}],
            "active": True,
            "absorbed_by": None,
            "decision": "open",
            "number": None,
            "state": "blank",
            "suspect": False,
            "resolved": False,
            "unresolved_reason": "",
            "note": "",
            "evidence": {},
            "adopted": None,
            "adoption_reason": "",
        })
    previous = next(
        (item for item in register["segments"] if item["order"] == segment["order"] - 1), None
    )
    if previous is not None:
        register["junctions"].append({
            "earlier_segment_id": previous["id"],
            "later_segment_id": segment["id"],
            "status": "open",
            "anchor_entry_id": None,
            "note": "",
            "resolved_at": None,
        })


def suspect_pairs(register):
    pairs = []
    for segment in register["segments"]:
        gates = sorted(segment["gates"], key=lambda gate: gate["index"])
        for first, second in zip(gates, gates[1:]):
            if first["condition"] != "partial" or second["condition"] != "partial":
                continue
            entries = [
                entry for entry in active_entries(register)
                if len(entry["observations"]) == 1
                and entry["observations"][0]["segment_id"] == segment["id"]
                and entry["observations"][0]["gate_index"] in (first["index"], second["index"])
            ]
            if len(entries) != 2:
                continue
            pairs.append({
                "segment_id": segment["id"],
                "scan_name": segment["photo"]["name"],
                "first_gate": first["index"],
                "second_gate": second["index"],
                "entry_ids": [entry["id"] for entry in entries],
                "reason": (
                    "Two short spans both reach unobserved support. They may be one picture split by a "
                    "broken scan edge. Join them, or record them as separate pictures."
                ),
            })
    return pairs


def outer_edge(register, entry):
    """True when the picture touches the start or end of the film this register covers."""
    ordered = ordered_entries(register)
    if not ordered:
        return False
    parts = observation_parts(register, entry)
    if not parts:
        return False
    first_scan = register["segments"][0]["id"]
    last_scan = max(register["segments"], key=lambda segment: segment["order"])["id"]
    if entry["id"] == ordered[0]["id"] and any(part["segment"]["id"] == first_scan for part in parts):
        return True
    if entry["id"] == ordered[-1]["id"] and any(part["segment"]["id"] == last_scan for part in parts):
        return True
    return False


def entry_resolved(register, entry):
    if entry["decision"] == "distinct":
        return True, ""
    if len(entry["observations"]) > 1:
        if entry["decision"] == "same_picture" and entry["evidence"].get("merge"):
            return True, ""
        return False, "Several views are joined without recorded evidence."
    if entry["suspect"]:
        return False, (
            "Two short spans may be one picture split by a broken scan edge. "
            "Join them, or record them as separate pictures."
        )
    gate = observation_parts(register, entry)[0]["gate"]
    if gate["condition"] == "bounded":
        return True, ""
    if outer_edge(register, entry):
        return True, ""
    return False, (
        "This short span reaches unobserved support inside the reel. There is no evidence that it is a "
        "whole picture, so its frame number stays blank. Join it with the picture it continues, or record "
        "it as a whole picture."
    )


def choose_adopted(register, entry, winner_id):
    segments, gates = gate_lookup(register)
    candidates = []
    for observation in entry["observations"]:
        key = (observation["segment_id"], observation["gate_index"])
        if key not in gates:
            continue
        candidates.append(
            (gates[key]["completeness"], -segments[key[0]]["order"], -key[1], key, gates[key])
        )
    if not candidates:
        return None
    pool = [item for item in candidates if item[3][0] == winner_id] or candidates
    _, _, _, key, gate = max(pool, key=lambda item: item[:3])
    segment = segments[key[0]]
    return {
        "segment_id": segment["id"],
        "specimen_id": segment["specimen_id"],
        "specimen_name": segment["specimen_name"],
        "result_id": segment["result_id"],
        "photo_id": segment["photo"]["id"],
        "photo_name": segment["photo"]["name"],
        "gate_index": gate["index"],
        "crop_digest": gate["crop_digest"],
        "crop_roi": list(gate["crop_roi"]),
        "roi": list(gate["roi"]),
        "completeness": gate["completeness"],
        "condition": gate["condition"],
    }


def fmt(value):
    return "—" if value is None else f"{value:.2f}"


def junction_members(register, junction):
    sides = {junction["earlier_segment_id"], junction["later_segment_id"]}
    return [
        entry for entry in active_entries(register)
        if len(entry_segments(register, entry) & sides) > 1
    ]


def junction_scores(register, junction):
    segments, gates = gate_lookup(register)
    scores = {}
    for side in (junction["earlier_segment_id"], junction["later_segment_id"]):
        values = [
            gates[(observation["segment_id"], observation["gate_index"])]["completeness"]
            for entry in junction_members(register, junction)
            for observation in entry["observations"]
            if observation["segment_id"] == side
            and (observation["segment_id"], observation["gate_index"]) in gates
        ]
        scores[side] = round(float(median(values)), 5) if values else None
    return scores


def recompute(register):
    score_completeness(register)
    ordered = ordered_entries(register)
    pairs = suspect_pairs(register)
    suspect_ids = {entry_id for pair in pairs for entry_id in pair["entry_ids"]}
    for entry in ordered:
        entry["suspect"] = entry["id"] in suspect_ids
    register["suspects"] = pairs
    for entry in ordered:
        entry["resolved"], entry["unresolved_reason"] = entry_resolved(register, entry)
    adopted_entries = set()
    for junction in register["junctions"]:
        members = junction_members(register, junction)
        if not members:
            continue
        scores = junction_scores(register, junction)
        earlier, later = scores[junction["earlier_segment_id"]], scores[junction["later_segment_id"]]
        winner = junction["earlier_segment_id"]
        if earlier is not None and later is not None and later - earlier > ADOPTION_TIE:
            winner = junction["later_segment_id"]
        delivered = [
            entry["adopted"]["segment_id"] for entry in members
            if entry["number"] is not None and entry["number"] <= register["sealed_through"]
            and entry.get("adopted")
            and entry["adopted"]["segment_id"] in scores
        ]
        if delivered:
            winner = max(set(delivered), key=delivered.count)
            reason = "Delivered frames keep the scan and position they were delivered with."
        else:
            names = {segment["id"]: segment["photo"]["name"] for segment in register["segments"]}
            reason = (
                "One scan is adopted for this splice. Median picture completeness: "
                f"{names[junction['earlier_segment_id']]} {fmt(earlier)}, "
                f"{names[junction['later_segment_id']]} {fmt(later)}. "
                "Clearer matching marks stay recorded as evidence and do not change the choice."
            )
        for entry in members:
            adopted_entries.add(entry["id"])
            if entry["number"] is not None and entry["number"] <= register["sealed_through"] and entry.get("adopted"):
                continue
            entry["adopted"] = choose_adopted(register, entry, winner)
            entry["adoption_reason"] = reason
        junction["adopted_segment_id"] = winner
        junction["completeness"] = scores
        junction["adoption_reason"] = reason
    for entry in ordered:
        if entry["id"] in adopted_entries:
            continue
        entry["adopted"] = choose_adopted(register, entry, entry["observations"][0]["segment_id"])
        entry["adoption_reason"] = "Only one scan observes this picture."
    sealed = integer(register["sealed_through"], "Delivered frame count", lower=0, upper=1_000_000)
    if sealed > len(ordered):
        raise InputError("The delivered frame count is longer than the active picture list.")
    number = 0
    stopped = ""
    previous_order = None
    for index, entry in enumerate(ordered):
        entry["number"] = None
        entry["state"] = "blank"
        order = earliest_key(register, entry)[0]
        if index < sealed:
            number = index + 1
            entry["number"] = number
            entry["state"] = "sealed"
            previous_order = order
            continue
        if not stopped and previous_order is not None and order != previous_order:
            junction = next(
                (item for item in register["junctions"]
                 if item["earlier_segment_id"] == _segment_id_at(register, previous_order)
                 and item["later_segment_id"] == _segment_id_at(register, order)),
                None,
            )
            if junction is not None and junction["status"] == "open":
                stopped = (
                    "The overlap at this splice is still open. Reconcile the repeated pictures and close "
                    "the overlap before later numbers can be issued."
                )
        if not stopped and not entry["resolved"]:
            stopped = entry["unresolved_reason"] or GAP_REASON
        if stopped:
            if not entry["resolved"] and not entry["unresolved_reason"]:
                entry["unresolved_reason"] = GAP_REASON
            elif entry["resolved"] and entry["state"] == "blank":
                entry["unresolved_reason"] = entry["unresolved_reason"] or GAP_REASON
            continue
        number += 1
        entry["number"] = number
        entry["state"] = "numbered"
        entry["unresolved_reason"] = ""
        previous_order = order
    register["numbered_through"] = max((entry["number"] for entry in ordered if entry["number"]), default=0)
    register["blank_count"] = sum(entry["state"] == "blank" for entry in ordered)
    register["unresolved_count"] = sum(not entry["resolved"] for entry in ordered)
    for junction in register["junctions"]:
        if junction["status"] == "open":
            junction["pairing"] = pairing_hint(register, junction)
        else:
            junction.pop("pairing", None)
    return register


def _segment_id_at(register, order):
    return next((segment["id"] for segment in register["segments"] if segment["order"] == order), None)


def pairing_hint(register, junction):
    tail, head = [], []
    for entry in ordered_entries(register):
        first = entry["observations"][0]["segment_id"]
        if first == junction["earlier_segment_id"]:
            tail.append(entry["id"])
        elif first == junction["later_segment_id"]:
            head.append(entry["id"])
    return {"earlier_tail": tail[-PAIRING_WINDOW:], "later_head": head[:PAIRING_WINDOW]}


def register_fields(payload):
    data = dictionary(payload, "Frame register")
    return {
        "title": string(data.get("title"), "Register title", maximum=100),
        "reel": string(data.get("reel"), "Reel label", maximum=80),
        "notes": string(data.get("notes"), "Register notes", maximum=1200, required=False),
    }


def create_register(store, payload):
    data = dictionary(payload, "Frame register")
    subset = store.tile(identity(data.get("tile_id"), "Specimen ID"))
    result = store.result(identity(data.get("result_id"), "Frame gate survey ID"))
    if result["tile_id"] != subset["id"]:
        raise InputError("That frame gate survey belongs to another specimen.")
    fields = register_fields({
        **data,
        "reel": data.get("reel") or subset.get("batch") or subset["name"],
        "title": data.get("title") or f"{subset['name']} frame numbers",
    })
    register = {
        **fields,
        "id": make_id(),
        "tile_id": subset["id"],
        "tile_name": subset["name"],
        "policy": POLICY,
        "segments": [],
        "junctions": [],
        "entries": [],
        "sealed_through": 0,
        "deliveries": [],
        "suspects": [],
        "numbered_through": 0,
        "blank_count": 0,
        "unresolved_count": 0,
    }
    register["segments"].append(build_segment(store, subset, result, 0))
    attach_entries(register, register["segments"][-1])
    recompute(register)
    return store.add_register(register)


def attach_segment(store, identifier, payload):
    data = dictionary(payload, "Frame scan")
    version = model_version(data)
    result_id = identity(data.get("result_id"), "Frame gate survey ID")
    note = string(data.get("note"), "Attach note", maximum=400, required=False)
    register = store.register(identifier)
    store.require_version(register, version)
    if any(segment["result_id"] == result_id for segment in register["segments"]):
        raise InputError("This frame gate survey is already part of the register.")
    result = store.result(result_id)
    subset = store.tile(result["tile_id"])
    same_photo = any(
        segment["photo"]["id"] == result["provenance"]["photo"]["id"]
        for segment in register["segments"]
    )
    if same_photo:
        raise InputError(
            "This photograph is already in the register. Attach the scan that carries the next stretch of film."
        )
    label = (subset.get("batch") or "").strip()
    if label and label.casefold() != register["reel"].strip().casefold():
        raise InputError(
            f"This scan is labelled {label}, but the register belongs to {register['reel']}. "
            "Attach scans of the same reel only."
        )
    last = max((segment["order"] for segment in register["segments"]), default=-1)
    segment = build_segment(store, subset, result, last + 1)

    def change(current):
        if any(item["result_id"] == result_id for item in current["segments"]):
            raise InputError("This frame gate survey is already part of the register.")
        current["segments"].append(segment)
        attach_entries(current, segment)
        if note:
            current["notes"] = (current["notes"] + "\n" + note).strip()
        recompute(current)

    return store.update_register(
        identifier, version, change, f"Attached frame scan {segment['photo']['name']}",
    )


def _marks(register, entry, raw, label):
    gate = observation_parts(register, entry)[0]["gate"]
    left, top, right, bottom = gate["crop_roi"]
    width, height = right - left, bottom - top
    return [
        [
            round(left + scalar(dictionary(mark, f"{label} mark").get("u"), f"{label} mark X", lower=0, upper=1) * width, 6),
            round(top + scalar(dictionary(mark, f"{label} mark").get("v"), f"{label} mark Y", lower=0, upper=1) * height, 6),
        ]
        for mark in collection(raw, f"{label} marks", maximum=8)
    ]


def parse_landmarks(register, first, second, raw):
    items = collection(raw or [], "Matching details", maximum=8)
    details = []
    first_marks, second_marks = [], []
    for item in items:
        pair = dictionary(item, "Matching detail")
        first_marks.append(pair.get("first"))
        second_marks.append(pair.get("second"))
        details.append(string(pair.get("detail"), "Matching detail", maximum=180, required=False))
    if not items:
        return [], None
    first_points = _marks(register, first, first_marks, "Earlier")
    second_points = _marks(register, second, second_marks, "Later")
    landmarks = [
        {"first": a, "second": b, "detail": note}
        for a, b, note in zip(first_points, second_points, details)
    ]
    offsets = [[a[0] - b[0], a[1] - b[1]] for a, b in zip(first_points, second_points)]
    shift = [
        sum(item[0] for item in offsets) / len(offsets),
        sum(item[1] for item in offsets) / len(offsets),
    ]
    first_gate = observation_parts(register, first)[0]["gate"]
    second_gate = observation_parts(register, second)[0]["gate"]
    left, top, right, bottom = first_gate["crop_roi"]
    second_left, second_top, second_right, second_bottom = second_gate["crop_roi"]
    scale = max(1.0, 0.5 * ((right - left) + (second_right - second_left)))
    drift = max(
        (((item[0] - shift[0]) ** 2 + (item[1] - shift[1]) ** 2) ** 0.5 for item in offsets),
        default=0.0,
    ) / scale
    return landmarks, round(float(drift), 5)


def merge_entries(store, identifier, payload):
    data = dictionary(payload, "Picture join")
    version = model_version(data)
    first_id = identity(data.get("first_id"), "Earlier picture ID")
    second_id = identity(data.get("second_id"), "Later picture ID")
    note = string(data.get("note"), "Join note", maximum=500, required=False)
    clearer = data.get("clearer_marks")
    if clearer is not None:
        clearer = enumeration(clearer, ("earlier", "later", "equal"), "Clearer matching marks")
    register = store.register(identifier)
    store.require_version(register, version)
    if first_id == second_id:
        raise InputError("Choose two different pictures to join.")
    ordered = ordered_entries(register)
    positions = {entry["id"]: index for index, entry in enumerate(ordered)}
    if first_id not in positions or second_id not in positions:
        raise InputError("One of these pictures is no longer active in the register.")
    if positions[first_id] > positions[second_id]:
        first_id, second_id = second_id, first_id
    first, second = find_entry(register, first_id), find_entry(register, second_id)
    if second["number"] is not None and second["number"] <= register["sealed_through"]:
        raise InputError("A delivered frame number cannot be absorbed into another picture.")
    first_segments = entry_segments(register, first)
    second_segments = entry_segments(register, second)
    same_scan = first_segments == second_segments and len(first_segments) == 1
    junction = None
    if same_scan:
        segment_id = next(iter(first_segments))
        indices = sorted(
            observation["gate_index"]
            for entry in (first, second)
            for observation in entry["observations"]
            if observation["segment_id"] == segment_id
        )
        if len(indices) != 2 or indices[1] - indices[0] != 1:
            raise InputError("Join only two adjacent short spans from the same scan.")
        evidence_kind = "torn scan edge"
    else:
        junction = next(
            (
                item for item in register["junctions"]
                if item["earlier_segment_id"] in first_segments
                and item["later_segment_id"] in second_segments
                and second_segments - first_segments == {item["later_segment_id"]}
            ),
            None,
        )
        if junction is None:
            raise InputError(
                "Join a picture from one scan with its repeated picture in the next scan. "
                "Both pictures must be on adjoining scans."
            )
        if junction["status"] != "open":
            raise InputError("This splice is already reconciled. Undo the reconciliation before joining further pictures.")
        earlier_id = junction["earlier_segment_id"]
        later_id = junction["later_segment_id"]
        unpaired_later = [entry for entry in ordered if entry_segments(register, entry) == {later_id}]
        if not unpaired_later or unpaired_later[0]["id"] != second_id:
            raise InputError(
                "Pair the repeated pictures in order: start with the earliest picture of the later scan "
                "that is not yet joined."
            )
        paired_earlier = [
            observation["gate_index"] for entry in ordered
            if {earlier_id, later_id} <= entry_segments(register, entry)
            for observation in entry["observations"] if observation["segment_id"] == earlier_id
        ]
        earlier_gate = next(
            observation["gate_index"] for observation in first["observations"]
            if observation["segment_id"] == earlier_id
        )
        if paired_earlier and earlier_gate <= max(paired_earlier):
            raise InputError(
                "This picture of the earlier scan comes before one already joined. Pair the overlap from "
                "the earliest repeated picture forward."
            )
        clearer = clearer or "equal"
        evidence_kind = "matching picture details"
    landmarks, drift = parse_landmarks(register, first, second, data.get("landmarks"))
    if not same_scan:
        if len(landmarks) < MIN_LANDMARKS:
            raise InputError(
                "Point out at least two matching picture details, one in each scan, before joining across scans."
            )
        if drift > LANDMARK_TOLERANCE:
            raise InputError(
                f"The marked details keep only {100 * (1 - drift):.1f}% of their spacing between the scans. "
                "Mark details that fall in the same place in both pictures."
            )

    def change(current):
        a, b = find_entry(current, first_id), find_entry(current, second_id)
        if b["number"] is not None and b["number"] <= current["sealed_through"]:
            raise InputError("A delivered frame number cannot be absorbed into another picture.")
        joined, seen = [], set()
        for observation in [*a["observations"], *b["observations"]]:
            key = (observation["segment_id"], observation["gate_index"])
            if key not in seen:
                seen.add(key)
                joined.append(observation)
        a["observations"] = joined
        a["decision"] = "same_picture"
        a["note"] = note
        a["evidence"] = {
            "merge": evidence_kind,
            "landmarks": landmarks,
            "landmark_drift": drift,
            "clearer_marks": clearer if not same_scan else None,
            "absorbed_entry": b["id"],
            "note": note,
        }
        b["active"] = False
        b["absorbed_by"] = a["id"]
        b["number"] = None
        b["state"] = "absorbed"
        b["evidence"] = {"merged_into": a["id"]}
        recompute(current)

    label = "same scan" if same_scan else "overlap"
    return store.update_register(identifier, version, change, f"Joined two views of one picture ({label})")


def separate_entries(store, identifier, payload):
    data = dictionary(payload, "Picture separation")
    version = model_version(data)
    first_id = identity(data.get("first_id"), "Earlier picture ID")
    second_id = identity(data.get("second_id"), "Later picture ID")
    note = string(data.get("note"), "Separation note", maximum=500)
    register = store.register(identifier)
    store.require_version(register, version)
    find_entry(register, first_id)
    find_entry(register, second_id)
    pair = next(
        (item for item in register["suspects"] if set(item["entry_ids"]) == {first_id, second_id}),
        None,
    )
    if pair is None:
        raise InputError("These two spans are not a suspected broken-edge pair in one scan.")

    def change(current):
        for entry_id in (first_id, second_id):
            entry = find_entry(current, entry_id)
            if entry["number"] is not None and entry["number"] <= current["sealed_through"]:
                raise InputError("Delivered frames cannot be changed into separate pictures after delivery.")
            entry["decision"] = "distinct"
            entry["note"] = note
            entry["evidence"] = {"separation": note}
        recompute(current)

    return store.update_register(identifier, version, change, "Recorded two short spans as separate pictures")


def undo_join(store, identifier, payload):
    data = dictionary(payload, "Undo join")
    version = model_version(data)
    entry_id = identity(data.get("entry_id"), "Absorbed picture ID")
    register = store.register(identifier)
    store.require_version(register, version)
    absorbed = next((entry for entry in register["entries"] if entry["id"] == entry_id and not entry["active"]), None)
    if absorbed is None:
        raise InputError("That picture is not an absorbed entry.")
    survivor = find_entry(register, absorbed["absorbed_by"])
    if survivor["number"] is not None and survivor["number"] <= register["sealed_through"]:
        raise InputError("A join that supports delivered numbers cannot be undone.")
    crossed = entry_segments(register, survivor)

    def change(current):
        target = next((entry for entry in current["entries"] if entry["id"] == entry_id), None)
        if target is None or target["active"]:
            raise InputError("That picture is no longer absorbed.")
        source = find_entry(current, target["absorbed_by"])
        restored = (target["observations"][0]["segment_id"], target["observations"][0]["gate_index"])
        source["observations"] = [
            observation for observation in source["observations"]
            if (observation["segment_id"], observation["gate_index"]) != restored
        ]
        if len(source["observations"]) == 1:
            source["decision"] = "open"
            source["evidence"] = {}
        target["active"] = True
        target["absorbed_by"] = None
        target["state"] = "blank"
        target["evidence"] = {}
        junction = junction_between(current, *crossed) if len(crossed) == 2 else None
        if junction is not None and junction["status"] != "open":
            junction["status"] = "open"
            junction["anchor_entry_id"] = None
            junction["note"] = "Reopened because a cross-scan join was undone."
            junction["resolved_at"] = None
        recompute(current)

    return store.update_register(identifier, version, change, "Undid a picture join")


def gate_owner(register, segment_id, gate_index):
    for entry in active_entries(register):
        if any(
            observation["segment_id"] == segment_id and observation["gate_index"] == gate_index
            for observation in entry["observations"]
        ):
            return entry
    raise InputError("A picture of the selected scan is no longer active in the register.")


def resolve_overlap(store, identifier, payload):
    data = dictionary(payload, "Splice overlap")
    version = model_version(data)
    earlier_id = identity(data.get("earlier_segment_id"), "Earlier scan")
    later_id = identity(data.get("later_segment_id"), "Later scan")
    mode = enumeration(data.get("mode"), ("adjacent", "repeat_through"), "Overlap boundary")
    anchor_id = data.get("anchor_entry_id")
    note = string(data.get("note"), "Overlap note", maximum=500, required=False)
    register = store.register(identifier)
    store.require_version(register, version)
    junction = junction_between(register, earlier_id, later_id)
    if junction is None:
        raise InputError("These two scans do not adjoin in this register.")
    if junction["status"] != "open":
        raise InputError("This splice has already been reconciled.")
    if any(entry["suspect"] for entry in active_entries(register)):
        raise InputError("Join or separate the suspected broken-edge pairs before closing this splice.")
    earlier = next(segment for segment in register["segments"] if segment["id"] == earlier_id)
    later = next(segment for segment in register["segments"] if segment["id"] == later_id)
    anchor = None
    if mode == "repeat_through":
        if not anchor_id:
            raise InputError("Choose the last picture that both scans show.")
        anchor = find_entry(register, identity(anchor_id, "Last repeated picture"))
        if not {earlier_id, later_id} <= entry_segments(register, anchor):
            raise InputError("The last repeated picture must join these two scans. Join it first.")
        later_gate = min(
            observation["gate_index"] for observation in anchor["observations"]
            if observation["segment_id"] == later_id
        )
        for gate in later["gates"]:
            owner = gate_owner(register, later_id, gate["index"])
            if gate["index"] > later_gate:
                break
            if not {earlier_id, later_id} <= entry_segments(register, owner):
                raise InputError(
                    f"Picture {gate['index']} of {later['photo']['name']} still repeats part of "
                    f"{earlier['photo']['name']} but is not joined with it. Join that picture, or choose a "
                    "later last repeated picture."
                )
    elif junction_members(register, junction):
        raise InputError(
            "These scans already share pictures. Record the last repeated picture instead of calling them adjacent."
        )

    def change(current):
        target = junction_between(current, earlier_id, later_id)
        if target is None or target["status"] != "open":
            raise InputError("This splice has already been reconciled.")
        if mode == "repeat_through":
            target["anchor_entry_id"] = anchor["id"]
        target["status"] = mode
        target["note"] = note
        target["resolved_at"] = timestamp()
        recompute(current)

    return store.update_register(identifier, version, change, "Reconciled the overlap at the splice")


def deliver(store, identifier, payload):
    data = dictionary(payload, "Frame delivery")
    version = model_version(data)
    through = integer(data.get("through"), "Delivered through frame", lower=1, upper=1_000_000)
    recipient = string(data.get("recipient"), "Recipient", maximum=120)
    note = string(data.get("note"), "Delivery note", maximum=500, required=False)
    register = store.register(identifier)
    store.require_version(register, version)
    if through <= register["sealed_through"]:
        raise InputError("That frame number has already been delivered.")
    if through > register["numbered_through"]:
        raise InputError("Number the pictures before delivering them.")

    def change(current):
        if through > current["numbered_through"]:
            raise InputError("The numbered pictures changed. Refresh and retry.")
        ordered = ordered_entries(current)
        delivered = [
            entry for entry in ordered
            if entry["number"] is not None
            and current["sealed_through"] < entry["number"] <= through
        ]
        if len(delivered) != through - current["sealed_through"]:
            raise InputError("The delivered frame run changed. Refresh and retry.")
        current["deliveries"].append({
            "through": through,
            "from": current["sealed_through"] + 1,
            "recipient": recipient,
            "note": note,
            "at": timestamp(),
            "frames": [
                {
                    "number": entry["number"],
                    "entry_id": entry["id"],
                    "segment_id": entry["adopted"]["segment_id"],
                    "specimen_id": entry["adopted"]["specimen_id"],
                    "photo_name": entry["adopted"]["photo_name"],
                    "gate_index": entry["adopted"]["gate_index"],
                    "roi": entry["adopted"]["roi"],
                }
                for entry in delivered
            ],
        })
        current["sealed_through"] = through
        recompute(current)

    return store.update_register(
        identifier, version, change, f"Delivered frames {register['sealed_through'] + 1}–{through}"
    )


def entry_artifact(store, identifier, entry_id):
    register = store.register(identifier)
    entry = next((item for item in register["entries"] if item["id"] == identity(entry_id, "Picture ID")), None)
    if entry is None:
        raise MissingError("The picture is not part of this register.")
    adopted = entry.get("adopted")
    if not adopted:
        raise InputError("This picture has no adopted image yet.")
    return adopted["crop_digest"]


def segment_artifact(store, identifier, segment_id):
    register = store.register(identifier)
    segment = next(
        (item for item in register["segments"] if item["id"] == identity(segment_id, "Scan ID")), None
    )
    if segment is None:
        raise MissingError("The scan is not part of this register.")
    return segment["overlay_digest"]


def confirm_whole(store, identifier, payload):
    data = dictionary(payload, "Short span record")
    version = model_version(data)
    entry_id = identity(data.get("entry_id"), "Picture ID")
    note = string(data.get("note"), "Evidence note", maximum=500, required=False)
    register = store.register(identifier)
    store.require_version(register, version)
    entry = find_entry(register, entry_id)
    if entry["number"] is not None and entry["number"] <= register["sealed_through"]:
        raise InputError("A delivered picture cannot be changed.")
    if len(entry["observations"]) > 1 or entry["suspect"]:
        raise InputError("Join or separate the views of this picture before recording it as a whole picture.")
    gate = observation_parts(register, entry)[0]["gate"]
    if gate["condition"] == "bounded":
        raise InputError("This picture already has both bounds inside the scan.")
    if outer_edge(register, entry):
        raise InputError("This picture already sits at the edge of the scanned run.")

    def change(current):
        target = find_entry(current, entry_id)
        if target["number"] is not None and target["number"] <= current["sealed_through"]:
            raise InputError("A delivered picture cannot be changed.")
        target["decision"] = "distinct"
        target["note"] = note
        target["evidence"] = {"whole_picture": note or "Recorded as a whole picture despite one unobserved bound."}
        recompute(current)

    return store.update_register(identifier, version, change, "Recorded a short span as a whole picture")


def undo_decision(store, identifier, payload):
    data = dictionary(payload, "Undo picture decision")
    version = model_version(data)
    entry_id = identity(data.get("entry_id"), "Picture ID")
    register = store.register(identifier)
    store.require_version(register, version)
    entry = find_entry(register, entry_id)
    if entry["number"] is not None and entry["number"] <= register["sealed_through"]:
        raise InputError("A delivered picture cannot be changed.")
    if entry["decision"] == "open":
        raise InputError("This picture has no recorded decision to undo.")

    def change(current):
        target = find_entry(current, entry_id)
        if target["number"] is not None and target["number"] <= current["sealed_through"]:
            raise InputError("A delivered picture cannot be changed.")
        target["decision"] = "open"
        target["note"] = ""
        target["evidence"] = {}
        recompute(current)

    return store.update_register(identifier, version, change, "Cleared a picture decision")


def gate_artifact(store, identifier, segment_id, gate_index):
    register = store.register(identifier)
    segment = next(
        (item for item in register["segments"] if item["id"] == identity(segment_id, "Scan ID")), None
    )
    if segment is None:
        raise MissingError("The scan is not part of this register.")
    gate = next(
        (item for item in segment["gates"] if item["index"] == integer(gate_index, "Picture index", lower=1, upper=1000)),
        None,
    )
    if gate is None:
        raise MissingError("That picture is not part of this scan.")
    return gate["crop_digest"]


def entry_scan_image(store, identifier, entry_id):
    register = store.register(identifier)
    entry = next((item for item in register["entries"] if item["id"] == identity(entry_id, "Picture ID")), None)
    if entry is None:
        raise MissingError("The picture is not part of this register.")
    if not entry.get("adopted"):
        raise InputError("This picture has no adopted scan view yet.")
    adopted = entry["adopted"]
    segment = next(item for item in register["segments"] if item["id"] == adopted["segment_id"])
    subset = store.tile(segment["specimen_id"])
    frame = specimen_frame(store.blobs, subset, segment["photo"]["id"])
    image = base_rgba(frame)
    width, height = image.size
    left, top, right, bottom = adopted["roi"]
    box = [
        round(left * width), round(top * height),
        round(right * width) - 1, round(bottom * height) - 1,
    ]
    draw = ImageDraw.Draw(image)
    draw.rectangle(box, outline=WARNING, width=3)
    draw.rectangle([max(0, box[0] - 2), max(0, box[1] - 2), box[2] + 2, box[3] + 2], outline=HIGHLIGHT, width=1)
    label = f"#{entry['number']}" if entry["number"] is not None else "blank"
    draw.rectangle([box[0], max(0, box[1] - 20), box[0] + 8 + 9 * len(label), max(0, box[1] - 2)], fill=(30, 38, 43))
    draw.text((box[0] + 4, max(0, box[1] - 18)), label, fill=HIGHLIGHT)
    draw.rectangle([0, 0, width - 1, height - 1], outline=GRID, width=1)
    return image


def present_entry(register, entry):
    segments, gates = gate_lookup(register)
    observations = []
    for observation in entry["observations"]:
        segment = segments.get(observation["segment_id"])
        gate = gates.get((observation["segment_id"], observation["gate_index"]))
        if segment is None or gate is None:
            continue
        observations.append({
            "segment_id": segment["id"],
            "segment_order": segment["order"],
            "specimen_id": segment["specimen_id"],
            "scan_name": segment["photo"]["name"],
            "gate_index": gate["index"],
            "roi": gate["roi"],
            "condition": gate["condition"],
            "completeness": gate["completeness"],
        })
    adopted = entry.get("adopted")
    return {
        "id": entry["id"],
        "number": entry["number"],
        "state": entry["state"],
        "active": entry["active"],
        "absorbed_by": entry["absorbed_by"],
        "resolved": entry["resolved"],
        "reason": entry["unresolved_reason"],
        "suspect": entry["suspect"],
        "decision": entry["decision"],
        "note": entry["note"],
        "observations": observations,
        "adopted": adopted,
        "adoption_reason": entry.get("adoption_reason", ""),
        "evidence": entry.get("evidence", {}),
        "frame_url": f"/api/registers/{register['id']}/entries/{entry['id']}/frame.png",
        "scan_url": f"/api/registers/{register['id']}/entries/{entry['id']}/scan.png",
        "viewer_url": (
            f"/api/specimens/{adopted['specimen_id']}/viewer/{adopted['photo_id']}" if adopted else None
        ),
        "result_url": f"/api/results/{adopted['result_id']}" if adopted else None,
    }


def present_register(register):
    ordered = ordered_entries(register)
    rank = {entry["id"]: index for index, entry in enumerate(ordered)}
    segments = [
        {
            "id": segment["id"],
            "order": segment["order"],
            "specimen_id": segment["specimen_id"],
            "specimen_name": segment["specimen_name"],
            "result_id": segment["result_id"],
            "source_version": segment["source_version"],
            "photo": segment["photo"],
            "completeness": segment["completeness"],
            "nominal_width_px": segment.get("nominal_width_px"),
            "bounded_count": segment["bounded_count"],
            "gate_count": len(segment["gates"]),
            "reference_width_px": segment["reference_width_px"],
            "overlay_url": f"/api/registers/{register['id']}/segments/{segment['id']}/image.png",
            "gates": [
                {key: gate[key] for key in (
                    "index", "range", "roi", "width_px", "width_mm", "condition", "completeness",
                )} | {"crop_url": f"/api/registers/{register['id']}/gates/{segment['id']}/{gate['index']}/crop.png"}
                for gate in segment["gates"]
            ],
        }
        for segment in register["segments"]
    ]
    entries = [present_entry(register, entry) for entry in register["entries"]]
    active = sorted((entry for entry in entries if entry["active"]), key=lambda item: rank[item["id"]])
    junctions = []
    for junction in register["junctions"]:
        hint = junction.get("pairing") or {}
        junctions.append({
            "earlier_segment_id": junction["earlier_segment_id"],
            "later_segment_id": junction["later_segment_id"],
            "status": junction["status"],
            "anchor_entry_id": junction["anchor_entry_id"],
            "note": junction["note"],
            "resolved_at": junction["resolved_at"],
            "adopted_segment_id": junction.get("adopted_segment_id"),
            "completeness": junction.get("completeness"),
            "adoption_reason": junction.get("adoption_reason"),
            "earlier_tail": sorted(
                [entry for entry in active if entry["id"] in set(hint.get("earlier_tail", []))],
                key=lambda entry: rank[entry["id"]],
            ),
            "later_head": sorted(
                [entry for entry in active if entry["id"] in set(hint.get("later_head", []))],
                key=lambda entry: rank[entry["id"]],
            ),
        })
    return dict(register) | {
        "segments": segments,
        "entries": entries,
        "active_entries": active,
        "junctions": junctions,
        "absorbed_entries": [entry for entry in entries if not entry["active"]],
    }


def gate_surveys(store, reel=None):
    wanted = (reel or "").strip().casefold()
    items = []
    for summary in store.list_tiles():
        subset = store.tile(summary["id"])
        label = (subset.get("batch") or "").strip()
        if wanted and label.casefold() != wanted:
            continue
        for result in store.results(subset["id"]):
            if result["kind"] != "gate":
                continue
            photo = result["provenance"]["photo"]
            items.append({
                "result_id": result["id"],
                "result_version": result["version"],
                "specimen_id": subset["id"],
                "specimen_name": subset["name"],
                "reel": label,
                "photo_id": photo["id"],
                "photo_name": photo["display_name"],
                "created_at": result["created_at"],
                "frames": result["metrics"].get("frames", 0),
                "partial": result["metrics"].get("partial", 0),
            })
    return sorted(items, key=lambda item: (item["specimen_name"].casefold(), item["created_at"]))


def available_candidates(store, register):
    used = {segment["result_id"] for segment in register["segments"]}
    used_photos = {segment["photo"]["id"] for segment in register["segments"]}
    return [
        item for item in gate_surveys(store, register["reel"])
        if item["result_id"] not in used and item["photo_id"] not in used_photos
    ]


def registers_for_result(store, result_id):
    for register in store.registers():
        if any(segment["result_id"] == result_id for segment in register["segments"]):
            return register
    return None


def summarize_register(register):
    return {
        key: register[key] for key in (
            "id", "version", "title", "reel", "tile_id", "tile_name", "sealed_through", "numbered_through",
            "blank_count", "unresolved_count", "created_at", "updated_at",
        )
    } | {
        "scan_count": len(register["segments"]),
        "frame_count": len(active_entries(register)),
        "delivery_count": len(register["deliveries"]),
    }
