"""Registered image measurements for archive film scans."""
from dataclasses import dataclass

import numpy as np
from PIL import ImageDraw
from scipy import ndimage

from .art import base_rgba, signed_overlay
from .geometry import analysis_mask, polygon_mask
from .model import InputError
from .photos import luminance


HIGHLIGHT = (116, 224, 202, 245)
WARNING = (240, 184, 95, 250)
GRID = (175, 194, 216, 225)
SHADOW = (19, 26, 30, 230)


@dataclass(frozen=True)
class Window:
    left: int
    right: int
    top: int
    bottom: int

    @property
    def slices(self):
        return slice(self.top, self.bottom), slice(self.left, self.right)

    @property
    def width(self):
        return self.right - self.left

    @property
    def height(self):
        return self.bottom - self.top

    def normalized(self, extent):
        width, height = extent
        return [self.left/width, self.top/height, self.right/width, self.bottom/height]

    def inset_y(self, lower, upper):
        return Window(
            self.left, self.right,
            self.top + round(lower*self.height),
            self.top + round(upper*self.height),
        )

    def outline(self):
        return [(self.left,self.top),(self.right-1,self.bottom-1)]


def usable_bounds(area, minimum_width=24, minimum_height=12):
    yy, xx = np.nonzero(area)
    if not len(xx):
        raise InputError("Choose a marked area with visible scan pixels.")
    bounds = Window(int(xx.min()),int(xx.max())+1,int(yy.min()),int(yy.max())+1)
    if bounds.width < minimum_width or bounds.height < minimum_height:
        raise InputError("The marked scan area is too small for this measurement.")
    return bounds


def local_evidence(frame, linear, area):
    evidence = analysis_mask(frame, area, minimum=48, allow_clipped=False)
    plane = luminance(linear)
    return plane, evidence, usable_bounds(area)


def contiguous(mask):
    edges = np.diff(np.pad(mask.astype(np.int8), (1,1)))
    return zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1))


def area_components(binary, measured, lane, minimum):
    labels, _ = ndimage.label(binary)
    objects = []
    close_to_unknown = ndimage.binary_dilation(~measured, iterations=1)
    for index, box in enumerate(ndimage.find_objects(labels),1):
        if box is None:
            continue
        region = labels[box] == index
        area = int(region.sum())
        height, width = region.shape
        if area < minimum or min(width,height)<3 or width > lane.width*.14 or height > lane.height*.82:
            continue
        if area/(height*width) < .56 or width/height < .22 or width/height > 5:
            continue
        yy, xx = np.nonzero(region)
        top = lane.top + box[0].start
        left = lane.left + box[1].start
        center = [left + float(np.mean(xx))+.5, top + float(np.mean(yy))+.5]
        touches = bool(np.any(close_to_unknown[box] & region)) or (
            box[0].start == 0 or box[1].start == 0
            or box[0].stop == lane.height or box[1].stop == lane.width
        )
        objects.append({
            "center":center,
            "bounds":Window(left,left+width,top,top+height),
            "pixels":area,
            "truncated":touches,
        })
    return sorted(objects,key=lambda item:item["center"][0])


def ideal_pitch(rows):
    gaps = []
    for holes in rows:
        positions = [hole["center"][0] for hole in holes if not hole["truncated"]]
        gaps.extend(delta for delta in np.diff(positions) if delta > 2)
    if len(gaps)<2:
        return None
    # The lower middle gap estimates one pitch even when a recorded row skips a hole.
    estimate = float(np.quantile(gaps,.38))
    in_family = [x for x in gaps if .65*estimate <= x <= 1.39*estimate]
    return float(np.median(in_family)) if in_family else None


def pitch_gaps(holes, pitch, evidence, lane):
    if pitch is None:
        return []
    valid = [hole["center"][0] for hole in holes if not hole["truncated"]]
    result = []
    for start,stop in zip(valid,valid[1:]):
        steps = int(np.rint((stop-start)/pitch))
        if steps<2 or steps>8 or abs((stop-start)/pitch-steps)>.26:
            continue
        for n in range(1,steps):
            x = start + (stop-start)*n/steps
            station = int(round(x))
            sample = evidence[lane.top:lane.bottom,max(lane.left,station-2):min(lane.right,station+3)]
            visible = sample.size > 0 and float(np.mean(sample)) >= .83
            result.append({"x_px":round(x,3),"state":"missing" if visible else "unobserved"})
    return result


def perforation_result(frame, linear, area, options):
    gray, evidence, bounds = local_evidence(frame,linear,area)
    portion = options["lane_fraction"]
    lanes = [bounds.inset_y(0,portion),bounds.inset_y(1-portion,1)]
    candidates = []
    cutoff = options["cutoff"]
    for lane in lanes:
        data = gray[lane.slices]
        visible = evidence[lane.slices]
        selected = (data >= cutoff) if options["polarity"]=="bright" else (data <= cutoff)
        candidates.append(area_components(selected & visible,visible,lane,options["minimum_area"]))
    pitch = ideal_pitch(candidates)
    overlay = base_rgba(frame,linear)
    draw = ImageDraw.Draw(overlay)
    width,height = frame.extent
    rows = []
    gaps = []
    for group,(lane,holes) in enumerate(zip(lanes,candidates),1):
        label = "Upper" if group == 1 else "Lower"
        draw.rectangle(lane.outline(),outline=GRID,width=1)
        for i,hole in enumerate(holes,1):
            rect = hole["bounds"]
            ink = WARNING if hole["truncated"] else HIGHLIGHT
            draw.rectangle(rect.outline(),outline=ink,width=2)
            x,y = hole["center"]
            rows.append({
                "lane":label,"number":i,"x_px":round(x,4),"y_px":round(y,4),
                "x_fraction":round(x/width,6),"y_fraction":round(y/height,6),
                "width_px":rect.width,"height_px":rect.height,"pixels":hole["pixels"],
                "condition":"partial" if hole["truncated"] else "open",
            })
        for gap in pitch_gaps(holes,pitch,evidence,lane):
            x=gap["x_px"]
            y=(lane.top+lane.bottom)/2
            draw.line([(x-4,y-4),(x+4,y+4)],fill=WARNING,width=2)
            draw.line([(x-4,y+4),(x+4,y-4)],fill=WARNING,width=2)
            gaps.append({"lane":label,**gap})
    stats = {
        "hole_count":len(rows),"partial_count":sum(item["condition"]=="partial" for item in rows),
        "visible_gaps":sum(item["state"]=="missing" for item in gaps),
        "unobserved_gaps":sum(item["state"]=="unobserved" for item in gaps),
        "pitch_px":round(pitch,4) if pitch else None,
        "pitch_mm":round(pitch*frame.scale[0],5) if pitch and frame.scale else None,
        "rows":rows,
        "gaps":gaps,
        "note":"Bright or dark openings in the two edge bands. Estimated gaps require several normal pitches and local image support.",
    }
    return stats,overlay,None


def column_aggregate(gray, support, bounds):
    region = gray[bounds.slices]
    measured = support[bounds.slices]
    counts = measured.sum(axis=0)
    values = np.zeros(bounds.width,dtype=np.float64)
    np.divide((region*measured).sum(axis=0),counts,out=values,where=counts>0)
    return values,counts/bounds.height


def visible_spans(valid):
    left = max(1,round(len(valid)*.015))
    return [(int(a),int(b)) for a,b in contiguous(valid) if b-a>=left]


def frame_gate_result(frame, linear, area, options):
    gray,evidence,bounds = local_evidence(frame,linear,area)
    band = bounds.inset_y(options["edge_trim"],1-options["edge_trim"])
    profile,support = column_aggregate(gray,evidence,band)
    smoothed = ndimage.gaussian_filter1d(profile,options["smooth_px"],mode="nearest")
    present = support >= options["support_fraction"]
    dark = smoothed < options["cutoff"]
    picture = present & dark if options["polarity"]=="dark" else present & ~dark
    minimum = max(6,round(options["minimum_fraction"]*bounds.width))
    spans = list(contiguous(picture))
    image = base_rgba(frame,linear)
    draw = ImageDraw.Draw(image)
    draws = []
    for left,right in spans:
        if right-left < minimum:
            continue
        run = Window(bounds.left+int(left),bounds.left+int(right),band.top,band.bottom)
        edge = left==0 or right==bounds.width or not present[max(0,left-1)] or not present[min(bounds.width-1,right)]
        key = len(draws)+1
        draw.rectangle(run.outline(),outline=WARNING if edge else HIGHLIGHT,width=2)
        draw.rounded_rectangle((run.left+3,run.top+3,run.left+26,run.top+22),3,fill=SHADOW)
        draw.text((run.left+7,run.top+5),str(key),fill=HIGHLIGHT)
        draws.append({
            "gate":key,"width_px":run.width,
            "width_mm":round(run.width*frame.scale[0],5) if frame.scale else None,
            "range":[round(run.left/frame.extent[0],6),round(run.right/frame.extent[0],6)],
            "roi":run.normalized(frame.extent),
            "condition":"partial" if edge else "bounded",
            "median":round(float(np.median(smoothed[left:right])),5),
        })
    zeros = [{"first_px":bounds.left+a,"last_px":bounds.left+b-1} for a,b in visible_spans(~present)]
    for row in zeros:
        draw.line([(row["first_px"],band.top),(row["last_px"],band.top)],fill=WARNING,width=4)
    metrics = {
        "frames":len(draws),"partial":sum(x["condition"]=="partial" for x in draws),
        "gap_count":len(zeros),"rows":draws,"unknown":zeros,
        "note":"Single scan, with the carrier left to right. Finds local picture ranges by a longitudinal density threshold. The preview does not assign reel numbers or fill cropped frames.",
        "profile": [round(float(v),5) if p else None for v,p in zip(smoothed[::max(1,len(smoothed)//288)],present[::max(1,len(smoothed)//288)])],
    }
    return metrics,image,None


def track_profile(gray, support, bounds, cutoff, min_support):
    data = gray[bounds.slices]
    available = support[bounds.slices]
    clear = (data > cutoff) & available
    count = available.sum(axis=0)
    good = count >= min_support*bounds.height
    sampled = np.divide(clear.sum(axis=0),count,out=np.zeros(bounds.width),where=count>0)
    active = sampled >= .07
    signal = good & active
    return sampled,good,signal


def sound_track_result(frame, linear, area, options):
    gray, evidence, bounds = local_evidence(frame,linear,area)
    if bounds.height > .30*bounds.width:
        raise InputError("Mark the optical lane as a narrow region along the carrier.")
    values,good,signal = track_profile(gray,evidence,bounds,options["clear_cutoff"],options["min_support"])
    image = base_rgba(frame,linear)
    draw = ImageDraw.Draw(image)
    draw.rectangle(bounds.outline(),outline=GRID,width=1)
    dropouts,missing = [], []
    width = frame.extent[0]
    for mask,key,rows in ((good & ~signal,"dark",dropouts),(~good,"unobserved",missing)):
        for left,right in contiguous(mask):
            if right-left < options["minimum_dropout_px"]:
                continue
            x0,x1 = bounds.left+int(left),bounds.left+int(right)
            draw.line([(x0,bounds.bottom-2),(x1-1,bounds.bottom-2)],fill=WARNING,width=3)
            rows.append({"state":key,"first_px":x0,"end_px":x1,
                         "x_fraction":[round(x0/width,6),round(x1/width,6)]})
    wave=[]; stride=max(1,int(np.ceil(bounds.width/580)))
    for left in range(0,bounds.width,stride):
        right=min(bounds.width,left+stride)
        usable=good[left:right]
        valid=int(usable.sum())
        value=round(float(np.mean(values[left:right][usable])),5) if usable.any() else None
        wave.append({"x_px":bounds.left+left,"value":value,"support":valid})
    chosen=values[signal]
    span=np.quantile(chosen,[.05,.95]).tolist() if len(chosen) else [None,None]
    duration=(bounds.width*frame.scale[0]/options["speed_mm_s"]) if frame.scale else None
    metrics = {
        "readable_percent":round(100*float(np.mean(good)),2),
        "dark_dropout_count":len(dropouts),"unobserved_count":len(missing),
        "range":[round(float(v),5) if v is not None else None for v in span],
        "duration_s":round(duration,6) if duration else None,"lane":bounds.normalized(frame.extent),
        "low_track_px":bounds.left,"source_stride_px":stride,
        "sound":[round(float(v),5) if valid else None for v,valid in zip(values,good)],
        "rows":wave,"dropouts":[*dropouts,*missing],
        "note":"Whole column modulation from a user-marked variable-area track. Dark records are visibly empty; unobserved records have insufficient pixels. This is a scan diagnostic, not a recovered soundtrack.",
    }
    return metrics,image,None


def density_reference(tile, frame, linear, region_id):
    choices=[region for region in tile["regions"] if region["purpose"]=="bare"]
    if region_id:
        choices=[region for region in choices if region["id"]==region_id]
    if len(choices)!=1:
        raise InputError("Mark one Clear film base region, or choose which one to measure.")
    choice=choices[0]
    area=polygon_mask(frame.extent,choice["vertices"])
    support=analysis_mask(frame,area,minimum=40,allow_clipped=False)
    control=np.median(linear[support],axis=0)
    if np.any(control<.025):
        raise InputError("The selected film base is too dense to serve as a transmission reference.")
    return choice,control,len(linear[support])


def optical_density(frame, tile, linear, area, options):
    source,support,bounds=local_evidence(frame,linear,area)
    choice,control,control_count=density_reference(tile,frame,linear,options["base_region"])
    floor=options["sensor_floor"]
    channels=np.maximum(linear,floor)
    densities=-np.log10(channels/control)
    known=support & np.all(linear>floor,axis=2)
    if np.count_nonzero(known)<30:
        raise InputError("Too little of this scan is within the readable transmission range.")
    weighted=-np.log10(np.maximum(source,floor)/float(luminance(control)))
    visible=weighted[known]
    center=float(np.median(visible))
    rows=[]
    xbounds=np.rint(np.linspace(bounds.left,bounds.right,options["samples"]+1)).astype(int)
    for i,(a,b) in enumerate(zip(xbounds,xbounds[1:]),1):
        mask=known[:,a:b]
        vals=weighted[:,a:b][mask]
        if len(vals):
            interval=np.quantile(vals,[.10,.50,.90])
            rows.append({"cell":i,"x_mid_px":float((a+b)/2),"samples":len(vals),
                         "p10":round(float(interval[0]),5),"median":round(float(interval[1]),5),"p90":round(float(interval[2]),5)})
        else:
            rows.append({"cell":i,"x_mid_px":float((a+b)/2),"samples":0,"p10":None,"median":None,"p90":None})
    scale=max(.12,float(np.quantile(np.abs(visible),.96)))
    image=signed_overlay(frame,weighted,known,scale,source=linear,unknown=area & ~known)
    channel_medians=np.median(densities[known],axis=0)
    metrics = {
        "median_density":round(center,5),
        "density_span":[round(float(i),5) for i in np.quantile(visible,[.05,.95])],
        "channel_medians":[round(float(i),5) for i in channel_medians],
        "unreadable_pixels":int(np.count_nonzero(area&~known)),"readable_pixels":int(np.count_nonzero(known)),
        "control":{"id":choice["id"],"label":choice["name"],"pixels":control_count,
                   "linear_rgb":[round(float(x),6) for x in control]},
        "rows":rows,
        "note":"Optical density relative to a photographed clear base. Below-floor or unsupported pixels stay unmeasured. Values are relative scanner readings, not spectrophotometer calibration.",
    }
    return metrics,image,{"mode":"signed","label":"Relative optical density","low":-scale,"high":scale,"midpoint":0}
