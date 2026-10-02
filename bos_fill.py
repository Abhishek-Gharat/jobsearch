#!/usr/bin/env python3
"""H385 via BrowserOS full fill: SR posting -> I'm interested -> oneclick -> fill all (NO submit)."""
import sys, time, io, os, re
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, 'D:\\newjobs')
os.environ['BROWSER_ENGINE'] = 'browseros'
import bos

SR_POST = 'https://jobs.smartrecruiters.com/AmazaticSolutions/744000036382338?utm_source=Simplify'
RESUME = 'D:\\newjobs\\Resume.pdf'
log = []
def say(s):
    print(s, flush=True)
    log.append(s)

b = bos.BOS('h385-bos-fill')
say('engine: ' + type(b).__name__)
page = b.open(SR_POST)
say('page=%s' % page)
time.sleep(8)
u, _ = b.call("evaluate", {"page": page, "func": "() => window.location.href + ' ||| ' + document.title"})
say('landed: ' + str(u)[:300])
snap = b.snapshot(page)
controls = bos.parse_controls(snap)
btn = bos.find_control(controls, "i'm interested", kinds=("link", "button"))
say('interested: %s' % btn)
if btn:
    r, ok = b.call("act", {"page": page, "kind": "click", "ref": btn["ref"]})
    say('click ok=%s :: %s' % (ok, str(r)[:200]))
# wait for oneclick form inputs to render (up to ~45s)
st_js = """() => {
  const inputs = document.querySelectorAll('input,select,textarea');
  const vis = Array.from(inputs).filter(e=>e.offsetParent!==null && e.type!=='hidden');
  return 'URL='+window.location.href.slice(0,120)+' TOTAL='+inputs.length+' VISIBLE='+vis.length;
}"""
forms_seen = False
for i in range(9):
    time.sleep(5)
    st, _ = b.call("evaluate", {"page": page, "func": st_js})
    say('poll %d: %s' % (i, str(st)[:220]))
    m = re.search(r'VISIBLE=(\d+)', str(st))
    if m and int(m.group(1)) >= 3:
        forms_seen = True
        break
say('forms_seen=%s' % forms_seen)
snap2 = b.snapshot(page)
say('snap2 len=%d' % len(snap2))
with io.open('h385_bos_form_snap.txt','w',encoding='utf-8') as f:
    f.write(snap2)
for cc in bos.parse_controls(snap2)[:80]:
    say('CTRL %s [%s] %s' % (cc['ref'], cc['kind'], cc['label'][:70]))
det_js = """() => {
  const vis = Array.from(document.querySelectorAll('input,select,textarea')).filter(e=>e.offsetParent!==null && e.type!=='hidden');
  return vis.slice(0,60).map(e=>{
    let l='';
    try{
      if(e.labels&&e.labels[0]) l=e.labels[0].innerText.trim().slice(0,50);
      if(!l) l=(e.getAttribute('aria-label')||e.placeholder||e.name||e.id||'').slice(0,50);
    }catch(x){}
    const opts = e.tagName==='SELECT' ? (' OPTS['+Array.from(e.options).slice(0,10).map(o=>o.text.slice(0,25)).join('/')+']') : '';
    return e.tagName.toLowerCase()+':'+(e.type||'')+':'+(e.name||'').slice(0,35)+' ['+l+']'+(e.required?'*':'')+opts;
  }).join(' ~ ').slice(0,3500);
}"""
det, _ = b.call("evaluate", {"page": page, "func": det_js})
say('VISIBLE-FIELDS: ' + str(det)[:3500])
with io.open('h385_bos_form_fields.txt','w',encoding='utf-8') as f:
    f.write(str(det))
say('wrote h385_bos_form_snap.txt + h385_bos_form_fields.txt')
