#!/usr/bin/env python3
"""Gmail Truth Engine - read-only duplicate protection.

Backends:
  api     -> Gmail API via OAuth (requires client_secret.json in BASE; token cached
             DPAPI-encrypted at gmail_token.bin). Scope: gmail.readonly ONLY.
  browser -> opencode worker reads Gmail web (signed-in profile) via granular tools;
             search-URL navigation only. Never composes/deletes/marks.

Outputs:
  gmail_truth_index.json   {dedupe_key: {company, role, portal, date, msg_id/url}}
  gmail_scan_log.json      per-scan audit
  gmail_parser_metrics.json
  gmail_duplicate_report.md
"""
from __future__ import annotations
import argparse, base64, json, re, sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
INDEX = BASE / "gmail_truth_index.json"
SCANLOG = BASE / "gmail_scan_log.json"
METRICS = BASE / "gmail_parser_metrics.json"
REPORT = BASE / "gmail_duplicate_report.md"
TOKEN = BASE / "gmail_token.bin"
SECRET = BASE / "client_secret.json"

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

# sender-domain -> portal  (parser table, learned/extended over time)
DOMAIN_PORTAL = {
    "smartrecruiters": "smartrecruiters", "workable": "workable",
    "greenhouse": "greenhouse", "lever.co": "lever", "ashbyhq": "ashby",
    "myworkdayjobs": "workday", "darwinbox": "darwinbox", "freshteam": "freshteam",
    "recruiterflow": "recruiterflow", "zohorecruit": "zoho", "icims": "icims",
    "eightfold": "eightfold", "pinpointhq": "pinpoint", "jobvite": "jobvite",
    "breezy": "breezy",
}
ROLE_RE = re.compile(
    r"(frontend|front-end|react|next\.?js|javascript|full.?stack|mern|web developer|ui engineer|software engineer|developer)",
    re.I)
CONFIRM_RE = re.compile(
    r"(application (?:was |has been )?(?:received|submitted|sent)|thank you for (?:applying|your application)|"
    r"thanks for applying|we (?:have )?received your application|your application to|"
    r"application confirmed|successfully (?:submitted|applied))", re.I)

def dpapi_protect(data: bytes) -> bytes:
    import ctypes
    blob = ctypes.c_buffer(data, len(data))
    out = ctypes.POINTER(ctypes.c_char)(); out_len = ctypes.c_int()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(blob), None, None, None,
                                                  None, 0, ctypes.byref(out), ctypes.byref(out_len)):
        raise OSError("CryptProtectData failed")
    raw = ctypes.string_at(out, out_len)
    ctypes.windll.kernel32.LocalFree(out)
    return raw

def dpapi_unprotect(data: bytes) -> bytes:
    import ctypes
    blob = ctypes.c_buffer(bytes(data), len(data))
    out = ctypes.POINTER(ctypes.c_char)(); out_len = ctypes.c_int()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(blob), None, None, None,
                                                    None, 0, ctypes.byref(out), ctypes.byref(out_len)):
        raise OSError("CryptUnprotectData failed")
    raw = ctypes.string_at(out, out_len)
    ctypes.windll.kernel32.LocalFree(out)
    return raw

# ------------------------------------------------------------------ API backend

def api_backend():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        import google.oauth2.credentials  # noqa
    except ImportError as e:
        print(f"[api] missing deps: {e}\n"
              "  pip install google-auth-oauthlib google-api-python-client\n"
              "  AND place client_secret.json (Desktop app) next to this script.")
        return None
    import requests
    creds = None
    if TOKEN.exists():
        try:
            tok = json.loads(dpapi_unprotect(TOKEN.read_bytes()))
            creds = json.loads(json.dumps(tok))
            from google.oauth2.credentials import Credentials
            creds = Credentials.from_authorized_user_info(creds)
        except Exception:
            creds = None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not SECRET.exists():
                print("[api] client_secret.json missing - cannot start OAuth")
                return None
            flow = InstalledAppFlow.from_client_secrets_file(str(SECRET), scopes=[SCOPE])
            creds = flow.run_local_server(port=0, prompt="consent")
        TOKEN.write_bytes(dpapi_protect(creds.to_json()))
    def gm(method, url, **kw):
        return requests.request(method, f"https://gmail.googleapis.com/gmail/v1/users/me/{url}",
                                headers={"Authorization": f"Bearer {creds.token}"}, timeout=30, **kw)
    return gm


def api_scan(query, max_msgs):
    gm = api_backend()
    if not gm:
        return []
    msgs, page = [], None
    while len(msgs) < max_msgs:
        q = query + (f"&pageToken={page}" if page else "")
        r = gm("GET", f"messages?q={urllib.parse.quote(query)}&maxResults=100" +
               (f"&pageToken={page}" if page else ""))
        if r.status_code != 200:
            print("[api] list error", r.status_code, r.text[:120]); break
        data = r.json()
        msgs += [m["id"] for m in data.get("messages", [])]
        page = data.get("nextPageToken")
        if not page:
            break
    out = []
    for mid in msgs[:max_msgs]:
        r = gm("GET", f"messages/{mid}?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date")
        if r.status_code != 200:
            continue
        hdrs = {h["name"].lower(): h["value"] for h in r.json().get("payload", {}).get("headers", [])}
        out.append({"msg_id": mid, "from": hdrs.get("from", ""), "subject": hdrs.get("subject",""),
                    "date": hdrs.get("date","")})
    return out


# ------------------------------------------------------------------ parsing

def parse_email(sender: str, subject: str):
    frm = (sender or "").lower()
    portal = next((p for d, p in DOMAIN_PORTAL.items() if d in frm), "generic")
    conf = bool(CONFIRM_RE.search(subject or "")) or portal in (
        "smartrecruiters","workable","greenhouse","lever","ashby","workday",
        "darwinbox","freshteam","recruiterflow","zoho")
    m = ROLE_RE.search(subject or "")
    role = m.group(0) if m else ""
    # company guess: display-name part of sender minus noise
    disp = re.sub(r"<.*$", "", sender or "").strip(' "\'')
    comp = disp or frm.split("@")[-1].split(".")[0]
    confidence = 0.9 if portal != "generic" else (0.6 if CONFIRM_RE.search(subject or "") else 0.35)
    return {"company": comp[:40], "role_hint": role, "portal": portal,
            "is_confirmation": bool(conf), "confidence": confidence}


# ------------------------------------------------------------------ index merge

def merge_into_index(rows, source):
    idx = load_json(INDEX, {"note": "gmail truth index (read-only derived)",
                              "generated_at": None, "entries": {}})
    added = dup_blocked = 0
    jd = load_json(BASE/"jobs.json", {"jobs": []})
    for r in rows:
        parsed = parse_email(r.get("from",""), r.get("subject",""))
        key_domain = next((d for d in DOMAIN_PORTAL if d in (r.get("from","").lower())), "generic")
        key = f"gmail:{parsed['portal']}:{re.sub(r'[^a-z0-9]','',(r.get('company_key') or parsed['company']).lower())}"
        entry = {"company": parsed["company"], "role_hint": parsed["role_hint"],
                  "portal": parsed["portal"], "date": r.get("date"),
                  "msg_id": r.get("msg_id"), "source": source,
                  "confidence": parsed["confidence"]}
        if key in idx["entries"]:
            continue
        idx["entries"][key] = entry
        added += 1
        # cross-mark queue: same-company pendings become review/skip guarded
        norm = re.sub(r"[^a-z0-9]", "", parsed["company"].lower())
        for j in jd["jobs"]:
            jn = re.sub(r"[^a-z0-9]", "", (j.get("company") or "").lower())
            if jn and jn == norm and j.get("status") == "pending":
                j["status"] = "skipped"
                j["error"] = "already_applied_via_gmail"
                dup_blocked += 1
    idx["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    INDEX.write_text(json.dumps(idx, indent=2, ensure_ascii=False), encoding="utf-8")
    (BASE/"jobs.json").write_text(json.dumps(jd, indent=2, ensure_ascii=False), encoding="utf-8")
    return added, dup_blocked

def load_json(p, d):
    try: return json.loads(Path(p).read_text(encoding="utf-8-sig"))
    except Exception: return d

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["api", "browser"], default="browser")
    ap.add_argument("--query", default="in:inbox newer_than:180d (application OR \"applied\" OR \"thank you\")")
    ap.add_argument("--max", type=int, default=120)
    a = ap.parse_args()

    log = {"scan_started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "backend": a.backend, "query": a.query}
    if a.backend == "api":
        rows = api_scan(a.query, a.max)
        log["emails_scanned"] = len(rows)
    else:
        print("[browser] launch run_gmail_scan.py worker first; this mode consumes its output file.")
        rows = []
        for f in sorted(BASE.glob("gmail_raw_*.jsonl")):
            for ln in f.read_text(encoding="utf-8-sig").splitlines():
                if ln.strip():
                    try: rows.append(json.loads(ln))
                    except Exception: pass
        log["emails_scanned"] = len(rows)

    parsed = [parse_email(r.get("from",""), r.get("subject","")) | r for r in rows]
    confirmations = [r for r, p in zip(rows, parsed) if p["is_confirmation"]]
    added, blocked = merge_into_index(confirmations, a.backend)
    log.update({"confirmations_found": len(confirmations),
                 "index_added": added, "queue_duplicates_blocked": blocked})
    SCANLOG.write_text(json.dumps(log, indent=2), encoding="utf-8")

    conf_counter = {}
    for p in parsed:
        k = f"{p['portal']}:{'confirm' if p['is_confirmation'] else 'other'}"
        conf_counter[k] = conf_counter.get(k, 0) + 1
    METRICS.write_text(json.dumps({"generated_at": log["scan_started"],
        "parser_table_size": len(DOMAIN_PORTAL), "classified": conf_counter},
        indent=2), encoding="utf-8")

    md = ["# Gmail Duplicate Report", "",
          f"- Backend: {a.backend}", f"- Query: `{a.query}`",
          f"- Emails scanned: {len(rows)}", f"- Confirmations found: {len(confirmations)}",
          f"- Index additions: {added}", f"- Queue duplicates auto-blocked: {blocked}",
          "", "## Sample confirmations", ""]
    for c in confirmations[:15]:
        p = parse_email(c.get("from",""), c.get("subject",""))
        md.append(f"- {p['company'][:28]} | {c.get('subject','')[:60]} | via {p['portal']} "
                  f"(conf {p['confidence']})")
    REPORT.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"gmail scan done: scanned={len(rows)} confirmations={len(confirmations)} "
          f"index+= {added} blocked={blocked}")

if __name__ == "__main__":
    main()
