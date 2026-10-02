#!/usr/bin/env python3
"""H385 via Amazatic careers page: list openings, find React.js Developer."""
import sys, time, os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, 'D:\\newjobs')
os.environ['BROWSER_ENGINE'] = 'browseros'
import bos

b = bos.BOS('h385-bos-careers')
PAGE = 14
b.call("navigate", {"page": PAGE, "url": "https://amazatic.com/careers/"})
print('waiting...', flush=True)
time.sleep(8)
cj = """() => {
  const links = Array.from(document.querySelectorAll('a')).map(a=>((a.innerText||'').trim().slice(0,60)+' -> '+(a.href||'').slice(0,140)));
  const jobish = links.filter(s=>/react|frontend|front-end|developer|engineer|designer|apply|opening|view|detail/i.test(s));
  const inputs = Array.from(document.querySelectorAll('input,select,textarea')).map(e=>{
    let l=''; try{ if(e.labels&&e.labels[0]) l=e.labels[0].innerText.trim().slice(0,40); if(!l) l=(e.getAttribute('aria-label')||e.placeholder||e.name||e.id||'').slice(0,40);}catch(x){}
    return e.tagName.toLowerCase()+':'+(e.type||'')+' ['+l+']';
  });
  return 'TITLE='+document.title+' || JOBS: '+jobish.slice(0,30).join(' ~ ').slice(0,2500)+' || INPUTS('+inputs.length+'): '+inputs.slice(0,30).join(' ~ ').slice(0,1500)+' || BODY='+document.body.innerText.slice(0,1200).replace(/\\n/g,' / ');
}"""
print(str(b.call("evaluate", {"page": PAGE, "func": cj})[0])[:4000], flush=True)
