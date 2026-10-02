#!/usr/bin/env python3
"""Deep-diagnose oneclick page: iframes, shadow DOM, buttons, manual-option."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, 'D:\\newjobs')
os.environ['BROWSER_ENGINE'] = 'browseros'
import bos

PAGE = 14
b = bos.BOS('h385-bos-deep')
t = b.read(PAGE)
print('READ head: ' + str(t)[:2000], flush=True)
dj = """() => {
  const out = [];
  out.push('URL=' + window.location.href.slice(0,150));
  const frames = Array.from(document.querySelectorAll('iframe')).map(f=>(f.src||'nosrc').slice(0,100)+' '+f.offsetWidth+'x'+f.offsetHeight);
  out.push('IFRAMES('+frames.length+')='+frames.join(' | ').slice(0,600));
  const shadows = [];
  document.querySelectorAll('*').forEach(el=>{ if(el.shadowRoot) shadows.push(el.tagName+'.'+(el.className||'').toString().slice(0,40)); });
  out.push('SHADOWS='+JSON.stringify(shadows).slice(0,400));
  const btns = Array.from(document.querySelectorAll('button,a')).map(x=>(x.innerText||'').trim()).filter(Boolean);
  out.push('CLICKS='+JSON.stringify([...new Set(btns)]).slice(0,600));
  const exp Lans = [];
  out.push('BODYLEN='+document.body.innerText.length);
  return out.join(' || ');
}"""
# fix accidental space typo by rebuilding string safely
dj = dj.replace('exp Lans', 'x')
r, _ = b.call("evaluate", {"page": PAGE, "func": dj})
print('DEEP: ' + str(r)[:2500], flush=True)
