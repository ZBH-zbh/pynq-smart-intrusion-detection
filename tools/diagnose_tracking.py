"""Which detector actually locks onto a moving object, and at what speed?

The complaint is that tracking will not lock onto slow objects, will not lock
onto fast ones, and is unstable on the ones it does lock. ``/status`` reports
``raw_targets`` -- detections out of the detector, before the tracker sees them
-- as well as ``tracks``. If the raw count is zero, no tracker can help and the
question moves to the detector.

Frame differencing has a known speed window: too slow and the inter-frame
difference never crosses THRESH=30, too fast and the changed region can exceed
MAX_CONTOUR_AREA=50000 and be rejected outright. ``--detector bg`` (a
running-average background model) was built to remove the slow end of that
window. This measures both against the same scripted movements, so the answer is
a number rather than an impression.

Why there is a web page
-----------------------
The operator needs to know which movement to make right now, and whoever starts
this cannot see the terminal it prints to. So the countdown is served over HTTP
with a start button, which also decouples the run from whenever the operator
happens to open the page.
"""
import argparse
import json
import os
import statistics
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from deploy_tracker import connect, http_json, run, start  # noqa: E402

PAGE_PORT = 8090

# (start, end, instruction)
PHASES = [
    (0, 10, "准备 —— 站到摄像头前，手里拿个东西"),
    (10, 24, "什么都不要动（背景采集 / 让背景模型学习）"),
    (24, 44, "用物体或手【很慢】地移动 —— 慢到你觉得程序不可能看出来"),
    (44, 64, "【中速】移动 —— 正常走路或挥手的速度"),
    (64, 80, "【很快】地挥动 —— 越快越好，幅度大一点"),
    (80, 90, "停下，什么都不要动"),
]
TOTAL = PHASES[-1][1]

STATE = {
    "armed": False,       # operator has pressed start
    "running": False,
    "detector": "-",
    "round": 0,
    "rounds": 2,
    "elapsed": 0.0,
    "text": "等待开始",
    "remaining": 0.0,
    "phase": -1,
    "done": False,
}
STATE_LOCK = threading.Lock()


def state_view():
    with STATE_LOCK:
        return dict(STATE)


PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>追踪诊断 —— 倒计时</title>
<style>
 html,body{margin:0;height:100%;background:#111;color:#eee;
   font-family:system-ui,"Microsoft YaHei",sans-serif}
 .wrap{max-width:1100px;margin:0 auto;padding:26px 24px}
 h1{font-size:21px;color:#888;font-weight:500;margin:0 0 14px}
 #big{font-size:50px;line-height:1.3;font-weight:700;margin:8px 0;
   min-height:140px}
 #cd{font-size:84px;font-variant-numeric:tabular-nums;font-weight:700;
   color:#4ade80;margin:6px 0}
 #meta{font-size:20px;color:#888;margin-top:6px}
 #bar{height:22px;background:#222;border-radius:11px;overflow:hidden;
   margin:20px 0}
 #fill{height:100%;width:0;background:#4ade80;transition:width .2s linear}
 #steps{font-size:17px;color:#777;line-height:2;margin-top:22px}
 #steps .now{color:#4ade80;font-weight:700}
 button{font-size:30px;padding:22px 60px;border-radius:14px;border:0;
   background:#4ade80;color:#08130c;font-weight:700;cursor:pointer}
 button:disabled{background:#333;color:#666;cursor:default}
</style></head><body><div class="wrap">
<h1>追踪 / 检测诊断　——　跟着大字做动作</h1>
<div id="bar"><div id="fill"></div></div>
<div id="cd">--</div>
<div id="big">点击下面的按钮开始</div>
<div id="meta">等你点「开始」</div>
<div id="steps">__STEPS__</div>
<p style="margin-top:26px"><button id="go">开始</button></p>
</div>
<script>
const STEPS = __STEPS_JSON__;
const cd=document.getElementById('cd'), big=document.getElementById('big'),
      meta=document.getElementById('meta'), fill=document.getElementById('fill'),
      go=document.getElementById('go'), steps=document.getElementById('steps');
function renderSteps(cur){
  steps.innerHTML = STEPS.map((s,i)=>
    `<div class="${i===cur?'now':''}">${s[0]}-${s[1]}s　${s[2]}</div>`).join('');
}
go.onclick = async ()=>{ go.disabled=true; go.textContent='已开始…';
  try { await fetch('/start'); } catch(e){} };
async function tick(){
  try {
    const s = await (await fetch('/state',{cache:'no-store'})).json();
    if (s.done){ cd.textContent='OK'; big.textContent='全部完成，可以停下来了';
      meta.textContent=''; fill.style.width='100%'; renderSteps(-1);
      go.disabled=true; go.textContent='已完成'; return; }
    if (s.armed){
      go.style.display='none';
      cd.textContent = s.remaining>0 ? s.remaining.toFixed(1)+'s' : '--';
      big.textContent = s.text;
      meta.textContent = `第 ${s.round}/${s.rounds} 轮　检测器 ${s.detector}`;
      fill.style.width = (100*s.elapsed/Math.max(1,s.total)).toFixed(1)+'%';
      renderSteps(s.phase);
    }
  } catch(e){ meta.textContent='连接中断…'; }
}
renderSteps(-1); setInterval(tick, 250); tick();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/state"):
            view = state_view()
            view["total"] = TOTAL
            self._send(json.dumps(view).encode(), "application/json")
            return
        if self.path.startswith("/start"):
            with STATE_LOCK:
                STATE["armed"] = True
            self._send(b'{"ok":true}', "application/json")
            return
        steps = "".join(
            f"<div>{start}-{end}s　{text}</div>"
            for start, end, text in PHASES)
        steps_json = json.dumps([[s, e, t] for s, e, t in PHASES],
                                ensure_ascii=False)
        page = PAGE.replace("__STEPS__", steps).replace(
            "__STEPS_JSON__", steps_json)
        self._send(page.encode("utf-8"), "text/html; charset=utf-8")

    def log_message(self, *args):
        pass


def serve():
    server = ThreadingHTTPServer(("0.0.0.0", PAGE_PORT), Handler)
    server.serve_forever()


def publish(**fields):
    with STATE_LOCK:
        STATE.update(fields)


def wait_for_frames(client, seconds=90):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if http_json("/status").get("frame", 0) > 5:
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def sample(detector, round_index):
    """Sample /status through the phase script; return per-phase buckets."""
    buckets = {index: [] for index in range(len(PHASES))}
    started = time.time()
    while True:
        elapsed = time.time() - started
        if elapsed >= TOTAL:
            break
        phase = -1
        for index, (start, end, text) in enumerate(PHASES):
            if start <= elapsed < end:
                phase = index
                publish(elapsed=elapsed, remaining=end - elapsed, text=text,
                        phase=index, detector=detector, round=round_index,
                        running=True)
                break
        try:
            status = http_json("/status")
        except Exception:
            time.sleep(0.35)
            continue
        if phase >= 0:
            buckets[phase].append(status)
        time.sleep(0.35)
    return buckets


def report(detector, buckets):
    lines = [f"  === detector = {detector} ==="]
    lines.append(f"  {'阶段':<38} {'样本':>5} {'有检测%':>8} {'raw均':>6} "
                 f"{'raw大':>6} {'有轨迹%':>8} {'轨迹大':>6}")
    for index, (_, _, text) in enumerate(PHASES):
        rows = buckets[index]
        if not rows:
            lines.append(f"  {text[:36]:<38} {'-':>5}")
            continue
        raw = [r.get("raw_targets", 0) for r in rows]
        trk = [r.get("tracks", 0) for r in rows]
        raw_hit = 100.0 * sum(1 for v in raw if v) / len(raw)
        trk_hit = 100.0 * sum(1 for v in trk if v) / len(trk)
        lines.append(f"  {text[:36]:<38} {len(rows):>5} {raw_hit:>7.1f}% "
                     f"{statistics.fmean(raw):>6.2f} {max(raw):>6d} "
                     f"{trk_hit:>7.1f}% {max(trk):>6d}")
    text = "\n".join(lines)
    print(text, flush=True)
    return text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-pl", action="store_true")
    parser.add_argument("--skip-bg", action="store_true")
    args = parser.parse_args()

    threading.Thread(target=serve, daemon=True).start()

    print("=" * 78, flush=True)
    print("  追踪/检测诊断 —— pl 与 bg 对比", flush=True)
    print("=" * 78, flush=True)
    for start_s, end_s, text in PHASES:
        print(f"  {start_s:>3}-{end_s:<3}s  {text}", flush=True)
    print("=" * 78, flush=True)
    print(f"\n  >>> 浏览器打开:  http://127.0.0.1:{PAGE_PORT}/", flush=True)
    print("  >>> 在页面上点「开始」，然后跟着大字做动作 <<<\n", flush=True)

    while not state_view()["armed"]:
        time.sleep(0.5)
    print("  操作者已点开始，准备板子 ...", flush=True)

    client = connect()
    results = {}

    for index, detector in enumerate(("pl", "bg")):
        if (detector == "pl" and args.skip_pl) or \
                (detector == "bg" and args.skip_bg):
            continue
        publish(detector=detector, round=index + 1, elapsed=0.0,
                remaining=0.0, text=f"正在启动 {detector} …", phase=-1)
        print(f"\n>>> 启动 detector={detector} ...", flush=True)
        start(client, "board", detector)
        if not wait_for_frames(client):
            print(f"  {detector} 没能启动:", flush=True)
            print(run(client, "tail -20 /tmp/tracker.log", 30), flush=True)
            continue
        print(f"  {detector} 已就绪，倒计时开始", flush=True)
        publish(text="准备", elapsed=0.0)
        time.sleep(1)
        buckets = sample(detector, index + 1)
        results[detector] = buckets
        report(detector, buckets)

    run(client, "echo xilinx | sudo -S pkill -f tracker_server.py; sleep 2", 30)

    best = None
    print("\n" + "=" * 78, flush=True)
    if len(results) == 2:
        print("  对比（‘有检测%’= 该阶段有多少帧检测器真的给出了框）", flush=True)
        print("=" * 78, flush=True)
        scores = {}
        for index, (_, _, text) in enumerate(PHASES):
            line = f"  {text[:36]:<38}"
            for detector in ("pl", "bg"):
                rows = results.get(detector, {}).get(index, [])
                raw = [r.get("raw_targets", 0) for r in rows]
                hit = 100.0 * sum(1 for v in raw if v) / len(raw) if raw else 0.0
                line += f"  {detector}:{hit:5.1f}%"
                if 1 <= index <= 4:
                    scores[detector] = scores.get(detector, 0.0) + hit
            print(line, flush=True)
        if scores:
            best = max(scores, key=scores.get)
            print("\n  运动阶段合计: "
                  + "  ".join(f"{k}={v:.0f}%" for k, v in scores.items()),
                  flush=True)

    leave = best or "pl"
    print(f"\n  重新启动 detector={leave}，摄像头保持开启", flush=True)
    start(client, "board", leave)
    if wait_for_frames(client):
        status = http_json("/status")
        print(f"  已就绪: frame={status['frame']} fps={status['fps']:.2f} "
              f"detector={status['detector']}", flush=True)
        print("  open http://192.168.137.125:8081/", flush=True)
        publish(done=True, text="全部完成", running=False)
    else:
        print("  启动失败，日志：", flush=True)
        print(run(client, "tail -20 /tmp/tracker.log", 30), flush=True)
        publish(done=True, text="启动失败", running=False)
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
