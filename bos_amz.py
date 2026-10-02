#!/usr/bin/env python3
"""H385 via Amazatic site: navigate own tab to amazatic.com, find React.js Developer posting/form."""
import sys, time, io, os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, 'D:\\newjobs')
os.environ['BROWSER_ENGINE'] = 'browseros'
import bos

b = bos.BOS('h385-bos-amz')
# close dead tracker tab, reuse page 14
try:
    b.close(12)
    print('closed 12', flush=True)
except Exception as e:
    print('close12: %s' % str(e)[:100], flush=True)
PAGE = 14
b.call("navigate", {"page": PAGE, "url": "https://amazatic.com/"})
print('navigating to amazatic.com, waiting...', flush=True)
time.sleep(8)
u, _ = b.call("evaluate", {"page": PAGE, "func": "() => window.location.href + ' ||| ' + document.title"})
print('landed: ' + str(u)[:300], flush=True)
cj = """() => {
  const links = Array.from(document.querySelectorAll('a')).map(a=>((a.innerText||'').trim().slice(0,50)+' -> '+(a.href||'').slice(0,120)));
  const jobish = links.filter(s=>/career|job|hiring|join|opening|react|apply/i.test(s));
  return 'TITLE='+document.title + ' || JOBLINKS: ' + jobish.slice(0,25).join(' ~ ').slice(0,2500) + ' || BODY='+document.body.innerText.slice(0,800).replace(/\\n/g,' / ');
}"""
print(str(b.call("evaluate", {"page": PAGE, "func": cj})[0])[:3000], flush=True)
