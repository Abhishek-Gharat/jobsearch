import bos
import sys
import time
import json

sys.stdout.reconfigure(encoding="utf-8")
b = bos.BOS("naukri-updater")
p = 154

PROFILE_SUMMARY = (
    "Frontend & Full Stack Developer with 1.2+ years of production experience engineering responsive, "
    "high-performance web applications using React.js, Next.js, TypeScript, Ant Design, and Node.js. "
    "Proven track record at Hridayam Soft Solutions building production collection systems, publishing custom "
    "reusable NPM packages, and integrating complex RESTful APIs with PostgreSQL backends. Full-stack capable "
    "with Node.js, Express, MongoDB, and Python APIs. Immediate joiner (0 days notice) based in Mumbai, "
    "available for Remote, Hybrid, or Onsite roles across India."
)

js_add_summary = f"""() => {{
    // Find Add or Edit button for Profile summary
    const addBtn = Array.from(document.querySelectorAll('.widget, .card, div')).find(w => {{
        return w.innerText && w.innerText.includes('Profile summary');
    }})?.querySelector('.add, .edit, span');
    
    if (addBtn) addBtn.click();
    return "Clicked add summary";
}}"""

res, _ = b.call("evaluate", {"page": p, "func": js_add_summary})
print("Open summary modal:", res)
time.sleep(1.5)

# Fill and save
js_fill_summary = f"""() => {{
    const textarea = document.querySelector('.modal textarea, textarea[name="summary"], textarea');
    if (!textarea) return "No textarea found";
    
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
    nativeSetter.call(textarea, {json.dumps(PROFILE_SUMMARY)});
    
    textarea.dispatchEvent(new Event('input', {{ bubbles: true }}));
    textarea.dispatchEvent(new Event('change', {{ bubbles: true }}));
    
    const saveBtn = Array.from(document.querySelectorAll('button, .btn')).find(b => b.innerText.trim() === 'Save' && b.offsetParent !== null);
    if (saveBtn) {{
        saveBtn.click();
        return "Saved profile summary";
    }}
    return "Save button not found";
}}"""

res_save, _ = b.call("evaluate", {"page": p, "func": js_fill_summary})
print("Save summary result:", res_save)
time.sleep(3)

# Verify
text = b.read(p)
for line in text.splitlines():
    if "summary" in line.lower() or "hridayam" in line.lower():
        print("Read line:", line)
