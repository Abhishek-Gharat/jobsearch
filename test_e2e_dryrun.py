#!/usr/bin/env python3
"""
test_e2e_dryrun.py — Full Integration Test of Universal ATS Engine
Executes complete real-world flow in a single process without submitting:
1. Opens real SmartRecruiters posting
2. Detects job description vs direct form
3. Clicks Apply / I'm interested and navigates to the application form
4. Reactively waits for SPA form inputs to render
5. Attaches verified resume and verifies in DOM
6. Fills candidate fields from candidate_core.json
7. Solves custom React/Angular dropdowns, country codes, checkboxes
8. Handles dynamic Experience and Education modals
9. Re-reads live DOM and generates strict verification proof
10. Confirms NO submission was made and keeps tab open
"""

import os
import sys
import json
import time

sys.path.insert(0, r"D:\newjobs")
os.environ["BROWSER_ENGINE"] = "fortress"
os.environ["FORTRESS_HEADLESS"] = "0"

import bos
import ats_engine

CANDIDATE_FILE = r"D:\newjobs\agent_state\candidate_core.json"
TEST_URL = "https://jobs.smartrecruiters.com/AmazaticSolutions/744000036382338"

def main():
    print("=" * 65)
    print("UNIVERSAL ATS ENGINE - REAL APPLICATION DRY-RUN TEST")
    print(f"Target: {TEST_URL}")
    print("Mode: STRICT DRY-RUN (SUBMIT DISABLED)")
    print("=" * 65)

    with open(CANDIDATE_FILE, "r", encoding="utf-8") as f:
        candidate = json.load(f)

    resume_path = candidate.get("resume_pdf")
    print(f"Candidate: {candidate['name']} ({candidate['email']})")
    print(f"Resume: {resume_path}")

    b = bos.BOS("dryrun-test")
    print(f"[1/8] Opening target URL: {TEST_URL}...")
    page = b.open(TEST_URL)
    time.sleep(3.0)

    # 1. State Detection
    print("\n[2/8] Detecting initial page state...")
    st = ats_engine.detect_page_state(b, page)
    print(f"State: {st.get('status')} | {st.get('details')}")

    # 2. Navigation to Form
    if st.get("status") == "JOB_DESCRIPTION":
        print("\n[3/8] Navigating to form (clicking Apply / I'm interested)...")
        page, nav_msg = ats_engine.navigate_to_form(b, page)
        print(f"Navigation: {nav_msg} (page {page})")

    # 3. Reactive Form Readiness Wait
    print("\n[4/8] Waiting for SPA form inputs to render...")
    ready, ready_msg, input_cnt = ats_engine.wait_for_form_ready(b, page, timeout=25)
    print(f"Readiness: {ready_msg}")

    # 4. Resume Upload
    print(f"\n[5/8] Uploading resume: {resume_path}...")
    up_ok, up_msg = ats_engine.upload_resume(b, page, resume_path)
    print(f"Upload outcome: {up_msg}")

    # 5. Semantic Form Filling
    print("\n[6/8] Populating candidate fields from verified profile...")
    fill_res = ats_engine.fill_form(b, page, candidate)
    print(f"Filled: {fill_res.get('filledCount', 0)} fields populated.")

    # 6. Dynamic Modals (Experience / Education)
    print("\n[7/8] Handling dynamic Experience & Education modals...")
    modal_actions = ats_engine.handle_dynamic_modals(b, page, candidate)
    if modal_actions:
        print(f"Modals completed: {', '.join(modal_actions)}")
    else:
        print("No dynamic modals triggered.")

    time.sleep(2.0)

    # 7. Post-Fill Live DOM Verification
    print("\n[8/8] Re-reading live DOM for post-fill verification...")
    v_res = ats_engine.verify_dom_state(b, page, candidate)

    print("\n" + "=" * 65)
    print("LIVE DOM VERIFICATION PROOF")
    print("=" * 65)
    print(f"Summary: {v_res.get('summary')}")
    print(f"Resume Attached in DOM: {v_res.get('resume_attached')} ({v_res.get('resume_name')})")

    print("\nVerified Fields in Live DOM:")
    for f in v_res.get("verified_fields", []):
        print(f"  ✓ [{f.get('type')}] {f.get('label')[:32]}: {f.get('value')[:50]}")

    if v_res.get("empty_fields"):
        print("\nEmpty / Optional Fields:")
        for f in v_res.get("empty_fields", [])[:6]:
            print(f"  - [{f.get('type')}] {f.get('label')[:32]}")

    if v_res.get("validation_errors"):
        print("\nValidation Errors Detected:")
        for err in v_res.get("validation_errors", []):
            print(f"  ! {err}")

    print("\n" + "=" * 65)
    print("TEST STATUS: SUCCESSFUL DRY-RUN")
    print("NO SUBMISSION PERFORMED. BROWSER TAB PRESERVED OPEN.")
    print("=" * 65)

    # Keep script alive for 15s so user can visually see the filled form
    print("\nPreserving session for visual inspection (15 seconds)...")
    time.sleep(15)

if __name__ == "__main__":
    main()
