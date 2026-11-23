from .model import dictionary, enumeration, flag, identity, integer, numeric_pair, scalar


TOOLS = {
    "pores": {
        "label": "Dark opening topology",
        "description": "Map closed dark openings and candidates that reach an unreadable boundary.",
        "fields": {
            "dark_cutoff": {"kind": "number", "label": "Linear intensity cutoff", "default": .065,
                            "min": .005, "max": .45, "step": .005},
            "min_pixels": {"kind": "integer", "label": "Minimum dark footprint (px²)", "default": 7,
                           "min": 1, "max": 500},
        },
    },
    "weave": {
        "label": "Emulsion texture spacing",
        "description": "Estimate visible spacing and orientation in two approximately orthogonal directions on a painted area.",
        "fields": {
            "reference_axis": {"kind": "number", "label": "Approximate first lamina direction (degrees)", "default": 0, "min": 0, "max": 179, "step": 1},
            "tolerance": {"kind": "number", "label": "Search half-angle (degrees)", "default": 24, "min": 6, "max": 35, "step": 1},
            "minimum_pitch": {"kind": "number", "label": "Minimum spacing (px)", "default": 5, "min": 3, "max": 25, "step": 1},
            "maximum_pitch": {"kind": "number", "label": "Maximum spacing (px)", "default": 32, "min": 8, "max": 52, "step": 1},
        },
    },
    "dots": {
        "label": "Dark particle census",
        "description": "Measure isolated dark grains, their footprint and elongation on the registered film.",
        "fields": {
            "radius": {"kind": "integer", "label": "Matrix background radius (px)", "default": 8, "min": 3, "max": 28},
            "contrast": {"kind": "number", "label": "Minimum grain contrast", "default": .13, "min": .03, "max": .65, "step": .01},
            "minimum_area": {"kind": "integer", "label": "Minimum footprint (px²)", "default": 5, "min": 2, "max": 80},
            "maximum_area": {"kind": "integer", "label": "Maximum footprint (px²)", "default": 144, "min": 24, "max": 1600},
        },
    },
    "color": {
        "label": "Color variation",
        "description": "Map the distance from the median specimen color and compare local cells.",
        "fields": {"grid": {"kind": "integer", "label": "Grid across", "default": 5, "min": 2, "max": 10}},
    },
    "defects": {
        "label": "Dust and scratch observations",
        "description": "Find local spots and elongated marks, review them by location, and place them on the film or the glass with a second, turned scan.",
        "fields": {
            "sensitivity": {"kind": "number", "label": "Sensitivity", "default": 0.63, "min": 0, "max": 1, "step": 0.01},
            "spot_radius": {"kind": "number", "label": "Search radius px", "default": 5, "min": 1.2, "max": 32, "step": 0.1},
            "polarity": {"kind": "choice", "label": "Spot polarity", "default": "dark", "choices": ["dark", "light", "both"]},
            "minimum_area": {"kind": "integer", "label": "Minimum spot px", "default": 6, "min": 2, "max": 800},
            "maximum_area": {"kind": "integer", "label": "Maximum spot px", "default": 330, "min": 5, "max": 4500},
        },
    },
    "glare": {
        "label": "Polarized glare",
        "description": "Inspect the local intensity gained in parallel polarization on common visible pixels.",
        "capture_pair": ["parallel", "cross"],
        "fields": {
            "balance": {"kind": "choice", "label": "Exposure balance", "default": "auto", "choices": ["auto", "fixed"]},
            "fixed_gain": {"kind": "number", "label": "Fixed gain", "default": 1, "min": 0.2, "max": 5, "step": 0.05},
            "glare_fraction": {"kind": "number", "label": "Hotspot fraction", "default": 0.2, "min": 0.04, "max": 0.9, "step": 0.02},
            "minimum_hotspot": {"kind": "integer", "label": "Minimum hotspot px", "default": 30, "min": 3, "max": 5000},
            "balance_region": {"kind": "region", "label": "Exposure tie area", "default": None},
        },
    },
    "direction": {
        "label": "Raking light",
        "description": "Find fine marks that change sign when the light moves to the opposite side.",
        "capture_pair": ["rake-left", "rake-right"],
        "fields": {
            "balance": {"kind": "choice", "label": "Exposure balance", "default": "auto", "choices": ["auto", "fixed"]},
            "fixed_gain": {"kind": "number", "label": "Fixed gain", "default": 1, "min": 0.2, "max": 5, "step": 0.05},
            "scale_px": {"kind": "number", "label": "Shading radius px", "default": 8, "min": 2, "max": 30, "step": 0.5},
            "remove_broad": {"kind": "flag", "label": "Remove broad light tilt", "default": True},
            "strength_floor": {"kind": "number", "label": "Minimum contrast", "default": 0.04, "min": 0.01, "max": 0.3, "step": 0.01},
        },
    },
    "texture": {
        "label": "Surface texture",
        "description": "See local roughness and the direction and spacing of recurring texture.",
        "fields": {
            "background_radius": {"kind": "number", "label": "Background radius px", "default": 8, "min": 2, "max": 28, "step": 0.5},
            "edge_smooth": {"kind": "number", "label": "Edge smoothing px", "default": 1.2, "min": 0.5, "max": 5, "step": 0.1},
            "maximum_lag": {"kind": "integer", "label": "Maximum spacing px", "default": 20, "min": 4, "max": 48},
        },
    },
    "craze": {
        "label": "Fine scratch network",
        "description": "Trace dark hairlines and report loops and crossings.",
        "fields": {
            "radius": {"kind": "number", "label": "Line width radius px", "default": 1.4, "min": 0.6, "max": 5, "step": 0.1},
            "sensitivity": {"kind": "number", "label": "Line sensitivity", "default": 0.72, "min": 0, "max": 1, "step": 0.02},
            "join_px": {"kind": "integer", "label": "Bridge width px", "default": 1, "min": 0, "max": 4},
            "component_minimum": {"kind": "integer", "label": "Minimum trace px", "default": 12, "min": 2, "max": 150},
            "minimum_cell": {"kind": "integer", "label": "Minimum closed-cell px", "default": 24, "min": 5, "max": 1200},
        },
    },
    "rim": {
        "label": "Carrier margin color",
        "description": "Compare marked carrier edges and depth bands with the interior color.",
        "fields": {
            "band": {"kind": "number", "label": "Band depth", "default": 8, "min": 0.5, "max": 70, "step": 0.5},
            "unit": {"kind": "choice", "label": "Band unit", "default": "percent", "choices": ["percent", "mm"]},
            "interior_multiple": {"kind": "number", "label": "Interior inset band multiples", "default": 2, "min": 1.1, "max": 3.5, "step": 0.1},
            "depth_bins": {"kind": "integer", "label": "Depth slices", "default": 4, "min": 2, "max": 9},
            "highlight_delta": {"kind": "number", "label": "Highlight from interior ΔE", "default": 13, "min": 3, "max": 45, "step": 1},
        },
    },
    "profile": {
        "label": "Line scan",
        "description": "Draw across a stripe or a surface boundary to inspect a line of color values.",
        "fields": {
            "start": {"kind": "point", "label": "Start", "default": [0.14, 0.37]},
            "end": {"kind": "point", "label": "End", "default": [0.86, 0.64]},
            "samples": {"kind": "integer", "label": "Stations", "default": 105, "min": 12, "max": 360},
            "band_pixels": {"kind": "number", "label": "Band width px", "default": 5, "min": 0, "max": 32, "step": 1},
        },
    },
    "reference": {
        "label": "Reference match",
        "description": "Compare this specimen with one frozen approved visual sample.",
        "fields": {
            "reference_id": {"kind": "reference", "label": "Approved visual reference", "default": None},
        },
    },
}


PATH_FIELDS = {
    "start": {"kind": "point", "label": "Start", "default": [.13, .48]},
    "end": {"kind": "point", "label": "End", "default": [.86, .48]},
    "samples": {"kind": "integer", "label": "Stations", "default": 82, "min": 10, "max": 210},
    "radius": {"kind": "integer", "label": "Sample half width px", "default": 16, "min": 4, "max": 54},
    "contrast": {"kind": "number", "label": "Signal threshold", "default": .055, "min": .005, "max": .6, "step": .005},
}
TOOLS.update({
    "marking": {"label": "Edge mark visibility", "description": "Read light edge marks and intervening lost support along a marked strip.", "fields": PATH_FIELDS},
    "joint": {"label": "Seam contrast", "description": "Read both sides of one film splice separately.", "fields": PATH_FIELDS},
    "aggregate": {"label": "Light speck candidates", "description": "Locate small clusters of brightness relative to the surrounding film.", "fields": {
        "radius": {"kind": "integer", "label": "Background radius px", "default": 6, "min": 2, "max": 22},
        "contrast": {"kind": "number", "label": "Signal threshold", "default": .018, "min": .003, "max": .35, "step": .001},
        "minimum": {"kind": "integer", "label": "Minimum cluster px", "default": 4, "min": 1, "max": 800},
        "maximum": {"kind": "integer", "label": "Maximum cluster px", "default": 155, "min": 3, "max": 3500},
    }},
})


def number_field(label, default, lower, upper, step):
    return {"kind":"number","label":label,"default":default,"min":lower,"max":upper,"step":step}


def integer_field(label, default, lower, upper):
    return {"kind":"integer","label":label,"default":default,"min":lower,"max":upper}


def polarity_field(default):
    return {"kind":"choice","label":"Image polarity","choices":["bright","dark"],"default":default}


TOOLS.update({
    "perforation": {
        "label":"Perforation pitch",
        "description":"Find visible openings in both carrier edges and mark plausible interrupted pitches.",
        "fields":{
            "cutoff":number_field("Opening cutoff",.64,.01,.92,.005),
            "polarity":polarity_field("bright"),
            "lane_fraction":number_field("Edge band as share of region",.19,.10,.34,.01),
            "minimum_area":integer_field("Minimum opening area (px²)",30,6,2000),
        },
    },
    "gate": {
        "label":"Frame gate survey",
        "description":"Mark the picture strip, then locate frame spans from a single registered scan.",
        "fields":{
            "cutoff":number_field("Picture threshold",.215,.01,.90,.005),
            "polarity":polarity_field("dark"),
            "smooth_px":number_field("Column smoothing (px)",1.15,.50,7,.05),
            "edge_trim":number_field("Trim across the strip",.21,.03,.40,.01),
            "support_fraction":number_field("Minimum observed portion",.83,.35,1,.01),
            "minimum_fraction":number_field("Minimum frame span (fraction)",.07,.01,.30,.01),
        },
    },
    "sound": {
        "label":"Optical track diagnostic",
        "description":"Read variable-area modulation, visibly dark dropouts and missing scan evidence along a marked track.",
        "fields":{
            "clear_cutoff":number_field("Clear track cutoff",.22,.01,.92,.005),
            "min_support":number_field("Minimum support per column",.80,.20,1,.01),
            "minimum_dropout_px":integer_field("Minimum dropout span (px)",6,2,75),
            "speed_mm_s":number_field("Assumed transport speed (mm/s)",456,40,2500,1),
        },
    },
    "density": {
        "label":"Optical density map",
        "description":"Measure relative transmission with an observed clear-film reference and a scanner floor.",
        "fields":{
            "base_region":{"kind":"region","purpose":"bare","label":"Clear film base","default":None},
            "sensor_floor":number_field("Usable linear sensor floor",.018,.0005,.25,.0005),
            "samples":integer_field("Longitudinal sample cells",12,4,40),
        },
    },
    "transport": {
        "label":"Carrier alignment at splice",
        "description":"Read both edges before and after a manually positioned join mark, leaving unseen edges blank.",
        "fields":{
            "join_fraction":number_field("Splice position along the registered image",.584,.04,.96,.001),
            "carrier_cutoff":number_field("Carrier cutoff",.11,.02,.45,.005),
            "fit_fraction":number_field("Fit width on each side of the join",.11,.025,.39,.005),
            "exclude_width_px":integer_field("Ignore pixels on each side of the seam",13,2,75),
        },
    },
})


def parsed_option(key, field, raw):
    label = field["label"]
    kind = field["kind"]
    if kind == "integer":
        return integer(raw, label, lower=field["min"], upper=field["max"])
    if kind == "number":
        return scalar(raw, label, lower=field["min"], upper=field["max"])
    if kind == "choice":
        return enumeration(raw, field["choices"], label)
    if kind == "flag":
        return flag(raw, label)
    if kind == "point":
        return numeric_pair(raw, label, lower=0, upper=1)
    if kind == "region":
        return identity(raw, label) if raw else None
    if kind == "reference":
        return identity(raw, label)
    raise RuntimeError(f"Unknown measurement option {key}.")


def analysis_parameters(payload):
    raw = dictionary(payload, "Measurement")
    kind = enumeration(raw.get("kind"), TOOLS, "Measurement mode")
    raw_options = dictionary(raw.get("options", {}), "Measurement options")
    config = TOOLS[kind]
    options = {
        key: parsed_option(key, field, raw_options.get(key, field["default"]))
        for key, field in config["fields"].items()
    }
    return {
        "kind": kind,
        "options": options,
        "region_id": identity(raw["region_id"], "Sampling region") if raw.get("region_id") else None,
        "photo_id": identity(raw["photo_id"], "Photo ID") if raw.get("photo_id") else None,
        "comparison_id": identity(raw["comparison_id"], "Paired photo ID") if raw.get("comparison_id") else None,
        "apply_neutral": flag(raw.get("apply_neutral", True), "Apply card correction"),
    }
