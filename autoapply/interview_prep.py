#!/usr/bin/env python3
"""Interview Intelligence - create prep kits for every SUBMITTED application."""
import json, re, sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
JOBS = BASE / "jobs.json"
UNIVERSE = BASE / "company_universe.json"
YC_HINT = BASE / "company_universe.json"

REACT_Q = [
    "Explain your React rendering mental model; when do re-renders happen?",
    "Hooks rules; why can't hooks be conditional?",
    "useMemo vs useCallback vs React.memo — pick and justify for a list page.",
    "Controlled vs uncontrolled components; forms at scale.",
    "State placement: local vs context vs store — decision process.",
    "Keys in lists: what breaks without stable keys?",
    "Error boundaries: what they catch and what they don't.",
    "Lazy loading / code splitting you have used (React.lazy, dynamic imports).",
]
NEXTJS_Q = [
    "SSR vs SSG vs ISR vs CSR — choose per page type and justify.",
    "Next.js data fetching: getServerSideProps vs app router fetch patterns you used.",
    "API routes: where did you place backend logic in Next.js projects?",
    "Image/font optimization defaults you relied on.",
    "Hydration mismatch bugs — how to debug and prevent.",
    "File-based routing: dynamic routes + catch-all examples.",
    "Middleware use-cases you implemented or would design.",
]
FULLSTACK_Q = [
    "Design a REST API for a resource end-to-end (routes, validation, errors).",
    "MongoDB vs PostgreSQL — schema decisions you made and trade-offs.",
    "Authentication flow you implemented (JWT storage choices, refresh strategy).",
    "CORS: what it is, how you configured it.",
    "N+1 queries / slow endpoint debugging approach.",
    "Deploying a MERN app: build, env vars, hosting choices.",
]
HR_Q = [
    "Walk me through your background in 2 minutes.",
    "Why are you looking now? (immediate availability, production experience)",
    "Tell me about the Collection System project — your specific contributions.",
    "A time you handled a production issue under deadline.",
    "Salary expectations (5.5 LPA floor per profile; state range if pressed).",
    "Notice period: immediate. Relocation: yes. Remote: comfortable.",
]

def write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text.strip() + "\n", encoding="utf-8")

def role_tracks(title):
    t = (title or "").lower()
    tracks = set()
    if "next" in t: tracks.add("nextjs")
    if "react" in t or "frontend" in t or "front-end" in t or "ui" in t or "web" in t: tracks.add("react")
    if "full stack" in t or "fullstack" in t or "mern" in t or "node" in t or "backend" in t: tracks.add("fullstack")
    return tracks or {"react"}

def main(only_new=True):
    jd = json.loads((JOBS).read_text(encoding="utf-8-sig"))
    uni = {}
    try:
        udata = json.loads((BASE/"company_universe.json").read_text(encoding="utf-8-sig"))
        uni = {c["name"].lower(): c for c in udata["companies"]}
    except Exception:
        pass
    created = []
    ivroot = BASE / "interviews"
    for j in jd["jobs"]:
        if j.get("status") != "submitted":
            continue
        co = j.get("company", "Unknown")
        slug = re.sub(r"[^a-z0-9]+", "_", co.lower()).strip("_")
        d = ivroot / slug
        marker = d / ".generated"
        if only_new and marker.exists():
            continue
        uinfo = uni.get(co.lower(), {})
        tracks = sorted(role_tracks(j.get("title")))
        ctx = {
            "company": co,
            "role": j.get("title", ""),
            "location": j.get("location", ""),
            "portal": j.get("portal", ""),
            "website": (uinfo.get("website") or "") if isinstance(uinfo, dict) else "",
            "one_liner": "",
        }
        write(d/"company_brief.md",
              f"# {co} — Company Brief\n\n- Website: {ctx['website'] or '(see careers page)'}\n"
              f"- ATS portal used: {ctx['portal']}\n- Role applied: {ctx['role']}\n- Location: {ctx['location']}\n"
              f"- Applied on: {j.get('updated_at')}\n\nResearch BEFORE interview:\n"
              f"1. Product surface from website ({ctx['website']})\n"
              f"2. Recent launches/news (public sources only)\n"
              f"3. Tech blog / engineering culture pages if present\n")
        write(d/"role_summary.md",
              f"# Role Summary — {ctx['role']}\n\nLocation: {ctx['location']}\nPortal: {ctx['portal']}\n\n"
              f"Re-read the original JD before the call; map each requirement to one line of "
              f"your real experience (HRIDAYAM Collection System / Gnapika e-commerce / REACTVIZ).\n")
        write(d/"tech_stack.md",
              f"# Likely Stack — {co}\n\nFrom posting signals: React ecosystem confirmed by JD scan.\n"
              f"Prepare talking points bridging: React/Next.js UI work -> REST integration -> "
              f"data-layer collaboration (PostgreSQL/MongoDB exposure per master profile).\n"
              f"Never claim tools not in the master profile.\n")
        banks = {"likely_react_questions.md": ("React Interview Questions", REACT_Q),
                 "likely_nextjs_questions.md": ("Next.js Interview Questions", NEXTJS_Q),
                 "likely_fullstack_questions.md": ("Full Stack Interview Questions", FULLSTACK_Q)}
        for fname, (h, bank) in banks.items():
            keep = bank
            if fname == "likely_nextjs_questions.md" and "nextjs" not in tracks:
                keep = bank[:4] + ["Only skim unless JD mentions Next.js explicitly."]
            if fname == "likely_fullstack_questions.md" and "fullstack" not in tracks:
                keep = bank[:4] + ["Skim — frontend is the primary track for this role."]
            body = "\n".join(f"{i}. {q}" for i, q in enumerate(keep, 1))
            write(d/fname, f"# {h}\n\n{body}\n")
        write(d/"hr_questions.md",
              "# HR / Behavioral\n\n" + "\n".join(f"{i}. {q}" for i, q in enumerate(HR_Q, 1)) +
              "\n\nTruthfulness rule: numbers/experience ONLY from master profile.\n")
        write(d/"salary_estimate.md",
              "# Salary Guidance\n\n- Floor (per master profile): 5.5 LPA\n"
              "- Expected: 5.5 LPA · Ideal: 6-7+ LPA\n- State a RANGE only if pressed; never invent current CTC.\n"
              "- If stipend/internship framing appears, confirm conversion terms before proceeding.\n")
        submitted_at = j.get("updated_at") or datetime.now(timezone.utc).isoformat()
        write(d/"followup_plan.md",
              f"# Follow-up Plan (drafts only — NEVER auto-send)\n\nSubmitted: {submitted_at}\n\n"
              f"- Day +5: polite status check draft (prepare, review, send manually)\n"
              f"- Day +10: value-add nudge (attach REACTVIZ link / portfolio update)\n"
              f"- Day +21: graceful close-or-park note\n\nDrafts live in followups.json\n")
        marker.write_text("", encoding="utf-8")
        created.append(slug)
    print(f"interview kits created: {len(created)}")
    for s in created:
        print("  interviews/", s, sep="")


if __name__ == "__main__":
    sys.exit(main())
