#!/usr/bin/env python3
"""Final gate check on oneclick page: body text, challenge markers, API init status."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, 'D:\\newjobs')
os.environ['BROWSER_ENGINE'] = 'browseros'
import bos

PAGE = 15
b = bos.BOS('h385-bos-gate')
t = b.read(PAGE)
print('BODY: ' + str(t)[:1200], flush=True)
g, _ = b.call("evaluate", {"page": PAGE, "func": """() => {
  const txt = document.body.innerText;
  const marks = ['human','captcha','challenge','verify you','robot','blocked','denied','forbidden','attention','perimeterx','cloudflare','press & hold','checkbox'];
  const hit = marks.filter(m=>txt.toLowerCase().includes(m));
  const btns = Array.from(document.querySelectorAll('button,a')).map(x=>(x.innerText||'').trim()).filter(Boolean);
  return 'BODYLEN='+txt.length+' || GATEMARKS='+JSON.stringify(hit)+' || CLICKS='+JSON.stringify([...new Set(btns)]).slice(0,500);
}"""})
print('GATE: ' + str(g)[:1500], flush=True)
a, _ = b.call("evaluate", {"page": PAGE, "func": """async () => {
  const urls = [
    'https://api.smartrecruiters.com/v1/companies/AmazaticSolutions/postings/744000036382338',
    'https://jobs.smartrecruiters.com/oneclick-ui/api/config'
  ];
  const out = [];
  for (const u of urls) {
    try { const r = await fetch(u); out.push(u.slice(0,60)+' -> '+r.status); }
    catch(e) { out.push(u.slice(0,60)+' -> ERR '+String(e).slice(0,80)); }
  }
  return out.join(' || ');
}"""})
print('API: ' + str(a)[:600], flush=True)
