#!/usr/bin/env python3
"""Check: processes hogging CPU for a while or using a lot of RAM, compared with a per-process baseline.

Env: AA_HOG_CPU_PCT, AA_HOG_MINUTES, AA_HOG_RSS_GB, AA_STATE_DIR (hogs.json: exponential moving average
of cpu/rss per command name). Known heavy apps are only reported above 2x their baseline. Never kills anything.
"""
import json
import os
import subprocess
from pathlib import Path

IGNORE = {"qemu-system-x86", "java", "idea", "goland", "webstorm", "pycharm", "clion", "rustrover",
          "chrome", "firefox", "node", "electron", "k3s-server", "containerd"}
ALPHA = 0.2


def main() -> None:
    cpu_limit = float(os.environ.get("AA_HOG_CPU_PCT", 50))
    minutes = float(os.environ.get("AA_HOG_MINUTES", 5))
    rss_limit_mb = float(os.environ.get("AA_HOG_RSS_GB", 4)) * 1024
    state = Path(os.environ.get("AA_STATE_DIR", ".")) / "hogs.json"
    try:
        baseline = json.loads(state.read_text())
    except Exception:
        baseline = {}

    out = subprocess.run(["ps", "-eo", "pid,comm,pcpu,rss,etimes", "--no-headers"], capture_output=True, text=True)
    procs = []
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            procs.append({"pid": int(parts[0]), "name": " ".join(parts[1:-3]), "cpu_pct": float(parts[-3]),
                          "rss_mb": int(parts[-2]) // 1024, "etimes": int(parts[-1])})
        except ValueError:
            continue

    # compare against the baseline from previous runs, then fold this run's per-command peaks into it
    items = []
    for p in procs:
        prior = baseline.get(p["name"], {"cpu": 0.0, "rss": 0})
        cpu_hog = p["cpu_pct"] > cpu_limit and p["etimes"] > minutes * 60
        rss_hog = p["rss_mb"] > rss_limit_mb
        if not (cpu_hog or rss_hog):
            continue
        if p["name"] in IGNORE:
            cpu_ok = not cpu_hog or prior["cpu"] == 0 or p["cpu_pct"] <= 2 * prior["cpu"]
            rss_ok = not rss_hog or prior["rss"] == 0 or p["rss_mb"] <= 2 * prior["rss"]
            if cpu_ok and rss_ok:
                continue
        items.append({"name": p["name"], "pid": p["pid"], "cpu_pct": p["cpu_pct"], "rss_mb": p["rss_mb"],
                      "baseline_cpu": prior["cpu"], "baseline_rss_mb": prior["rss"]})

    peak: dict[str, dict] = {}
    for p in procs:
        cur = peak.setdefault(p["name"], {"cpu": 0.0, "rss": 0})
        cur["cpu"] = max(cur["cpu"], p["cpu_pct"])
        cur["rss"] = max(cur["rss"], p["rss_mb"])
    for name, cur in peak.items():
        b = baseline.get(name, {"cpu": cur["cpu"], "rss": cur["rss"]})
        baseline[name] = {"cpu": round(b["cpu"] * (1 - ALPHA) + cur["cpu"] * ALPHA, 2),
                          "rss": int(b["rss"] * (1 - ALPHA) + cur["rss"] * ALPHA)}
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps(baseline))
    if not items:
        print(json.dumps({"id": "resource-hogs", "severity": "none", "title": "No resource hogs", "summary": ""}))
        return
    items.sort(key=lambda i: (-i["cpu_pct"], -i["rss_mb"]))
    desc = ", ".join(f"{i['name']} (pid {i['pid']}, {i['cpu_pct']:.0f} % CPU, {i['rss_mb']} MB)" for i in items[:5])
    print(json.dumps({
        "id": "resource-hogs", "severity": "medium",
        "title": f"{len(items)} process{'es' if len(items) != 1 else ''} hogging resources",
        "summary": desc, "items": items, "actions": [],
    }))


if __name__ == "__main__":
    main()
