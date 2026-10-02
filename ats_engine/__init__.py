"""
ATS Engine - Universal Browser Form Automation Suite
Modular, zero-guesswork ATS navigation, dynamic form detection,
resume upload, semantic field solving, and live DOM verification.
"""

from .ats_detector import detect_page_state, navigate_to_form, wait_for_form_ready
from .ats_uploader import upload_resume
from .ats_solver import fill_form, handle_dynamic_modals
from .ats_verifier import verify_dom_state

__all__ = [
    "detect_page_state",
    "navigate_to_form",
    "wait_for_form_ready",
    "upload_resume",
    "fill_form",
    "handle_dynamic_modals",
    "verify_dom_state"
]
