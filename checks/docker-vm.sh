#!/bin/bash
# Check (report only, never deletes): reclaimable Docker images/volumes over 1 GB and qcow2 files in AA_VM_DIR
# that are not part of any libvirt domain's backing chain. Fails (exit 1) when virsh or qemu-img fail, so that
# the active VM disks are never reported as unused by mistake.
set -uo pipefail

vm_dir="${AA_VM_DIR:-/mnt/data/vm}"
docker_df=""
if docker info >/dev/null 2>&1; then
    docker_df=$(docker system df --format json 2>/dev/null || true)
fi

used_images=""
if command -v virsh >/dev/null 2>&1; then
    if ! domains=$(virsh -c qemu:///system list --all --name 2>&1); then
        echo "virsh list failed: $domains" >&2
        exit 1
    fi
    while read -r dom; do
        [[ -z "$dom" ]] && continue
        while read -r src; do
            [[ "$src" != *.qcow2 ]] && continue
            used_images+="$src"$'\n'
            # -U (force-share) reads images a running QEMU holds open
            if ! chain=$(sudo /usr/bin/qemu-img info -U --backing-chain "$src" 2>&1); then
                echo "qemu-img info failed for $src: $chain" >&2
                exit 1
            fi
            used_images+=$(awk '/^image: /{print substr($0, 8)}' <<<"$chain")$'\n'
        done < <(virsh -c qemu:///system domblklist "$dom" 2>/dev/null | awk 'NR > 2 {print $2}')
    done <<<"$domains"
fi

DOCKER_DF="$docker_df" USED="$used_images" VM_DIR="$vm_dir" python3 - <<'PY'
import json, os, re
from pathlib import Path

def to_mb(s):
    m = re.match(r"([\d.]+)\s*([kMGT]?B)", s or "")
    if not m:
        return 0
    n, unit = float(m.group(1)), m.group(2)
    return int(n * {"B": 1 / 1048576, "kB": 1 / 1024, "MB": 1, "GB": 1024, "TB": 1048576}[unit])

items = []
for line in os.environ["DOCKER_DF"].splitlines():
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        continue
    kind = {"Images": "image", "Local Volumes": "volume"}.get(row.get("Type"))
    if not kind:
        continue
    mb = to_mb(row.get("Reclaimable", ""))
    if mb >= 1024:
        items.append({"name": f"docker {kind}s", "kind": kind, "size_mb": mb})

used = {os.path.realpath(p) for p in os.environ["USED"].splitlines() if p.strip()}
vm_dir = Path(os.environ["VM_DIR"])
if vm_dir.is_dir():
    for f in sorted(vm_dir.glob("*.qcow2")):
        if os.path.realpath(f) not in used:
            items.append({"name": str(f), "kind": "vm-overlay", "size_mb": round(f.stat().st_size / 1048576)})

if not items:
    print(json.dumps({"id": "docker-vm", "severity": "none", "title": "No unused Docker or VM data", "summary": ""}))
    raise SystemExit
total = sum(i["size_mb"] for i in items)
print(json.dumps({
    "id": "docker-vm", "severity": "low",
    "title": f"Unused Docker/VM data: {total} MB",
    "summary": "Report only, nothing is deleted automatically: " + ", ".join(f"{i['name']} ({i['size_mb']} MB)" for i in items),
    "items": items, "actions": [],
}))
PY
