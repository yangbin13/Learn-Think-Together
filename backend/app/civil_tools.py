"""Bounded engineering calculations; SI inputs and explicit reference points.

These tools calculate the supplied model. They cannot validate a drawing, choose
supports, or certify a structure. No material strength or design safety inference.
"""
from __future__ import annotations
import math


def finite(value, name, *, positive=False, nonnegative=False, limit=1e12):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value)>limit:
        raise ValueError(f'{name} must be a finite number within ±{limit:g}')
    if positive and value<=0 or nonnegative and value<0:
        raise ValueError(f'{name} has an invalid sign')
    return float(value)


def signed_compound_centroid(components, reference=None, length_unit='m'):
    """Signed areas: positive solids, negative holes, common x/y origin and units."""
    if not isinstance(components,list) or not 1<=len(components)<=100:
        raise ValueError('components must contain 1–100 signed area/centroid components')
    if not isinstance(length_unit,str) or not 1<=len(length_unit)<=20:
        raise ValueError('length_unit must be a short common coordinate unit')
    parsed=[]
    for item in components:
        if not isinstance(item,dict) or set(item)-{'area','x','y','label'} or not {'area','x','y'}<=set(item):
            raise ValueError('each component requires area, x, y and optional label')
        area=finite(item['area'],'area')
        if area==0:raise ValueError('component area cannot be zero')
        x=finite(item['x'],'x',limit=1e9);y=finite(item['y'],'y',limit=1e9)
        label=item.get('label','')
        if not isinstance(label,str) or len(label)>80:raise ValueError('label must be short text')
        parsed.append({'area':area,'x':x,'y':y,'label':label})
    area=math.fsum(c['area'] for c in parsed)
    if not any(c['area']>0 for c in parsed) or area<=0:raise ValueError('remaining area must be positive')
    sx=math.fsum(c['area']*c['x'] for c in parsed)
    sy=math.fsum(c['area']*c['y'] for c in parsed)
    x,y=sx/area,sy/area
    if not math.isfinite(x) or not math.isfinite(y):raise ValueError('centroid overflows')
    shift=None
    if reference is not None:
        if not isinstance(reference,dict) or set(reference)!={'x','y'}:raise ValueError('reference requires x and y')
        rx=finite(reference['x'],'reference.x',limit=1e9);ry=finite(reference['y'],'reference.y',limit=1e9)
        dx,dy=x-rx,y-ry
        eps=max(1,abs(rx),abs(ry),abs(x),abs(y))*1e-12
        shift={'reference':{'x':rx,'y':ry},'dx':dx,'dy':dy,
               'x_direction':'right' if dx>eps else 'left' if dx < -eps else 'unchanged',
               'y_direction':'up' if dy>eps else 'down' if dy < -eps else 'unchanged'}
    return {'components':parsed,'total_signed_area':area,'sum_area_times_x':sx,'sum_area_times_y':sy,
            'centroid':{'x':x,'y':y},'shift_from_reference':shift,
            'units':{'coordinates':length_unit,'area':f'{length_unit}²'},
            'assumptions':['All components use one origin and common units.','Holes are negative areas; component geometry, overlap and containment are not checked.'],
            'verified_by':'Signed-area first moment conservation, math.fsum'}


def hydrostatic_vertical_plate(width,height,top_depth=0,density=1000,gravity=9.81,hinge_depth=None):
    """One submerged vertical rectangle, one-side gauge water pressure, SI units."""
    width=finite(width,'width',positive=True,limit=1e6)
    height=finite(height,'height',positive=True,limit=1e6)
    top_depth=finite(top_depth,'top_depth',nonnegative=True,limit=1e6)
    density=finite(density,'density',positive=True,limit=1e6)
    gravity=finite(gravity,'gravity',positive=True,limit=1e3)
    area=width*height
    centroid_depth=top_depth+height/2
    centroidal_second_moment=width*height**3/12
    force=density*gravity*area*centroid_depth
    pressure_depth=centroid_depth+centroidal_second_moment/(area*centroid_depth)
    moment=None
    if hinge_depth is not None:
        hinge_depth=finite(hinge_depth,'hinge_depth',limit=1e6)
        arm=abs(hinge_depth-pressure_depth)
        moment={'reference_depth_from_free_surface_m':hinge_depth,'vertical_lever_arm_m':arm,'magnitude_Nm':force*arm,
                'assumption':'Moment magnitude about the supplied point/axis perpendicular to the drawing; no support reactions are inferred.'}
    return {'inputs_SI':{'width_m':width,'height_m':height,'top_depth_m':top_depth,'density_kg_m3':density,'gravity_m_s2':gravity},
            'area_m2':area,'centroid_depth_from_free_surface_m':centroid_depth,
            'centroidal_second_moment_m4':centroidal_second_moment,'resultant_force_N':force,
            'center_pressure_depth_from_free_surface_m':pressure_depth,
            'center_pressure_distance_from_top_m':pressure_depth-top_depth,
            'center_pressure_distance_from_bottom_m':top_depth+height-pressure_depth,
            'moment_about_supplied_reference':moment,
            'assumptions':['Vertical rectangular plate, entirely submerged, one-sided static fluid gauge pressure.','No inclined plate, flow force, opposite-side water pressure or structural safety check.','When no reference point is supplied, no moment about a support is inferred.'],
            'verified_by':'F=ρgA h_centroid; h_pressure=h_centroid+I_centroid/(A h_centroid)'}
