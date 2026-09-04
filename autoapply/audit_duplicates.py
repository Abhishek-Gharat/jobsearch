#!/usr/bin/env python3
"""Audit results history for REAL duplicate submissions vs log-snapshot echoes."""
import json, re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
GAP_MIN = 10  # submitted lines for same id spaced >10min apart => distinct real attempts

def parse_ts(s):
    try:
        d = datetime.fromisoformat(str(s))
        if d.tzinfo is None:
            d = d.astimezone()
        return d
    except Exception:
        return None

def norm_url(u):
    u = (u or "").split("?")[0].rstrip("/").lower()
    m = re.search(r"(smartrecruiters\.com/[^/]+/(\d+))", u)
    if m:
        return "sr:" + m.group(2)          # numeric posting id = canonical identity
    return u

def main():
    jd = json.loads((BASE/"jobs.json").read_text(encoding="utf-8-sig"))
    by_id = {j["id"]: j for j in jd["jobs"]}

    # ---- collect every result line ever written
    events = defaultdict(list)   # job_id -> [(result, ts, file)]
    files = sorted(BASE.glob("results/*.jsonl"))
    for f in files:
        for ln in f.read_text(encoding="utf-8-sig").splitlines():
            if not ln.strip():
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            jid = r.get("job_id")
            if jid:
                events[jid].append((r.get("result"), parse_ts(r.get("ts")), f.name,
                                     (r.get("evidence") or {}).get("confirmation","")))

    counts_table = []
    real_dups = []
    snapshot_echoes = 0
    for jid, evs in sorted(events.items()):
        c = Counter(res for res, _, _, _ in evs)
        if not any(k in c for k in ("submitted", "review_required", "failed", "skipped")):
            continue
        subs = sorted([t for res, t, _, _ in evs if res == "submitted" and t])
        # detect real repeat submissions: same id, submitted lines spaced > GAP_MIN
        repeats = []
        for i in range(1, len(subs)):
            if (subs[i] - subs[i-1]).total_seconds() > GAP_MIN*60:
                repeats.append((subs[i-1], subs[i]))
        snapshot_echoes += max(0, len(subs)-1-len(repeats))
        meta = by_id.get(jid, {})
        counts_table.append({
            "id": jid, "company": meta.get("company", "?"),
            "url": meta.get("url", ""), "portal": meta.get("portal", "?"),
            "submitted": c.get("submitted",0), "review_required": c.get("review_required",0),
            "failed": c.get("failed",0), "skipped": c.get("skipped",0),
            "total_lines": len(evs),
            "real_repeat_submissions": len(repeats),
        })
        if c.get("submitted",0) > 1 and repeats:
            real_dups.append({
                "id": jid, "company": meta.get("company","?"), "url": meta.get("url",""),
                "submission_times": [t.isoformat() for t in subs],
                "repeat_pairs": [(a.isoformat(), b.isoformat()) for a,b in repeats],
                "confirmations": [cf for res,t,f,cf in evs if res=="submitted"],
            })

    # ---- cross-id URL-level duplicates among submitted jobs
    url_groups = defaultdict(list)
    for row in counts_table:
        if row["submitted"] >= 1 and row["url"]:
            url_groups[norm_url(row["url"])].append(row)
    url_dupes = {k: v for k, v in url_groups.items() if len(v) > 1}

    # ---- canonicalization + prevention for TRUE url-level dupes
    index_path = BASE/"applied_dedupe_index.json"
    idx = json.loads(index_path.read_text(encoding="utf-8-sig")) if index_path.exists() else \
          {"note":"permanent guard - checked before every application","index":{}}
    actions = []
    for k, rows in url_dupes.items():
        # canonical = the one currently submitted; earliest other = first-applied
        subm = [r for r in rows if r["submitted"]]
        canon = min(subm, key=lambda r: r["id"]) if subm else rows[0]
        idx["index"][k] = {"canonical_id": canon["id"], "company": canon["company"]}
        for r in rows:
            if r["id"] == canon["id"]:
                continue
            j = by_id.get(r["id"])
            if j and j.get("status") in ("pending","in_progress"):
                j["status"] = "skipped"
                j["error"] = f"duplicate_of_{canon['id']}"
                j["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
                actions.append(f"{r['id']} -> skipped (duplicate of {canon['id']}) [{k}]")
        actions.append(f"index added: {k} -> canonical {canon['id']}")
    # also register every single-submitted url as future guard
    for k, rows in url_groups.items():
        if k not in idx["index"] and len(rows) == 1 and rows[0]["submitted"]:
            idx["index"][k] = {"canonical_id": rows[0]["id"], "company": rows[0]["company"]}
    idx["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    index_path.write_text(json.dumps(idx, indent=2), encoding="utf-8")
    (BASE/"jobs.json").write_text(json.dumps(jd, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---- markdown report
    md = ["# Duplicate Submission Audit", "",
          f"Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
          f"Result files scanned: {len(files)}",
          f"Snapshot-echo lines (same attempt logged twice): {snapshot_echoes}",
          f"REAL duplicate submission incidents: {len(real_dups)}",
          f"URL-level multi-submission groups: {len(url_dupes)}",
          "", "## Per-job result counts", "",
          "| id | company | submitted | review | failed | skipped | lines | real-repeats |",
          "|---|---|---|---|---|---|---|---|"]
    for r in sorted(counts_table, key=lambda x: -(x["submitted"]*10+x["total_lines"])):
        md.append(f"| {r['id']} | {r['company'][:22]} | {r['submitted']} | "
                  f"{r['review_required']} | {r['failed']} | {r['skipped']} | "
                  f"{r['total_lines']} | {r['real_repeat_submissions']} |")
    md += ["", "## Real duplicate incidents (evidence)", ""]
    if not real_dups:
        md.append("_None found._ All repeated lines are retry-walk snapshots of the same "
                  "attempt history, not second submissions.\n")
    for d in real_dups:
        md += [f"- **{d['id']} {d['company']}** submitted {len(d['submission_times'])}x:",
               f"  - times: {', '.join(d['submission_times'])}",
               f"  - confirmations: {d['confirmations']}"]
    md += ["", "## URL-level groups (>1 job id sharing one posting)", ""]
    if not url_dupes:
        md.append("_None._")
    for k, rows in url_dupes.items():
        md.append(f"- `{k}`: " + ", ".join(f"{r['id']}({r['submitted']}x submitted)" for r in rows))
    md += ["", "## Actions taken", ""]
    md += [f"- Permanent dedupe index: applied_dedupe_index.json ({len(idx['index'])} urls guarded)"
           ] + [f"- {a}" for a in actions]
    (BASE/"duplicate_audit.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    out = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "real_duplicate_incidents": real_dups,
        "url_level_duplicate_groups": {k: [{"id": r["id"], "submitted": r["submitted"],
                                             "canonical": False} for r in v]
                                        for k, v in url_dupes.items()},
        "snapshot_echo_lines": snapshot_echoes,
        "dedupe_index_size": len(idx["index"]),
        "actions": actions,
    }
    (BASE/"duplicate_jobs.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"jobs_with_history={len(counts_table)} real_dups={len(real_dups)} "
          f"url_groups_multi={len(url_dupes)} echoes={snapshot_echoes}")
    print(f"dedupe index size={len(idx['index'])}")

if __name__ == "__main__":
    main()
