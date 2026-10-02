import bos
import sys
import time
import json

sys.stdout.reconfigure(encoding="utf-8")
b = bos.BOS("naukri-updater")
p = 154

NEW_SUMMARY = (
    "Frontend & Full Stack Developer with 1.2+ years of production experience building high-performance, "
    "responsive web applications using React.js, Next.js, TypeScript, Ant Design, and Node.js. "
    "Proven track record at Hridayam Soft Solutions delivering enterprise collection systems, developing "
    "reusable internal NPM component packages, and integrating complex RESTful APIs with PostgreSQL backends. "
    "Experienced in state management (Redux Toolkit, Context API), responsive UI architecture, and API integration "
    "with Node.js, Express, MongoDB, and Python APIs. Immediate joiner (0 days notice) based in Mumbai, "
    "open to Remote, Hybrid, or Onsite roles across India."
)

# 1. Click edit icon next to Profile summary
js_click = """() => {
    const title = Array.from(document.querySelectorAll('.widgetTitle')).find(s => s.innerText.trim() === 'Profile summary');
    const editBtn = title?.closest('.widgetHead')?.querySelector('.edit.icon, span.edit');
    if (editBtn) {
        editBtn.click();
        return "Clicked summary edit";
    }
    return "Not found";
}"""

res = b.call("evaluate", {"page": p, "func": js_click})
print("Click result:", res)
time.sleep(2)

# 2. Set textarea and save
js_fill_and_save = f"""() => {{
    const textarea = document.querySelector('.modal textarea, textarea[name="summary"], textarea');
    if (!textarea) return "No textarea";
    
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
    nativeSetter.call(textarea, {json.dumps(NEW_SUMMARY)});
    
    textarea.dispatchEvent(new Event('input', {{ bubbles: true }}));
    textarea.dispatchEvent(new Event('change', {{ bubbles: true }}));
    
    const saveBtn = Array.from(document.querySelectorAll('button, .btn')).find(b => b.innerText.trim() === 'Save' && b.offsetParent !== null);
    if (saveBtn) {{
        saveBtn.click();
        return "Saved summary";
    }}
    return "Save button not found";
}}"""

res_save = b.call("evaluate", {"page": p, "func": js_fill_and_save})
print("Fill & Save result:", res_save)
time.sleep(3)

# 3. Read back
js_verify = """() => {
    const title = Array.from(document.querySelectorAll('.widgetTitle')).find(s => s.innerText.trim() === 'Profile summary');
    const cont = title ? title.closest('.widgetHead')?.nextElementSibling : null;
    return cont ? cont.innerText : 'None';
}"""
print("Updated summary:", b.call("evaluate", {"page": p, "func": js_verify}))
