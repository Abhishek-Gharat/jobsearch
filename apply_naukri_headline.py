import bos
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
b = bos.BOS("naukri-updater")
p = 154

NEW_HEADLINE = "Frontend & Full Stack Developer (React.js, Next.js, TypeScript, Node.js) | 1.2 Yrs Exp | Immediate Joiner (0 Days Notice) | Ex-Hridayam Soft Solutions"
print(f"Target Headline ({len(NEW_HEADLINE)} chars):\n{NEW_HEADLINE}")

snap = b.snapshot(p)
ctrls = bos.parse_controls(snap)
tb = next((c for c in ctrls if c['kind'] == 'textbox' and 'webhook' not in c['label'].lower()), None)
save_btn = next((c for c in ctrls if c['label'] == 'Save'), None)

print("Target Textbox:", tb)
print("Save Button:", save_btn)

if tb and save_btn:
    b.call("act", {"page": p, "kind": "fill", "ref": tb["ref"], "value": NEW_HEADLINE})
    time.sleep(1)
    b.call("act", {"page": p, "kind": "click", "ref": save_btn["ref"]})
    time.sleep(3)
    print("Saved headline!")
    
# Verify
js_read_headline = """() => {
    const el = document.querySelector('.resume-headline-text, .headline, [data-qa="resume-headline"]');
    return el ? el.innerText.trim() : document.querySelector('.widget.resumeHeadline')?.innerText.trim() || "";
}"""
res, _ = b.call("evaluate", {"page": p, "func": js_read_headline})
print("Current headline on page:", res)
