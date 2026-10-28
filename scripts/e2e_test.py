"""Full end-to-end test: FastAPI server + real YouTube job (local run)."""
import json
import subprocess
import sys
import time

import urllib.request

BASE = "http://127.0.0.1:8765"

# start server
proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "backend.main:app", "--port", "8765"],
    cwd="/home/z/my-project/autotabber",
    stdout=open("/tmp/at_test/uvicorn.log", "w"),
    stderr=subprocess.STDOUT,
)

def http(path, data=None):
    req = urllib.request.Request(BASE + path)
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, body, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]

try:
    # wait for server
    for _ in range(30):
        try:
            s, h = http("/api/health")
            if s == 200:
                print("health:", h)
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        raise SystemExit("server did not start; see /tmp/at_test/uvicorn.log")

    # homepage check
    s = urllib.request.urlopen(BASE + "/")
    html = s.read().decode()
    print("index OK:", "AutoTabber" in html, "| vendor check:")
    s = urllib.request.urlopen(BASE + "/vendor/alphatab/alphaTab.min.mjs")
    print("alphatab js:", s.status, len(s.read()), "bytes")

    # create job
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    s, data = http("/api/jobs", {"url": url, "instrument": "electric"})
    print("job created:", s, data)
    job_id = data["id"]

    last = None
    t0 = time.time()
    while time.time() - t0 < 900:
        s, job = http(f"/api/jobs/{job_id}")
        cur = (job["status"], job["stage"], job["progress"])
        if cur != last:
            print(f"  [{time.time()-t0:6.1f}s] {cur}")
            last = cur
        if job["status"] in ("done", "error"):
            break
        time.sleep(2)

    if job["status"] == "error":
        print("JOB ERROR:", job["error"])
        sys.exit(1)

    print("\ntitle:", job["title"])
    print("tempo:", job["tempo"])
    print("files:", job["files"])
    tex = job["alphatex"]
    print("\n--- alphaTex (first 500 chars) ---")
    print(tex[:500])
    assert "\\tempo" in tex and "|" in tex
    # download checks
    for name, href in job["files"].items():
        with urllib.request.urlopen(BASE + href, timeout=30) as r:
            blob = r.read()
        print(f"download {name}: {len(blob)} bytes")
    print("\nE2E OK")
finally:
    proc.terminate()
