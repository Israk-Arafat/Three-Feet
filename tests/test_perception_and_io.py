import cv2
import numpy as np
import pytest

from three_feet.perception.catchall import ZoneChangeDetector
from three_feet.perception.sam import parse_sam_output
from three_feet.perception.spark import HazardAssessment, conservative_level, merge_concepts
from three_feet.perception.wonder import rule_from_extraction
from three_feet.safety.actuator import DryRunPlug
from three_feet.sources import split_jpegs

# Sample output line from https://dev.meta.ai/docs/sam/reading-segmentation (320x334 source).
DOC_LINE = (
    "<0f>0<|box;x1=211;y1=228;x2=270;y2=254;w=320;h=334|><|mask;x=0;y=0;data=27,60,~!!!!M!0c[0o91w?q1!pIH4pPRVp2B3'7`e.ioeAf6-k/#Xd8%dX9x(|>"
    ",1<|box;x1=155;y1=228;x2=202;y2=254;w=320;h=334|><|mask;x=0;y=0;data=27,48,~!!!!J!0c[q=Pj_zs=*4C4(/./x#:/`S_GnD`=o3?X{emCgO$y@|>"
)


def test_parse_sam_doc_sample():
    dets, diags = parse_sam_output(DOC_LINE, (320, 334), "button")
    assert diags == []
    assert {d.object_id for d in dets} == {"0", "1"}
    d0 = next(d for d in dets if d.object_id == "0")
    assert d0.mask.shape == (334, 320)
    ys, xs = np.nonzero(d0.mask)
    assert xs.min() >= 211 and xs.max() <= 270 and ys.min() >= 228 and ys.max() <= 254
    assert d0.area > 1000


def test_parse_sam_rescales_downscaled_upload():
    # Same output, but pretend the source was 2x larger and uploaded at half scale.
    dets, _ = parse_sam_output(DOC_LINE, (640, 668), "button", scale=0.5)
    d0 = next(d for d in dets if d.object_id == "0")
    assert d0.mask.shape == (668, 640)
    assert d0.box[0] == 422


def test_parse_sam_zero_matches():
    dets, diags = parse_sam_output("", (320, 334))
    assert dets == [] and diags == []


def _jpeg(color):
    ok, buf = cv2.imencode(".jpg", np.full((8, 8, 3), color, np.uint8))
    return buf.tobytes()


def test_split_jpegs_handles_boundaries_and_partial_frames():
    a, b = _jpeg(10), _jpeg(200)
    buf = bytearray(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + a + b"\r\n--frame\r\n\r\n" + b[:20])
    assert split_jpegs(buf) == [a]
    buf.extend(b[20:] + b"\r\n")
    assert split_jpegs(buf) == [b]


def test_conservative_hazard():
    def a(flam, conf=0.9, clear=True):
        return HazardAssessment("x", "", flam, conf, "", clear)

    assert conservative_level(None) == "high"
    assert conservative_level(a("unknown")) == "high"
    assert conservative_level(a("low", conf=0.3)) == "high"
    assert conservative_level(a("low", clear=False)) == "high"
    assert conservative_level(a("medium")) == "medium"
    assert conservative_level(a("low")) == "low"


def test_merge_concepts():
    assert merge_concepts(["Fleece  blanket", "blanket", "a, b"], ["blanket", "towel"], 3) == [
        "Fleece blanket", "blanket", "towel"]


def test_rule_uses_stricter_value_and_defaults():
    r = rule_from_extraction({"found": True, "front_in": 36, "back_in": 12, "sides_in": 48,
                              "quote": "q", "page": 4}, minimum_in=36)
    assert (r.front_in, r.back_in, r.left_in, r.right_in) == (36, 36, 48, 48)
    assert r.source == "manual" and r.page == 4
    r = rule_from_extraction({"found": False}, minimum_in=36)
    assert (r.front_in, r.source) == (36, "default")


def test_dryrun_plug():
    plug = DryRunPlug(load_w=1500, settle_s=0)
    assert plug.status().power_w == 1500
    plug.turn_off()
    assert plug.status().power_w == 0 and plug.off_commands == 1
    plug.turn_on()
    assert plug.status().on is True and plug.status().power_w == 1500
    assert plug.on_commands == 1
    plug.stuck = True
    plug.simulate_power_on()
    plug.turn_off()
    assert plug.status().power_w == 1500
    plug.reachable = False
    assert plug.status().reachable is False
    with pytest.raises(ConnectionError):
        plug.turn_on()
    assert plug.on_commands == 1




def test_catchall_flags_unexplained_change_in_zone():
    zone = np.zeros((100, 100), bool)
    zone[20:80, 20:80] = True
    ref = np.full((100, 100, 3), 120, np.uint8)
    det = ZoneChangeDetector(zone, min_area_px=50)
    det.set_reference(ref)
    cur = ref.copy()
    cur[40:60, 40:60] = 250
    blobs = det.unknown_blobs(cur)
    assert len(blobs) == 1 and blobs[0].source == "catchall"
    explained = np.zeros((100, 100), bool)
    explained[40:60, 40:60] = True
    assert det.unknown_blobs(cur, explained) == []
