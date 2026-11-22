"""Estimate lateral carrier motion through an observed join."""
import numpy as np
from PIL import ImageDraw
from scipy import ndimage

from .art import base_rgba
from .film import GRID, HIGHLIGHT, WARNING, local_evidence
from .model import InputError


def find_carrier_edges(gray, support, box, cutoff):
    sample=gray[box.slices]
    visible=support[box.slices]
    smooth=ndimage.median_filter(sample, size=(3,1),mode="nearest")
    active=(smooth>=cutoff)&visible
    top_band=max(3,int(box.height*.35))
    bottom_band=min(box.height-3,int(box.height*.65))
    enough=np.count_nonzero(visible,axis=0)>=box.height*.65
    hit_top=np.any(active[:top_band],axis=0)
    hit_bottom=np.any(active[bottom_band:],axis=0)
    top_edges=np.argmax(active[:top_band],axis=0)+box.top
    bottom_edges=box.bottom-1-np.argmax(active[bottom_band:][::-1],axis=0)
    # Require known scanner bed on the outside of each measured edge.
    offsets=np.arange(box.width)
    top_support=visible[np.clip(top_edges-box.top-1,0,box.height-1),offsets]
    bottom_support=visible[np.clip(bottom_edges-box.top+1,0,box.height-1),offsets]
    left=hit_top & enough & top_support & (top_edges>box.top+1)
    right=hit_bottom & enough & bottom_support & (bottom_edges<box.bottom-2)
    return top_edges,bottom_edges,left,right


def fit_side(positions,values,measured,nominal):
    choice=measured.copy()
    if choice.sum()<7:
        return None
    for _ in range(3):
        x=positions[choice]-nominal
        y=values[choice]+.5
        if len(x)<7 or np.ptp(x)<5:
            return None
        slope,intercept=np.polynomial.polynomial.polyfit(x,y,1)[::-1]
        residual=values+.5-(slope*(positions-nominal)+intercept)
        center=np.median(residual[choice])
        jitter=1.4826*np.median(np.abs(residual[choice]-center))
        choice=measured & (np.abs(residual-center)<=max(1.5,3*jitter))
    spread=np.sqrt(np.mean(residual[choice]**2)) if choice.any() else 0
    return {"slope":float(slope),"at_join_px":float(intercept),"rms_px":float(spread),"columns":int(choice.sum())}


def position_description(left,right,frame):
    units=frame.scale[1] if frame.scale else 1
    compare=(right["at_join_px"]-left["at_join_px"]) if left and right else None
    return {
        "before":left,"after":right,
        "jump":round(compare*units,5) if compare is not None else None,
        "jump_px":round(compare,4) if compare is not None else None,
        "unit":"mm" if frame.scale else "px",
    }


def draw_fit(draw, fit, interval, center):
    if not fit:
        return
    segment=[(x,fit["at_join_px"]+fit["slope"]*(x-center)) for x in interval]
    draw.line(segment,fill=HIGHLIGHT,width=2)


def transport_join(frame, linear, area, options):
    gray,support,box=local_evidence(frame,linear,area)
    upper,lower,top_known,bottom_known=find_carrier_edges(gray,support,box,options["carrier_cutoff"])
    width,height=frame.extent
    center=round(options["join_fraction"]*width)
    isolation=options["exclude_width_px"]
    half=round(options["fit_fraction"]*box.width)
    positions=np.arange(box.left,box.right)
    left=(positions>=center-isolation-half)&(positions<center-isolation)
    right=(positions<=center+isolation+half)&(positions>center+isolation)
    if min(np.count_nonzero(left),np.count_nonzero(right))<7:
        raise InputError("Keep space on both sides of the join mark inside the selected scan area.")
    image=base_rgba(frame,linear)
    draw=ImageDraw.Draw(image)
    draw.line((center,box.top,center,box.bottom-1),fill=WARNING,width=2)
    records=[]
    for label,edge,valid in (("Upper",upper,top_known),("Lower",lower,bottom_known)):
        before=fit_side(positions,edge,valid&left,center)
        after=fit_side(positions,edge,valid&right,center)
        entry=position_description(before,after,frame)
        for current,side in ((before,left),(after,right)):
            sequence=positions[side]
            draw_fit(draw,current,(int(sequence[0]),int(sequence[-1])),center)
        entry.update({
            "edge":label,
            "before_support_percent":round(100*float(np.mean(valid[left])),2),
            "after_support_percent":round(100*float(np.mean(valid[right])),2),
            "slope_change_deg":round(float(np.degrees(np.arctan(after["slope"])-np.arctan(before["slope"]))),5) if before and after else None,
        })
        records.append(entry)
    valid_pairs=[x["jump_px"] for x in records if x["jump_px"] is not None]
    if not valid_pairs:
        raise InputError("No carrier edge is clearly measurable just before and after the join.")
    draw.rectangle(box.outline(),outline=GRID,width=1)
    separation=(
        records[1]["after"]["at_join_px"]-records[0]["after"]["at_join_px"]
        - records[1]["before"]["at_join_px"]+records[0]["before"]["at_join_px"]
        if len(valid_pairs)==2 else None
    )
    value={
        "join_position_px":center,
        "join_position_fraction":round(center/width,6),
        "measured_edges":len(valid_pairs),
        "fit_window_px":half,
        "translation_px":round(float(np.mean(valid_pairs)),5),
        "width_change_px":round(float(separation),5) if separation is not None else None,
        "rows":records,
        "note":"Fits the observed carrier edges outside a small neighborhood of the marked splice. It estimates lateral alignment and width change, without declaring the splice safe for a particular transport.",
    }
    return value,image,None
