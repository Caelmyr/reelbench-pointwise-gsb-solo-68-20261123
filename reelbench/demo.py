from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from PIL import Image
from scipy import ndimage

from .geometry import DEFAULT_CORNERS, MIN_SUPPORT_ALPHA, canvas_dimensions, homography
from .photos import linear_to_srgb, png_bytes, srgb_to_linear, uint8_image
from .store import make_id
from .photos import store_capture


DEMO_KEY = "reelbench.training.v2"
PLANE_SIZE = (712, 408)
PHOTO_SIZE = (912, 538)
DEMO_ROLES = ("diffuse", "parallel", "cross", "rake-left", "rake-right")
POSES = {
    "diffuse": [[0.092, 0.086], [0.911, 0.112], [0.894, 0.912], [0.081, 0.889]],
    "parallel": [[0.122, 0.107], [0.910, 0.084], [0.925, 0.871], [0.101, 0.916]],
    "cross": [[0.079, 0.101], [0.908, 0.119], [0.877, 0.916], [0.081, 0.909]],
    "rake-left": [[0.089, 0.094], [0.894, 0.088], [0.902, 0.905], [0.064, 0.911]],
    "rake-right": [[0.094, 0.095], [0.913, 0.094], [0.926, 0.906], [0.112, 0.893]],
}


@dataclass(frozen=True)
class DemoSpecimen:
    seed: int
    name: str
    body: str
    glaze: str
    firing: str
    batch: str
    colors: tuple


SERIES = (
    DemoSpecimen(173, "Harbor crossing · R12", "Acetate", "Orange-mask negative", "Flatbed A", "Reel 12",
                ((57, 27, 20), (123, 76, 54), (203, 153, 102))),
    DemoSpecimen(284, "Workshop diary · R03", "Polyester", "Warm print", "Flatbed B", "Reel 03",
                ((39, 25, 22), (101, 72, 64), (188, 151, 128))),
    DemoSpecimen(391, "Parade route · R06", "Acetate", "Neutral print", "Flatbed A", "Reel 06",
                ((24, 29, 27), (94, 104, 91), (185, 191, 178))),
)

LEADER = DemoSpecimen(519, "Festival leader · R21", "Acetate", "Neutral print", "Flatbed B", "Reel 21",
                      ((36, 33, 30), (112, 106, 97), (198, 192, 181)))
LEADER_KEY = "reelbench.training.leader.v1"
LEADER_POSES = {
    "head": [[0.094, 0.088], [0.909, 0.111], [0.896, 0.909], [0.083, 0.892]],
    "tail": [[0.103, 0.096], [0.904, 0.085], [0.917, 0.903], [0.096, 0.914]],
}


def soft_band(x, low, high, edge):
    return np.clip((x - low) / edge + .5, 0, 1) * np.clip((high - x) / edge + .5, 0, 1)


def leader_coverage(key):
    def coverage(u, v):
        if key == "head":
            return soft_band(u, .030, .615, .016)
        return soft_band(u, .448, .984, .016) * (1 - soft_band(u, .755, .789, .005))
    return coverage


def random_field(generator, size, y_space, x_space):
    height, width = size
    rows, cols = int(np.ceil(height / y_space)) + 4, int(np.ceil(width / x_space)) + 4
    coarse = generator.normal(size=(rows, cols)).astype(np.float32)
    y, x = np.indices(size, dtype=np.float32)
    return ndimage.map_coordinates(coarse, (y / y_space + 1, x / x_space + 1), order=3).astype(np.float32)


@dataclass(frozen=True)
class FilmField:
    transmitted: np.ndarray
    carrier: np.ndarray
    residue: np.ndarray
    relief: np.ndarray
    u: np.ndarray
    v: np.ndarray


def picture_density(x, y, gate, generator):
    horizon = 0.54 + .06 * np.cos(4*x + gate)
    water = np.maximum(y - horizon, 0)
    roofline = 0.46 + 0.06 * np.floor((x * 5 + gate) % 5) / 5
    structures = (x > .15) & (x < .83) & (y > roofline) & (y < horizon)
    masts = ((x * 8 + gate*.17) % 1 < .035) & (y > .25) & (y < horizon)
    clouds = .12 * np.cos(9*x + 6*y + gate) + .10 * np.sin(7*y - 2*x)
    river = .12 * np.sin(104 * water + 5 * x) + .07 * np.cos(60*water + 31*x)
    ripple = np.where(water > 0, river, clouds)
    patches = (np.sin(49*x + 37*y) * np.cos(21*x - 44*y)) * .04
    return np.clip(.53 + ripple - .30 * structures - .24 * masts + patches + generator.normal(0,.015,x.shape), .06, .90)


def rounded_hole(x, y, center, lane, width, height):
    dx = np.abs(x-center)-width*.35
    dy = np.abs(y-lane)-height*.34
    radius = min(width, height)*.16
    return np.hypot(np.maximum(dx, 0), np.maximum(dy, 0)) + np.minimum(np.maximum(dx,dy),0) < radius


def bake_field(specimen, hole_cy=.774, hole_wobble=.006):
    width, height = PLANE_SIZE
    generator = np.random.default_rng(specimen.seed)
    yr, xr = np.indices((height,width), dtype=np.float32)
    x, y = xr/width, yr/height
    carrier = (y > .143 + .006*np.sin(8*x)) & (y < .834 + .003*np.cos(12*x)) & (x > .037) & (x < .965)
    base = np.array([.44, .32, .21]) if specimen.seed == 173 else np.array([.35,.325,.29])
    color = np.broadcast_to(base, (*x.shape,3)).astype(np.float32).copy()
    rough = random_field(generator, x.shape, 10, 12)
    grain = generator.normal(size=x.shape).astype(np.float32)
    color += (.009*rough + .006*grain)[...,None]
    color[~carrier] = [.045,.049,.048]
    count=16
    for step,cx in enumerate(np.linspace(.061,.939,count)):
        for lane, cy in enumerate((.21,hole_cy)):
            if lane==0 and step==6:
                continue
            width_jitter = .0145*(1 + .09*np.sin(step*2.1 + lane))
            height_jitter = .051*(1+.08*np.cos(step+lane))
            hole = rounded_hole(x,y,cx+.002*np.sin(step*.7),cy+hole_wobble*np.sin(11*cx),width_jitter,height_jitter)
            color[hole & carrier] = [.795,.795,.755] + (.013*grain[hole & carrier,None])
    stops = [.12,.5,.91]
    palette=srgb_to_linear(np.array(specimen.colors,dtype=np.float32)/255)
    for gate,(left,right) in enumerate(zip((.083,.256,.431,.606,.781),(.242,.418,.590,.767,.932))):
        in_picture=(x>left)&(x<right)&(y>.279)&(y<.677)
        signal=picture_density((x-left)/(right-left), (y-.279)/.398, gate, generator)
        shade=np.stack([np.interp(signal,stops,palette[:,j]) for j in range(3)],axis=2)
        color[in_picture]=shade[in_picture]
    matte_y=.708 + .003*np.sin(12*x)
    width_y=.009+.004*np.sin(130*x + 1.6*np.sin(29*x))+.0025*np.sin(224*x)
    lane = (np.abs(y-matte_y) < .022)&(x>.059)&(x<.944)
    modulation=lane & (np.abs(y-matte_y)<width_y)
    color[lane]=[.045,.044,.040]
    color[modulation]=[.60,.54,.44]
    dropout=(x>.694)&(x<.715)&lane
    color[dropout]=[.036,.038,.036]
    tag = carrier & (x>.091)&(x<.196)&(y>.241)&(y<.257)
    thin_print = tag & (np.mod(x*142,1)<.31)
    color[thin_print]=[.058,.054,.042]
    seam = (np.abs(x-(.584+.013*(y-.47)))<.0028)&carrier
    color[seam]=[.54,.50,.42]
    scratch = (np.abs(y-(.42+.0022*np.sin(21*x)))<.0028)&(x>.16)&(x<.76)
    color[scratch]=[.65,.62,.57]
    stray=np.zeros(x.shape,dtype=bool)
    for index in range(43):
        cx,cy=generator.uniform((.12,.30),(.88,.64))
        rx,ry=generator.uniform(.0025,.007,2)
        dirt=((x-cx)/rx)**2+((y-cy)/ry)**2 < 1
        stray|=dirt
        color[dirt]*=.19 if index%3 else 1.65
    control=(x>.827)&(x<.919)&(y>.874)&(y<.939)
    color[control]=[.191,.175,.162] + .003*grain[control,None]
    marks = np.zeros(x.shape,dtype=bool)
    for xx in (.065,.935):
        for yy in (.105,.889):
            marks |= (abs(x-xx)<.003)&(abs(y-yy)<.019)
            marks |= (abs(x-xx)<.012)&(abs(y-yy)<.003)
    color[marks]=[.16,.63,.58]
    relief=.02*rough + .12*ndimage.gaussian_filter(stray.astype(float),1.3)+.16*seam+.14*scratch
    return FilmField(np.clip(color,0,1), carrier, (stray | scratch | seam), relief, x, y)


def light_field(field, role):
    x, y = field.u, field.v
    dy, dx = np.gradient(field.relief)
    broad=.95+.08*(1-y)-.05*(x-.5)**2
    if role in ("rake-left","rake-right"):
        direction=1 if role=="rake-left" else -1
        grazing=np.clip(1+4.8*direction*dx-1.5*dy,.34,1.6)
        source=field.transmitted * (.72 + .035*direction*(x-.5))[:, :, None]
        source *= grazing[...,None]
    else:
        source=field.transmitted * (broad * (.89 if role == "cross" else 1.0))[...,None]
        reflection=np.exp(-(((x-.68)/.19)**2+((y-.37)/.16)**2))*.18
        if role=="parallel":
            source=source + (reflection*field.carrier + .045*field.residue)[...,None]
    return np.clip(source,0,1)


def desk_background(width, height, seed):
    generator=np.random.default_rng(seed+999)
    noise=.01 * random_field(generator,(height,width),17,21)
    y,x=np.indices((height,width),dtype=np.float32)
    velvet=.022+.032*np.exp(-((x/width-.4)**2+(y/height-.31)**2)*5)+noise
    return np.maximum(velvet,.012)[...,None] * np.array([.64,.77,.85])


def capture_alpha(x, y):
    horizontal=np.abs(x-.5)-.476
    vertical=np.abs(y-.5)-.459
    round_edge=np.hypot(np.maximum(horizontal,0),np.maximum(vertical,0))
    inner=np.minimum(np.maximum(horizontal,vertical),0)
    return np.clip(.5-(round_edge+inner-.021)*410,0,1)


def warp_to_photo(array, corners, photo_size, seed, coverage=None):
    width, height = photo_size
    photo_to_tile = np.linalg.inv(homography(DEFAULT_CORNERS, corners))
    photo_y, photo_x = np.indices((height, width), dtype=np.float32)
    photo_y = (photo_y + 0.5) / height
    photo_x = (photo_x + 0.5) / width
    denominator = photo_to_tile[2, 0] * photo_x + photo_to_tile[2, 1] * photo_y + photo_to_tile[2, 2]
    tile_u = (photo_to_tile[0, 0] * photo_x + photo_to_tile[0, 1] * photo_y + photo_to_tile[0, 2]) / denominator
    tile_v = (photo_to_tile[1, 0] * photo_x + photo_to_tile[1, 1] * photo_y + photo_to_tile[1, 2]) / denominator
    tile_h, tile_w = array.shape[:2]
    coords = (tile_v * tile_h - 0.5, tile_u * tile_w - 0.5)
    tile_image = np.stack(
        [ndimage.map_coordinates(array[:, :, component], coords, order=1, mode="nearest") for component in range(3)],
        axis=2,
    )
    alpha = capture_alpha(tile_u, tile_v)
    if coverage is not None:
        alpha = alpha * coverage(tile_u, tile_v)
    shadow = ndimage.shift(ndimage.gaussian_filter(alpha, 7), (9, 5), order=1, mode="constant")
    stage = desk_background(width, height, seed)
    stage *= (1 - 0.61 * shadow)[:, :, None]
    rim = np.clip(alpha - ndimage.minimum_filter(alpha, size=8), 0, 1)
    tile_image *= 1 - 0.24 * rim[:, :, None]
    mixed = stage * (1 - alpha[:, :, None]) + tile_image * alpha[:, :, None]
    return linear_to_srgb(mixed), alpha


def render_photo(field, role, specimen, corners=None, coverage=None):
    linear = light_field(field, role)
    image, alpha = warp_to_photo(linear, corners or POSES[role], PHOTO_SIZE, specimen.seed, coverage)
    if coverage is None:
        array = np.rint(np.clip(image, 0, 1) * 255).astype(np.uint8)
        return Image.fromarray(array)
    return uint8_image(np.clip(image, 0, 1), alpha)


def rectangle(name, purpose, left, top, right, bottom):
    return {
        "id": make_id(), "name": name, "purpose": purpose,
        "vertices": [[left, top], [right, top], [right, bottom], [left, bottom]],
    }


def training_regions():
    return [
        rectangle("Scanned carrier", "inspection", .041, .151, .951, .823),
        rectangle("Picture window", "inspection", .093, .281, .919, .675),
        rectangle("Optical sound lane", "inspection", .062, .691, .940, .725),
        rectangle("Clear base", "bare", .074, .250, .125, .272),
        rectangle("Dense frame leader", "glazed", .591, .527, .605, .610),
        rectangle("Scanner neutral patch", "neutral", .838, .883, .910, .927),
    ]


def training_specimen(store, specimen):
    field = bake_field(specimen)
    photos = []
    for role in DEMO_ROLES:
        image = render_photo(field, role, specimen)
        photo = store_capture(
            store.blobs, png_bytes(image), role,
            f"{specimen.name.lower().replace(' ', '-')}-{role}.png",
            "Synthetic scan of a film strip, perforation rows, picture gates and a variable-area sound track.",
            make_id(),
            synthetic=True,
        )
        photos.append(photo)
    base = photos[0]
    size = canvas_dimensions(base, POSES["diffuse"], 152, 87.1)
    return {
        "name": specimen.name,
        "batch": specimen.batch,
        "body": specimen.body,
        "glaze": specimen.glaze,
        "firing": specimen.firing,
        "notes": "Synthetic film scan. Perforation rows, frame gates, optical track, dust and a splice are visible.",
        "width_mm": 152,
        "height_mm": 87.1,
        "synthetic": True,
        "photos": photos,
        "plane": {"photo_id": base["id"], "corners": POSES["diffuse"], "size": list(size)},
        "regions": training_regions(),
        "registrations": {
            photo["id"]: {"base_photo_id": base["id"], "corners": POSES[photo["role"]], "support_percent": 100}
            for photo in photos[1:]
        },
    }


def leader_specimen(store):
    field = bake_field(LEADER, hole_cy=.742, hole_wobble=.002)
    photos = []
    notes = {
        "head": "First pass of the leader; clean at the start, stops before the tail.",
        "tail": "Second pass of the leader; reaches the end, skips one band.",
    }
    for key in ("head", "tail"):
        image = render_photo(field, "diffuse", LEADER, LEADER_POSES[key], leader_coverage(key))
        photos.append(store_capture(
            store.blobs, png_bytes(image), "diffuse",
            f"festival-leader-r21-scan-{key}.png", notes[key], make_id(), synthetic=True,
        ))
    base, other = photos
    size = canvas_dimensions(base, LEADER_POSES["head"], 152, 87.1)
    samples = np.linspace(0, 1, 400)
    uu, vv = np.meshgrid(samples, samples)
    tail_support = float(np.mean(capture_alpha(uu, vv) * leader_coverage("tail")(uu, vv) >= MIN_SUPPORT_ALPHA))
    return {
        "name": LEADER.name,
        "batch": LEADER.batch,
        "body": LEADER.body,
        "glaze": LEADER.glaze,
        "firing": LEADER.firing,
        "notes": "Synthetic leader scanned twice. Both passes share the same perforations and variable-area "
                 "track; the first pass is clean at the head, the second reaches the tail, and one short band "
                 "is photographed by neither.",
        "width_mm": 152,
        "height_mm": 87.1,
        "synthetic": True,
        "photos": photos,
        "plane": {"photo_id": base["id"], "corners": LEADER_POSES["head"], "size": list(size)},
        "regions": [
            rectangle("Scanned carrier", "inspection", .041, .151, .951, .823),
            rectangle("Optical sound lane", "inspection", .062, .684, .940, .740),
        ],
        "registrations": {
            other["id"]: {
                "base_photo_id": base["id"],
                "corners": LEADER_POSES["tail"],
                "support_percent": round(100 * tail_support, 2),
            },
        },
    }


def load_training(store):
    tiles = []
    created = False
    existing = store.seeded_tiles(DEMO_KEY)
    if existing:
        tiles.extend(existing)
    else:
        rows = [training_specimen(store, specimen) for specimen in SERIES]
        tiles.extend(store.seed_tiles(DEMO_KEY, rows))
        created = True
    leader = store.seeded_tiles(LEADER_KEY)
    if leader:
        tiles.extend(leader)
    else:
        tiles.extend(store.seed_tiles(LEADER_KEY, [leader_specimen(store)]))
        created = True
    return tiles, created


@lru_cache(maxsize=1)
def preview_photo():
    specimen = SERIES[0]
    rendered = render_photo(bake_field(specimen), "diffuse", specimen)
    return png_bytes(rendered)
