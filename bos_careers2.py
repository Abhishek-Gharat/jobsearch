#!/usr/bin/env python3
"""H385 careers via JS navigation (avoids load-event hang)."""
import sys, time, os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, 'D:\\newjobs')
os.environ['BROWSER_ENGINE'] = 'browseros'
import bos

b = bos.BOS('h385-bos-careers2')
PAGE = 14
r, ok = b.call("evaluate", {"page": PAGE, "func": "() => { window.location.href='https://amazatic.com/careers/'; return 'navigating'; }"})
print('nav: %s %s' % (ok, str(r)[:150]), flush=True)
for i in range(6):
    time.sleep(5)
    u, _ = b.call("evaluate", {"page": PAGE, "func": "() => window.location.href + ' ||| ' + document.title + ' ||| bodylen=' + (document.body?document.body.innerText.length:0)"})
    print('poll %d: %s' % (i, str(u)[:250]), flush=True)
    if 'careers' in str(u).lower():
        break
cj = """() => {
  const links = Array.from(document.querySelectorAll('a')).map(a=>((a.innerText||'').trim().slice(0,60)+' -> '+(a.href||'').slice(0,140)));
  const jobish = links.filter(s=>/react|frontend|front-end|developer|engineer|designer|apply|opening|view|detail/i.test(s));
  return 'JOBS: '+jobish.slice(0,30).join(' ~ ').slice(0,2500)+' || BODY='+document.body.innerText.slice(0,1000).replace(/\\n/g,' / ');
}"""
print(str(b.call("evaluate", {"page": PAGE, "func": cj})[0])[:3500], flush=True)
