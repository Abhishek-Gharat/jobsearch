import bos
import sys

sys.stdout.reconfigure(encoding="utf-8")
b = bos.BOS("naukri-updater")
p = 154

js = """() => {
    const edits = Array.from(document.querySelectorAll('.editOneTheme, .edit, .add, a[href*="edit"]')).map(el => {
        return {
            text: el.innerText.trim(),
            parentText: el.parentElement ? el.parentElement.innerText.trim().slice(0, 100) : "",
            className: el.className,
            tag: el.tagName
        };
    });
    return JSON.stringify(edits);
}"""

res, _ = b.call("evaluate", {"page": p, "func": js})
print("Result len:", len(res))
print(res[:1000])
