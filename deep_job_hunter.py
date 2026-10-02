#!/usr/bin/env python3
"""Offline discovery-to-planner bridge.

This module reads the existing ``autoapply/jobs.json`` export and proposes queue
rows.  It deliberately has no browser, network, or application side effects.
The default command is a dry run; ``--commit`` is the only way it can call the
atomic queue-store writer.

The source export and the master queue have intentionally different schemas.
Only jobs which are explicitly pending, fit the compact planner profile, have a
non-root job URL, and are not already represented in the queue are proposed.
Existing queue rows are never edited or have their statuses changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent
SOURCE_DEFAULT = ROOT / "autoapply" / "jobs.json"
QUEUE_DEFAULT = ROOT / "excel-rows.json"

# The source can use either words or the auto-apply dashboard's labels.  This
# list is intentionally allow-listed: an absent/unknown status is not silently
# treated as pending.
PENDING_SOURCE_STATUSES = {
    "pending", "queued", "queue", "discovered", "new", "open", "active",
    "ready", "ready_to_apply", "ready-to-apply", "unprocessed",
    "not_processed", "not-processed", "review_required", "review-required",
    "pending_human", "pending-human",
}
STATUS_MAP = {
    "pending": "PENDING",
    "queued": "PENDING",
    "queue": "PENDING",
    "discovered": "UNPROCESSED",
    "new": "UNPROCESSED",
    "open": "UNPROCESSED",
    "active": "UNPROCESSED",
    "ready": "READY",
    "ready_to_apply": "READY",
    "ready-to-apply": "READY",
    "unprocessed": "UNPROCESSED",
    "not_processed": "NOT_PROCESSED",
    "not-processed": "NOT_PROCESSED",
    "review_required": "PENDING_HUMAN",
    "review-required": "PENDING_HUMAN",
    "pending_human": "PENDING_HUMAN",
    "pending-human": "PENDING_HUMAN",
}

TRACKING_QUERY_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "referrer", "source", "src", "trk", "tracking", "gh_src",
}
ROOT_PATHS = {
    "", "/", "/careers", "/career", "/jobs", "/job", "/openings",
    "/opportunities", "/join-us", "/joinus", "/work-with-us", "/company/jobs",
}
ATS_HOST_MARKERS = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "smartrecruiters.com",
    "myworkdayjobs.com", "icims.com", "jobvite.com", "breezy.hr", "recruitee.com",
    "teamtailor.com", "pinpointhq.com", "applytojob.com", "eightfold.ai",
    "rippling.com", "taleo.net", "successfactors.com",
)
JOB_PATH_MARKERS = (
    "/job/", "/jobs/", "/position/", "/positions/", "/opening/", "/openings/",
    "/requisition/", "/requisitions/", "/vacancy/", "/vacancies/", "/en-us/job/",
    "/jobs/view/",
)
DEAD_WORDS = re.compile(r"\b(?:closed|expired|filled|no longer available|not accepting)\b", re.I)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        return ""
    return str(value).strip()


def _first(job: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = _text(job.get(key))
        if value:
            return value
    return ""


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [_text(v) for v in value if _text(v)]
    return []


def _source_jobs(payload: Any) -> list[dict[str, Any]]:
    """Accept both the dashboard object and a plain list for easy offline use."""
    if isinstance(payload, dict):
        payload = payload.get("jobs", payload.get("rows", []))
    if not isinstance(payload, list):
        raise ValueError("source JSON must contain a jobs/rows array")
    return [dict(row) for row in payload if isinstance(row, Mapping)]


def load_json(path: Path | str) -> Any:
    with open(path, encoding="utf-8-sig") as handle:
        return json.load(handle)


def canonicalize_url(value: Any) -> str:
    """Canonicalize a URL for identity without following redirects or doing I/O."""
    raw = _text(value)
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return ""
    host = parts.hostname.lower() if parts.hostname else ""
    if not host:
        return ""
    try:
        port = parts.port
    except ValueError:
        return ""
    netloc = host
    if port and not ((parts.scheme.lower() == "http" and port == 80) or
                     (parts.scheme.lower() == "https" and port == 443)):
        netloc += f":{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
             if key.lower() not in TRACKING_QUERY_KEYS and not key.lower().startswith("utm_")]
    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(query), ""))


def is_actual_job_url(value: Any) -> bool:
    """Return true for a detailed posting URL, never for a careers/jobs root.

    No request is made.  Known ATS hosts are accepted when their path is a
    detailed path; unknown hosts are accepted only when the path contains a
    conventional posting marker.  This errs toward dropping a root page rather
    than putting a planner/executor in front of a non-job page.
    """
    url = canonicalize_url(value)
    if not url:
        return False
    parts = urlsplit(url)
    path = parts.path.lower()
    if path in ROOT_PATHS:
        return False
    # A host root with only a query/fragment was normalized to a root above.
    if any(marker in path for marker in JOB_PATH_MARKERS):
        return True
    host = parts.hostname or ""
    if any(marker in host for marker in ATS_HOST_MARKERS):
        segments = [piece for piece in path.split("/") if piece]
        # Some ATSes use /company/slug-id rather than /jobs/id.  Requiring two
        # path components avoids accepting https://ats.example/careers alone.
        return len(segments) >= 2 and any(re.search(r"\d{3,}|[a-z0-9]+-[a-z0-9-]+", s)
                                          for s in segments)
    return False


def _url_candidates(job: Mapping[str, Any]) -> list[str]:
    evidence = job.get("evidence") if isinstance(job.get("evidence"), Mapping) else {}
    candidates: list[str] = []
    for key in (
        "url", "job_url", "jobUrl", "canonical_url", "canonicalUrl", "apply_url",
        "applyUrl", "career_url", "careerUrl", "ats_url", "atsUrl",
    ):
        candidates.append(_text(job.get(key)))
    for key in (
        "url", "job_url", "jobUrl", "canonical_url", "canonicalUrl", "apply_url", "applyUrl",
        "career_url", "careerUrl", "ats_url", "atsUrl",
    ):
        candidates.append(_text(evidence.get(key)))
    candidates.extend(_as_list(job.get("sourceUrls")))
    candidates.extend(_as_list(job.get("source_urls")))
    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        canonical = canonicalize_url(candidate)
        if canonical and is_actual_job_url(canonical) and canonical not in seen:
            result.append(canonical)
            seen.add(canonical)
    return result


# Public aliases make the safety intent clear to callers without duplicating
# URL parsing logic.
is_actual_ats_job_url = is_actual_job_url
normalize_url = canonicalize_url


def _norm_identity(value: Any) -> str:
    value = _text(value).casefold().replace("&", " and ")
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def identity_key(company: Any, role: Any) -> tuple[str, str]:
    return _norm_identity(company), _norm_identity(role)


def _url_key(value: Any) -> str:
    return canonicalize_url(value).casefold()


def _status_for(job: Mapping[str, Any]) -> str | None:
    raw = _text(job.get("status")).casefold().replace(" ", "_")
    return STATUS_MAP.get(raw) if raw in PENDING_SOURCE_STATUSES else None


def _evidence(job: Mapping[str, Any]) -> dict[str, Any]:
    raw = job.get("evidence")
    evidence = dict(raw) if isinstance(raw, Mapping) else {}
    # Keep only compact, useful evidence fields; never copy a huge browser dump.
    output: dict[str, Any] = {}
    for key in ("live", "is_live", "isLive", "live_status", "status", "last_verified",
                "lastVerified", "verified_at", "verifiedAt", "freshness", "postedDate",
                "source", "notes"):
        value = evidence.get(key)
        if isinstance(value, (str, int, float, bool)) and str(value).strip() != "":
            output[key] = value
    for key in ("live", "is_live", "isLive", "live_status", "last_verified", "lastVerified",
                "verified_at", "verifiedAt", "freshness"):
        if key not in output and _text(job.get(key)):
            output[key] = job[key]
    return output


def _explicitly_dead(job: Mapping[str, Any], evidence: Mapping[str, Any]) -> bool:
    for key in ("live", "is_live", "isLive"):
        if key in evidence and isinstance(evidence[key], bool) and not evidence[key]:
            return True
    status = _text(evidence.get("live_status") or evidence.get("status")).casefold()
    if status in {"closed", "expired", "filled", "dead", "inactive", "not_live", "not-live"}:
        return True
    # Only inspect explicit failure/error fields.  A normal notes field may
    # mention that another job was closed and should not disqualify this one.
    for key in ("failureReason", "failure_reason", "error"):
        if DEAD_WORDS.search(_text(job.get(key))):
            return True
    return False


def _freshness(job: Mapping[str, Any], evidence: Mapping[str, Any]) -> str:
    raw = _first(job, "freshness", "freshness_label") or _first(evidence, "freshness")
    if raw:
        return raw
    posted = _first(job, "postedDate", "posted_date", "published_at", "publishedAt", "created_at")
    if not posted:
        return "unknown"
    # This is an annotation only.  We do not reject by age because source
    # timestamps can have different timezone/clock conventions.
    try:
        parsed = datetime.fromisoformat(posted.replace("Z", "+00:00")).date()
        age = (datetime.now(timezone.utc).date() - parsed).days
        return "fresh" if age <= 14 else ("recent" if age <= 45 else "stale")
    except (TypeError, ValueError):
        try:
            parsed = date.fromisoformat(posted[:10])
            age = (datetime.now(timezone.utc).date() - parsed).days
            return "fresh" if age <= 14 else ("recent" if age <= 45 else "stale")
        except (TypeError, ValueError):
            return "unknown"


def _queue_ids(rows: Iterable[Mapping[str, Any]]) -> set[str]:
    return {_text(row.get("queueId")) for row in rows if _text(row.get("queueId"))}


def _new_queue_id(url: str, company: str, role: str, used: set[str]) -> str:
    digest = hashlib.sha1(f"{url}|{_norm_identity(company)}|{_norm_identity(role)}".encode()).hexdigest()[:10].upper()
    candidate = f"DH-{digest}"
    suffix = 2
    while candidate in used:
        candidate = f"DH-{digest}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def normalize_job(job: Mapping[str, Any], used_ids: set[str] | None = None) -> dict[str, Any] | None:
    """Normalize one source record, or return ``None`` when it is unsafe.

    Fit is evaluated by the existing planner function.  This function does not
    mutate ``job`` and does not write planner state.
    """
    status = _status_for(job)
    if status is None:
        return None
    company = _first(job, "company", "employer", "organization")
    role = _first(job, "title", "role", "job_title", "jobTitle")
    if not company or not role:
        return None
    urls = _url_candidates(job)
    if not urls:
        return None

    evidence = _evidence(job)
    if _explicitly_dead(job, evidence):
        return None

    # Import lazily so importing this utility remains lightweight and testable.
    import agent1_planner
    experience = _first(job, "experience", "experienceRequired", "experience_required", "exp_req")
    location = _first(job, "location", "locations")
    fits, score, reason = agent1_planner.evaluate_job_fit(role, experience, location)
    if not fits or score < getattr(agent1_planner, "MIN_FIT_SCORE", 60):
        return None

    used = used_ids if used_ids is not None else set()
    url = urls[0]
    source_urls = urls[:]
    source_id = _first(job, "id", "job_id", "jobId")
    posted = _first(job, "postedDate", "posted_date", "published_at", "publishedAt", "created_at")
    updated = _first(job, "updated_at", "updatedAt", "discovered_at", "discoveredAt")
    portal = _first(job, "portal", "platform", "source") or "ATS"
    skills = _as_list(job.get("keyMatchingSkills") or job.get("matching_skills"))
    missing = _as_list(job.get("missingSkills") or job.get("missing_skills"))
    notes = _first(job, "notes", "description")
    row: dict[str, Any] = {
        "queueId": _new_queue_id(url, company, role, used),
        "company": company,
        "role": role,
        "platform": portal,
        "jobUrl": url,
        "canonicalUrl": url,
        "sourceUrls": source_urls,
        "location": location,
        "postedDate": posted,
        "experience": experience,
        "experienceRequired": experience,
        "matchScore": score,
        "matchReason": reason,
        "keyMatchingSkills": skills,
        "missingSkills": missing,
        "applicationMethod": _first(job, "applicationMethod", "application_method") or "ATS",
        "sourceType": "AUTOAPPLY_DISCOVERY",
        "sourceJobId": source_id,
        "status": status,
        "timestamp": updated,
        "freshness": _freshness(job, evidence),
        "liveEvidence": evidence,
    }
    if notes:
        row["notes"] = notes
    return row


def _duplicate(row: Mapping[str, Any], rows: Iterable[Mapping[str, Any]], seen_urls: set[str] | None = None,
               seen_identities: set[tuple[str, str]] | None = None) -> bool:
    url = _url_key(row.get("jobUrl"))
    identity = identity_key(row.get("company"), row.get("role"))
    if url and seen_urls is not None and url in seen_urls:
        return True
    if all(identity) and seen_identities is not None and identity in seen_identities:
        return True
    for existing in rows:
        existing_url = _url_key(existing.get("jobUrl") or existing.get("canonicalUrl"))
        if url and existing_url and url == existing_url:
            return True
        existing_identity = identity_key(existing.get("company"), existing.get("role"))
        if all(identity) and identity == existing_identity:
            return True
    return False


def discover_jobs(source_jobs: Iterable[Mapping[str, Any]], queue_rows: list[Mapping[str, Any]],
                  limit: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build a proposal and an auditable offline report without writing files."""
    started = time.perf_counter()
    source = [dict(item) for item in source_jobs if isinstance(item, Mapping)]
    existing = list(queue_rows or [])
    used_ids = _queue_ids(existing)
    seen_urls = {_url_key(row.get("jobUrl") or row.get("canonicalUrl")) for row in existing}
    seen_urls.discard("")
    seen_identities = {identity_key(row.get("company"), row.get("role")) for row in existing}
    seen_identities.discard(("", ""))
    proposed: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for item in source:
        counts["input"] += 1
        raw_status = _text(item.get("status")).casefold().replace(" ", "_")
        if raw_status not in PENDING_SOURCE_STATUSES:
            counts["not_pending"] += 1
            continue
        # Keep reasons useful in the report while normalize_job stays a small
        # pure transformer.  The checks below mirror its safety gates.
        if not _first(item, "company", "employer", "organization") or not _first(item, "title", "role", "job_title", "jobTitle"):
            counts["missing_identity"] += 1
            continue
        if not _url_candidates(item):
            counts["missing_or_root_url"] += 1
            continue
        row = normalize_job(item, used_ids)
        if row is None:
            counts["fit_or_live_rejected"] += 1
            continue
        if _duplicate(row, existing, seen_urls, seen_identities):
            counts["duplicate"] += 1
            continue
        proposed.append(row)
        seen_urls.add(_url_key(row["jobUrl"]))
        seen_identities.add(identity_key(row["company"], row["role"]))
        counts["accepted"] += 1
        if limit is not None and limit > 0 and len(proposed) >= limit:
            counts["limit_reached"] += 1
            break
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    report = {
        "mode": "dry-run",
        "source_count": len(source),
        "existing_queue_count": len(existing),
        "proposed_count": len(proposed),
        "counts": dict(counts),
        "elapsed_ms": elapsed_ms,
        "wrote": False,
    }
    return proposed, report


def run(source_path: Path | str = SOURCE_DEFAULT, queue_path: Path | str = QUEUE_DEFAULT,
        commit: bool = False, limit: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run the offline proposal; commit only when explicitly requested."""
    source_path, queue_path = Path(source_path), Path(queue_path)
    source_jobs = _source_jobs(load_json(source_path))
    import queue_store
    existing = queue_store.load(queue_path, quiet=True)
    proposed, report = discover_jobs(source_jobs, existing, limit=limit)
    if commit and proposed:
        # Append only.  queue_store.save validates and atomically replaces the
        # queue, while its shrink guard prevents an accidental destructive run.
        queue_store.save(existing + proposed, queue_path, backup=True)
        report["mode"] = "commit"
        report["wrote"] = True
        report["written_count"] = len(proposed)
    elif commit:
        report["mode"] = "commit"
        report["wrote"] = False
        report["written_count"] = 0
    return proposed, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline, no-submit autoapply discovery planner")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="report only (default)")
    mode.add_argument("--commit", action="store_true", help="append proposals via queue_store atomic save")
    parser.add_argument("--source", type=Path, default=SOURCE_DEFAULT)
    parser.add_argument("--queue", type=Path, default=QUEUE_DEFAULT)
    parser.add_argument("--limit", type=int, default=None, help="maximum accepted rows")
    args = parser.parse_args(argv)
    try:
        _, report = run(args.source, args.queue, commit=args.commit, limit=args.limit)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
