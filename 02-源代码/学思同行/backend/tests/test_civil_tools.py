import math
import pytest

from backend.app.civil_tools import signed_compound_centroid,hydrostatic_vertical_plate


def test_hole_direction_and_conservation():
    parts=[{'area':12,'x':2,'y':1.5},{'area':-1,'x':3,'y':2}]
    r=signed_compound_centroid(parts,reference={'x':2,'y':1.5})
    assert r['shift_from_reference']['x_direction']=='left'
    assert r['shift_from_reference']['y_direction']=='down'
    assert math.isclose(sum(c['area']*(c['x']-r['centroid']['x']) for c in parts),0,abs_tol=1e-12)
    assert math.isclose(sum(c['area']*(c['y']-r['centroid']['y']) for c in parts),0,abs_tol=1e-12)


def test_centroid_translation_and_scaling():
    p=[{'area':5,'x':-2,'y':3},{'area':3,'x':7,'y':-1}]
    r=signed_compound_centroid(p)['centroid']
    moved=signed_compound_centroid([{**c,'x':c['x']+20,'y':c['y']-6} for c in p])['centroid']
    scaled=signed_compound_centroid([{**c,'area':c['area']*9,'x':c['x']*3,'y':c['y']*3} for c in p])['centroid']
    assert moved==pytest.approx({'x':r['x']+20,'y':r['y']-6})
    assert scaled==pytest.approx({'x':r['x']*3,'y':r['y']*3})


@pytest.mark.parametrize('parts',[
    [],[{'area':-1,'x':0,'y':0}],[{'area':1,'x':0,'y':0},{'area':-1,'x':0,'y':0}],
    [{'area':0,'x':1,'y':1}],[{'area':True,'x':0,'y':0}],[{'area':1,'x':float('nan'),'y':0}],
    [{'area':'1','x':0,'y':0}],[{'area':1,'x':0,'y':0,'execute':'anything'}],
])
def test_centroid_rejects_invalid_model(parts):
    with pytest.raises(ValueError):signed_compound_centroid(parts)


def test_hydrostatics_reference_point_matters():
    r=hydrostatic_vertical_plate(2,3,hinge_depth=3)
    assert r['resultant_force_N']==pytest.approx(88290)
    assert r['center_pressure_depth_from_free_surface_m']==pytest.approx(2)
    assert r['center_pressure_distance_from_bottom_m']==pytest.approx(1)
    assert r['moment_about_supplied_reference']['magnitude_Nm']==pytest.approx(88290)
    # Incorrect placement at geometric centre gives a 1.5m arm about the bottom:
    # it overestimates this moment, so no unsupported 'too small/dangerous' claim.
    assert 88290*1.5>r['moment_about_supplied_reference']['magnitude_Nm']
    assert hydrostatic_vertical_plate(2,3)['moment_about_supplied_reference'] is None


def test_hydrostatic_scaling_and_deep_plate():
    a=hydrostatic_vertical_plate(2,3)
    b=hydrostatic_vertical_plate(4,3,density=2000)
    assert b['resultant_force_N']==pytest.approx(a['resultant_force_N']*4)
    assert b['center_pressure_depth_from_free_surface_m']==pytest.approx(a['center_pressure_depth_from_free_surface_m'])
    deep=hydrostatic_vertical_plate(2,3,top_depth=20)
    assert deep['centroid_depth_from_free_surface_m']<deep['center_pressure_depth_from_free_surface_m']<23


@pytest.mark.parametrize('kw',[
    {'width':0,'height':3},{'width':2,'height':-1},{'width':True,'height':3},
    {'width':2,'height':3,'top_depth':-1},{'width':2,'height':3,'density':0},
    {'width':2,'height':3,'gravity':float('inf')},{'width':2,'height':3,'hinge_depth':float('nan')},
])
def test_hydrostatic_rejects_invalid_inputs(kw):
    with pytest.raises(ValueError):hydrostatic_vertical_plate(**kw)
