#!/usr/bin/env python3
"""H385 BrowserOS clean retry: SR posting -> I'm interested -> oneclick -> fill all (NO submit)."""
import sys, time, io, os, re
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, 'D:\\newjobs')
os.environ['BROWSER_ENGINE'] = 'browseros'
import bos

SR_POST = 'https://jobs.smartrecruiters.com/AmazaticSolutions/744000036382338?utm_source=Simplify'
RESUME = 'D:\\newjobs\\Resume.pdf'
P = {
 'first': 'Alex', 'last': 'Morgan', 'email': 'candidate@example.com',
 'phone': '9876543210', 'loc': 'Mumbai, Maharashtra, India',
 'li': 'https://www.linkedin.com/in/developer-portfolio01/',
 'gh': 'https://github.com/developer-portfolio/',
 'pf': 'https://portfolio-developer-portfolio.vercel.app',
}
log = []
def say(s):
    print(s, flush=True)
    log.append(s)

b = bos.BOS('h385-bos-retry')
say('engine: ' + type(b).__name__)
page = b.open(SR_POST)
say('page=%s' % page)
time.sleep(8)
snap = b.snapshot(page)
controls = bos.parse_controls(snap)
btn = bos.find_control(controls, "i'm interested", kinds=("link", "button"))
say('interested: %s' % btn)
if btn:
    say('click: %s' % str(b.call("act", {"page": page, "kind": "click", "ref": btn["ref"]}))[:150])
time.sleep(20)
u, _ = b.call("evaluate", {"page": page, "func": "() => window.location.href.slice(0,150)+' ||| '+document.title.slice(0,60)+' ||| bodylen='+(document.body?document.body.innerText.length:0)"})
say('state: ' + str(u)[:300])
st, _ = b.call("evaluate", {"page": page, "func": """() => {
  const v = Array.from(document.querySelectorAll('input,select,textarea')).filter(e=>e.offsetParent!==null && e.type!=='hidden');
  return 'VISIBLE='+v.length+' || '+v.slice(0,40).map(e=>{
    let l=''; try{ if(e.labels&&e.labels[0]) l=e.labels[0].innerText.trim().slice(0,45); if(!l) l=(e.getAttribute('aria-label')||e.placeholder||e.name||e.id||'').slice(0,45);}catch(x){}
    return e.tagName.toLowerCase()+':'+(e.type||'')+' ['+l+']'+(e.required?'*':'');
  }).join(' ~ ').slice(0,2500);
}"""})
say('FIELDS: ' + str(st)[:2800])
with io.open('h385_bos_retry.txt','w',encoding='utf-8') as f:
    f.write('\n'.join(log) + '\n---FIELDS---\n' + str(st))
say('wrote h385_bos_retry.txt')
