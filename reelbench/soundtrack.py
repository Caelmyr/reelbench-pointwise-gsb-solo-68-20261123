"""Adopted audio renders from two registered scans of one optical track."""
import wave
from io import BytesIO

import numpy as np
from scipy import ndimage

from .analysis import capture_provenance, specimen_provenance
from .color import corrected_linear
from .film import area_components, contiguous, local_evidence, perforation_result
from .geometry import region_domain, specimen_frame
from .model import InputError, dictionary, identity, scalar, string
from .parameters import TOOLS


HOLE_MINIMUM_PX = 18
MEDIAN_PX = 3
SOURCE_LABELS = {"head": "scan-head", "tail": "scan-tail", "blend": "both scans", "unobserved": "no scan"}


def hole_cutoff_default():
    return TOOLS["perforation"]["fields"]["cutoff"]["default"]


def take_fields(payload):
    data = dictionary(payload, "Adopted sample")
    return {
        "paired_photo_id": identity(data.get("paired_photo_id"), "Second scan"),
        "carrier_region_id": identity(data.get("carrier_region_id"), "Perforation region"),
        "fade_ms": scalar(data.get("fade_ms", 24.0), "Handoff fade", lower=4, upper=150),
    }


def excerpt_fields(payload):
    data = dictionary(payload, "Excerpt")
    return {
        "start_s": scalar(data.get("start_s"), "Excerpt start", lower=0, upper=7200),
        "end_s": scalar(data.get("end_s"), "Excerpt end", lower=0, upper=7200),
        "note": string(data.get("note"), "Excerpt note", maximum=500, required=False),
    }


def wav_bytes(pcm, rate):
    stream = BytesIO()
    with wave.open(stream, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(int(rate))
        out.writeframes(np.asarray(pcm, dtype="<i2").tobytes())
    return stream.getvalue()


def read_wav_samples(blobs, digest):
    with wave.open(str(blobs.require(digest)), "rb") as source:
        rate = source.getframerate()
        frames = source.readframes(source.getnframes())
    return np.frombuffer(frames, dtype="<i2").copy(), rate


def column_reading(frame, linear, area, options, hole_cutoff):
    """Clear share per lane column with sprocket bright spots kept out of the track."""
    gray, evidence, bounds = local_evidence(frame, linear, area)
    if bounds.height > .30 * bounds.width:
        raise InputError("Mark the optical lane as a narrow region along the carrier.")
    data = gray[bounds.slices]
    available = evidence[bounds.slices]
    spots = area_components((data >= hole_cutoff) & available, available, bounds, HOLE_MINIMUM_PX)
    excluded = np.zeros_like(available)
    for spot in spots:
        box = spot["bounds"]
        excluded[box.top - bounds.top:box.bottom - bounds.top, box.left - bounds.left:box.right - bounds.left] = True
    excluded = ndimage.binary_dilation(excluded, iterations=1) & available
    kept = available & ~excluded
    count = kept.sum(axis=0)
    expected = area[bounds.slices].sum(axis=0) - excluded.sum(axis=0)
    good = (count >= options["min_support"] * expected) & (count > 0)
    clear = (data > options["clear_cutoff"]) & kept
    share = np.divide(clear.sum(axis=0), count, out=np.zeros(bounds.width), where=count > 0)
    return {"bounds": bounds, "share": share, "good": good, "spots": len(spots), "excluded_px": int(excluded.sum())}


def scan_frame(store, tile, photo_id, apply_neutral):
    frame = specimen_frame(store.blobs, tile, photo_id)
    linear, _ = corrected_linear(tile, frame, apply_neutral)
    return frame, linear


def perforation_anchors(frame, linear, carrier_area):
    options = {key: field["default"] for key, field in TOOLS["perforation"]["fields"].items()}
    stats, _, _ = perforation_result(frame, linear, carrier_area, options)
    return sorted(row["x_px"] for row in stats["rows"] if row["condition"] == "open")


def shared_timebase(anchor_sets, mm_per_px):
    pitches = [float(np.median(np.diff(xs))) for xs in anchor_sets.values() if len(xs) > 2]
    tolerance = max(2.0, 0.18 * min(pitches)) if pitches else 3.0
    observations = sorted((x, name) for name, xs in anchor_sets.items() for x in xs)
    clusters = []
    for x, name in observations:
        if clusters and x - clusters[-1]["xs"][-1] <= tolerance:
            clusters[-1]["xs"].append(x)
            clusters[-1]["scans"].add(name)
        else:
            clusters.append({"xs": [x], "scans": {name}})
    anchors = [{"x_px": float(np.mean(item["xs"])), "scans": sorted(item["scans"])} for item in clusters]
    common = [item for item in anchors if len(item["scans"]) > 1]
    if len(common) < 2:
        raise InputError("The two scans do not share enough measured perforations to fix a common travel timebase.")
    xs = [item["x_px"] for item in anchors]
    pitch = float(np.median(np.diff(xs))) if len(xs) > 1 else None
    origin = common[0]["x_px"]
    return {
        "origin_x_px": round(origin, 4),
        "pitch_px": round(pitch, 4) if pitch else None,
        "pitch_mm": round(pitch * mm_per_px, 5) if pitch else None,
        "anchors": [
            {"x_px": round(item["x_px"], 3), "film_mm": round((item["x_px"] - origin) * mm_per_px, 4), "scans": item["scans"]}
            for item in anchors
        ],
    }


def cleaned(reading):
    smooth = ndimage.median_filter(reading["share"], size=MEDIAN_PX, mode="nearest")
    return np.where(reading["good"], smooth, 0.0)


def state_runs(states):
    start = 0
    for index in range(1, len(states)):
        if states[index] != states[start]:
            yield start, index - 1
            start = index
    yield start, len(states) - 1


def choose_handoff(head_clean, tail_clean, both, fade_px, fade_ms):
    runs = [(int(a), int(b)) for a, b in contiguous(both) if b - a >= fade_px + 4]
    if not runs:
        raise InputError("The two scans share no clearly photographed segment long enough for one handoff fade.")
    head_mid = float(np.median(head_clean[both]))
    tail_mid = float(np.median(tail_clean[both]))
    gain = float(np.clip(head_mid / tail_mid, 0.25, 4.0)) if tail_mid > 1e-6 else 1.0
    tail_scaled = tail_clean * gain
    mismatch = np.abs(head_clean - tail_scaled)
    chosen = min(runs, key=lambda run: float(np.median(mismatch[run[0] + 2:run[1] - 2])))
    smooth = ndimage.gaussian_filter1d(mismatch, sigma=max(1.0, fade_px / 6))
    left, right = chosen
    center = left + 2 + int(np.argmin(smooth[left + 2:right - 2]))
    start = int(min(max(center - fade_px // 2, left), right - fade_px))
    rms = float(np.sqrt(np.mean(mismatch[start:start + fade_px] ** 2)))
    return start, start + fade_px, gain, tail_scaled, rms, len(runs)


def render_take(store, measurement_id, payload):
    result = store.result(measurement_id)
    if result["kind"] != "sound":
        raise InputError("An adopted sample attaches to an optical track diagnostic.")
    fields = take_fields(payload)
    tile = store.tile(result["tile_id"])
    params = result["parameters"]
    options = params["options"]
    region = result["provenance"]["region"]
    if region is None:
        raise InputError("Rerun the diagnostic on a marked optical lane before adopting a sample.")
    carrier = next((item for item in tile["regions"] if item["id"] == fields["carrier_region_id"]), None)
    if carrier is None or carrier["purpose"] == "ignore":
        raise InputError("Choose the marked carrier region used for perforation measurement.")
    first_id = params["photo_id"]
    second_id = fields["paired_photo_id"]
    if second_id == first_id:
        raise InputError("Choose the other scan of the same leader.")
    registered = {tile["plane"]["photo_id"], *tile["registrations"]} if tile.get("plane") else set()
    if second_id not in {item["id"] for item in tile["photos"]}:
        raise InputError("The second scan is no longer attached to this specimen.")
    if second_id not in registered:
        raise InputError("Register the second scan in the specimen plane before adopting a sample.")
    scans = []
    hole_cutoff = hole_cutoff_default()
    for photo_id in (first_id, second_id):
        frame, linear = scan_frame(store, tile, photo_id, params["apply_neutral"])
        lane_area = region_domain(frame.extent, tile, region)
        reading = column_reading(frame, linear, lane_area, options, hole_cutoff)
        carrier_area = region_domain(frame.extent, tile, carrier)
        anchors = perforation_anchors(frame, linear, carrier_area)
        scans.append({"photo_id": photo_id, "frame": frame, "linear": linear, "reading": reading, "anchors": anchors})
    if scans[0]["reading"]["bounds"] != scans[1]["reading"]["bounds"]:
        raise InputError("The two reads do not share the lane geometry.")
    scale = scans[0]["frame"].scale
    if scale is None:
        raise InputError("Enter the specimen's physical dimensions before adopting a sample.")
    mm_per_px = float(scale[0])
    rate = int(round(options["speed_mm_s"] / mm_per_px))
    if not 300 <= rate <= 96000:
        raise InputError("The transport speed and plane scale give an unusable sample rate.")
    for scan in scans:
        if not scan["reading"]["good"].any():
            raise InputError("One of the scans shows no readable track in the marked lane.")
    scans.sort(key=lambda scan: int(np.argmax(scan["reading"]["good"])))
    head, tail = scans
    timebase = shared_timebase({"head": head["anchors"], "tail": tail["anchors"]}, mm_per_px)
    head_clean, tail_clean = cleaned(head["reading"]), cleaned(tail["reading"])
    both = head["reading"]["good"] & tail["reading"]["good"]
    fade_px = max(4, int(round(fields["fade_ms"] / 1000 * rate)))
    start, stop, gain, tail_scaled, rms, qualified = choose_handoff(
        head_clean, tail_clean, both, fade_px, fields["fade_ms"],
    )
    width = head["reading"]["share"].shape[0]
    states = np.array(["unobserved"] * width, dtype=object)
    out = np.zeros(width)
    states[:start] = np.where(head["reading"]["good"][:start], "head", "unobserved")
    out[:start] = head_clean[:start]
    states[start:stop] = "blend"
    t = (np.arange(fade_px) + 0.5) / fade_px
    out[start:stop] = head_clean[start:stop] * np.cos(t * np.pi / 2) + tail_scaled[start:stop] * np.sin(t * np.pi / 2)
    states[stop:] = np.where(tail["reading"]["good"][stop:], "tail", "unobserved")
    out[stop:] = tail_scaled[stop:]
    sourced = states != "unobserved"
    dc = float(np.mean(out[sourced])) if sourced.any() else 0.0
    audio = out - dc
    peak = float(np.max(np.abs(audio[sourced]))) if sourced.any() else 0.0
    out_gain = 0.89 / peak if peak > 1e-9 else 1.0
    audio *= out_gain
    audio[~sourced] = 0.0
    pcm = np.rint(np.clip(audio, -1, 1) * 32767).astype("<i2")
    origin = timebase["origin_x_px"]
    left = head["reading"]["bounds"].left
    segments = [
        {
            "state": state,
            "source": SOURCE_LABELS[state],
            "first_sample": first,
            "last_sample": last,
            "first_s": round(first / rate, 6),
            "last_s": round((last + 1) / rate, 6),
            "first_mm": round((left + first - origin) * mm_per_px, 4),
            "last_mm": round((left + last - origin) * mm_per_px, 4),
            "readable": state != "unobserved",
        }
        for first, last in state_runs(states)
        for state in [states[first]]
    ]
    switch_mm = (left + (start + stop) / 2 - origin) * mm_per_px
    rationale = (
        f"{qualified} shared clear run(s) qualified for the handoff; the single {fields['fade_ms']:.0f} ms "
        f"equal-power fade sits at film {switch_mm:.1f} mm, the lowest-disagreement point of the quietest run. "
        f"Tail scan gain {gain:.3f} was matched to the head scan over the overlap; residual disagreement at the "
        f"fade is {rms:.4f} RMS clear share. A {MEDIAN_PX} px median pass removed isolated scan specks, and "
        f"{head['reading']['spots'] + tail['reading']['spots']} sprocket bright spot(s) reaching into the lane "
        f"were excluded from the track read."
    )
    digest = store.blobs.put(wav_bytes(pcm, rate))
    record = {
        "tile_id": tile["id"],
        "result_id": result["id"],
        "kind": "soundtake",
        "label": "Adopted optical track sample",
        "specimen": specimen_provenance(tile),
        "lane_region": region,
        "carrier_region": carrier,
        "head": capture_provenance(head["frame"]),
        "tail": capture_provenance(tail["frame"]),
        "options": {**options, "fade_ms": fields["fade_ms"], "median_px": MEDIAN_PX,
                    "hole_cutoff": hole_cutoff, "hole_minimum_px": HOLE_MINIMUM_PX},
        "timebase": {
            **timebase,
            "mm_per_px": round(mm_per_px, 8),
            "mm_per_sample": round(mm_per_px, 8),
            "sample_rate": rate,
            "speed_mm_s": round(rate * mm_per_px, 4),
            "plane_width_px": head["frame"].extent[0],
            "plane_height_px": head["frame"].extent[1],
        },
        "switch": {
            "first_sample": start,
            "last_sample": stop - 1,
            "film_mm": round(switch_mm, 4),
            "fade_ms": fields["fade_ms"],
            "tail_gain": round(gain, 5),
            "mismatch_rms": round(rms, 5),
            "qualified_runs": qualified,
            "rationale": rationale,
        },
        "segments": segments,
        "stats": {
            "samples": width,
            "duration_s": round(width / rate, 6),
            "unobserved_percent": round(100 * float(np.mean(~sourced)), 2),
            "unobserved_runs": sum(1 for segment in segments if segment["state"] == "unobserved"),
            "hole_spots_excluded": int(head["reading"]["spots"] + tail["reading"]["spots"]),
            "hole_pixels_excluded": int(head["reading"]["excluded_px"] + tail["reading"]["excluded_px"]),
        },
        "audio": {
            "digest": digest,
            "sample_rate": rate,
            "samples": width,
            "format": "pcm16-mono-wav",
            "dc_offset": round(dc, 6),
            "output_gain": round(out_gain, 5),
        },
        "note": "One adopted render from two registered scans of the same leader; each film position appears once. "
                "Columns without scan coverage are digital silence and stay marked unobserved — missing image, not readable film.",
    }
    return store.save_take(record)


def cut_excerpt(store, take_id, payload):
    take = store.take(take_id)
    fields = excerpt_fields(payload)
    rate = take["audio"]["sample_rate"]
    samples = take["audio"]["samples"]
    duration = samples / rate
    start_s, end_s = fields["start_s"], fields["end_s"]
    if end_s <= start_s:
        raise InputError("The excerpt end must be after its start.")
    if start_s >= duration or end_s > duration + 1e-6:
        raise InputError("Keep the excerpt inside the adopted take.")
    first = int(round(start_s * rate))
    last = min(int(round(end_s * rate)), samples)
    if last - first < 8:
        raise InputError("The excerpt is shorter than a usable span.")
    pcm, _ = read_wav_samples(store.blobs, take["audio"]["digest"])
    digest = store.blobs.put(wav_bytes(pcm[first:last], rate))
    segments = []
    for segment in take["segments"]:
        low = max(segment["first_sample"], first)
        high = min(segment["last_sample"], last - 1)
        if low > high:
            continue
        segments.append({
            **segment,
            "first_sample": low - first,
            "last_sample": high - first,
            "first_s": round((low - first) / rate, 6),
            "last_s": round((high - first + 1) / rate, 6),
        })
    record = {
        "take_id": take["id"],
        "tile_id": take["tile_id"],
        "result_id": take["result_id"],
        "kind": "soundexcerpt",
        "label": "Restoration excerpt",
        "note": fields["note"],
        "take_first_sample": first,
        "take_last_sample": last - 1,
        "start_s": round(first / rate, 6),
        "end_s": round(last / rate, 6),
        "timebase": take["timebase"],
        "segments": segments,
        "unobserved_spans": sum(1 for segment in segments if segment["state"] == "unobserved"),
        "audio": {"digest": digest, "sample_rate": rate, "samples": last - first, "format": "pcm16-mono-wav"},
        "reading_note": "Spans marked unobserved are missing scan image — digital silence with no film evidence. "
                        "Do not read them as captured soundtrack.",
    }
    return store.save_excerpt(record)
