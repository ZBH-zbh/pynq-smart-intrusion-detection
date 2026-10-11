"""Replay a recorded session through the real tracking layer and report.

Why a replay rather than another live run
-----------------------------------------
Scoring a change by walking in front of the camera again measures the change
AND the operator AND the lighting at once, which is how this project ended up
tuning against synthetic scenes it already passed. A recorded session is a
fixed input: run it before and after a change and the difference is the change.

Fidelity
--------
This drives the real ``TrackerPipeline`` -- the same class the live service
runs -- rather than a reimplementation, so the box filtering, the merging, the
trails and the crossing counter are all the shipped ones. Only the frame image
is faked, because the tracking layer does not read it; it is drawn on and the
drawing is discarded.

The recorded boxes are pre-filter, so the pipeline's own area filter runs here
exactly as it does live.

Usage
-----
    python replay_session.py E:\\fpga\\env\\sessions\\session_20260926_201127
    python replay_session.py <dir> --expect 10 --skip-startup 5
"""

import argparse
import os
import sys

import numpy as np

# The report is Chinese and this runs in whatever terminal the operator has.
# A legacy Windows code page turns every line of it into mojibake.
if os.name == "nt":
    os.system("chcp 65001 > nul")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "pynq_deploy")))

from session import Session, SessionError  # noqa: E402
from tracker_server import HEIGHT, WIDTH, TrackerPipeline  # noqa: E402

#: The values the service actually runs with, taken from its CLI defaults.
#:
#: These are NOT the TrackerPipeline constructor defaults, and using those was a
#: real bug in the first version of this harness: the class defaults min_speed
#: to 0.0 while the service runs --min-speed 4, so the replay under-filtered and
#: reported crossings the live configuration would never have produced. Any
#: number this harness prints has to come from the configuration being shipped.
LIVE_DEFAULTS = {
    "merge_gap": 0.0,             # MERGE_GAP
    "min_speed": 4.0,             # MIN_SPEED
    "prediction_weight": 0.3,     # PREDICTION_WEIGHT
    "max_box_fraction": 0.5,      # MAX_BOX_FRACTION
    "coast": 0,                   # COAST
    "crossing": True,
    "confirm_distance": 12.0,     # CONFIRM_DISTANCE
    "confirm_seconds": 1.0,       # CONFIRM_SECONDS
    "group_strips": True,         # NO_GROUP_STRIPS unset
}


def replay(session, pipeline_kwargs=None, skip_startup=0, limit=None):
    """Feed a session through a fresh pipeline.

    ``skip_startup`` drops the first frames. The frame-difference chain reports
    a whole-frame detection on its very first frame after the camera opens --
    observed as a single 320x240 box on frame 0 of a 12-second recording of an
    empty room -- and that is an artefact of the first difference, not a target.
    """
    pipeline = TrackerPipeline(**(pipeline_kwargs or {}))
    blank = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)

    frames = 0
    alarms = 0
    for index, _mask, boxes, centers, timestamp, fps in session.frames():
        if index < skip_startup:
            continue
        if limit is not None and frames >= limit:
            break
        if not boxes:
            # Still step, so tracks age out on the real clock rather than
            # freezing between detections.
            pipeline.step(blank, [], timestamp=timestamp, fps=fps,
                          centers=[])
            frames += 1
            continue
        _vis, _info, alarm = pipeline.step(
            blank, boxes, timestamp=timestamp, fps=fps, centers=centers)
        alarms += 1 if alarm else 0
        frames += 1
    return pipeline, frames, alarms


def report(session, pipeline, frames, alarms, expect=None, verbose=True):
    counter = pipeline.counter
    info = counter.info(recent=40) if counter is not None else {}
    labels = info.get("labels", ["A", "B"])
    counts = info.get("counts", [0, 0])
    total = info.get("total", 0)
    events = info.get("events", [])

    from collections import Counter
    outcomes = Counter(event["outcome"] for event in events)

    if verbose:
        print(f"会话      : {session.path}")
        print(f"头部      : {session.summary()}")
        print(f"重放帧数  : {frames}   报警帧: {alarms}")
        print(f"轨迹 ID   : {pipeline.tracker.total_ids} 个")
        grouper = getattr(pipeline, "grouper", None)
        if grouper is not None:
            grouping = grouper.info()
            print(f"对象分组  : 证明同物 {grouping['proven_pairs']} 对，"
                  f"证明不同物 {grouping['distinct_pairs']} 对，"
                  f"当前在跟 {grouping['tracked']} 条")
            if grouping["pairs"]:
                print(f"            同物轨迹对: "
                      + "  ".join(str(p) for p in grouping["pairs"]))
        print()
        print(f"越线判定  : {labels[0]}={counts[0]}  {labels[1]}={counts[1]}  "
              f"合计={total}")
        print(f"判定明细  : " + "  ".join(
            f"{name}={count}" for name, count in sorted(outcomes.items()))
            or "（无）")
        print()

    # The decisions themselves, oldest first: this is what makes a wrong count
    # explainable rather than just wrong.
    for event in events:
        mark = {"counted": "计数", "duplicate": "重复", "outside": "线段外",
                "degenerate": "退化"}.get(event["outcome"], event["outcome"])
        point = event.get("point")
        where = (f"({point[0]:.0f},{point[1]:.0f})" if point else "   -   ")
        print(f"    t={event['t']:>7.2f}  id={event['id']:>3}  "
              f"{event['label']:<4} {where:>11}  {mark:<4} "
              f"{event.get('detail') or ''}")

    if expect is not None:
        verdict = "通过" if total == expect else "不符"
        print()
        print(f"    期望 {expect} 次，实际 {total} 次 —— {verdict}")
        return {"total": total, "expected": expect, "ok": total == expect,
                "outcomes": dict(outcomes), "frames": frames}
    return {"total": total, "outcomes": dict(outcomes), "frames": frames}


def main():
    parser = argparse.ArgumentParser(description="replay a recorded session")
    parser.add_argument("session", help="session directory")
    parser.add_argument("--skip-startup", type=int, default=5,
                        help="drop the first N frames (default 5)")
    parser.add_argument("--expect", type=int, default=None,
                        help="expected number of crossings")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--merge-gap", type=float, default=LIVE_DEFAULTS["merge_gap"])
    parser.add_argument("--min-speed", type=float, default=LIVE_DEFAULTS["min_speed"])
    args = parser.parse_args()

    try:
        session = Session(args.session)
    except SessionError as exc:
        print(f"读取失败: {exc}")
        return 2

    pipeline, frames, alarms = replay(
        session,
        pipeline_kwargs=dict(LIVE_DEFAULTS,
                             merge_gap=args.merge_gap,
                             min_speed=args.min_speed),
        skip_startup=args.skip_startup,
        limit=args.limit,
    )
    report(session, pipeline, frames, alarms, expect=args.expect)
    return 0


if __name__ == "__main__":
    sys.exit(main())
