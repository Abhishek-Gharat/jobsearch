import bos
import sys
import time
import json

sys.stdout.reconfigure(encoding="utf-8")
b = bos.BOS("naukri-updater")
p = 154

NEW_HEADLINE = "Frontend & Full Stack Developer (React.js, Next.js, TypeScript, Node.js) | 1.2 Yrs Exp | Immediate Joiner (0 Days Notice) | Ex-Hridayam Soft Solutions"

# Check if modal is currently open, if not click edit icon
js_open_and_set = f"""() => {{
    let textarea = document.querySelector('textarea.resumeHeadlineTxt, .modal textarea, textarea');
    if (!textarea) {{
        const el = Array.from(document.querySelectorAll('span.edit.icon')).find(s => {{
            return s.parentElement && s.parentElement.innerText.includes('Resume headline');
        }});
        if (el) el.click();
    }}
}}"""
b.call("evaluate", {"page": p, "func": js_open_and_set})
time.sleep(1.5)

# Set textarea value and dispatch React / input events
js_fill_and_save = f"""() => {{
    const textarea = document.querySelector('textarea.resumeHeadlineTxt, .modal textarea, textarea');
    if (!textarea) return "No textarea";
    
    // Set value directly
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
    nativeSetter.call(textarea, {json.dumps(NEW_HEADLINE)});
    
    textarea.dispatchEvent(new Event('input', {{ bubbles: true }}));
    textarea.dispatchEvent(new Event('change', {{ bubbles: true }}));
    
    // Find Save button in modal
    const saveBtn = Array.from(document.querySelectorAll('button, .btn')).find(b => b.innerText.trim() === 'Save' && b.offsetParent !== null);
    if (saveBtn) {{
        saveBtn.click();
        return "Saved via JS click";
    }}
    return "Value set, save btn not found";
}}"""

import json
res, _ = b.call("evaluate", {"page": p, "func": js_fill_and_save})
print("Fill and save result:", res)
time.sleep(3)

# Verify
text = b.read(p)
for line in text.splitlines():
    if "headline" in line.lower() or "hridayam" in line.lower():
        print("Read line:", line)
