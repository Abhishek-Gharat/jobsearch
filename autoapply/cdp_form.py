import asyncio, json, sys, time, random, urllib.request
import websockets

CDP = "http://127.0.0.1:9222/json/list"

def targets():
    with urllib.request.urlopen(CDP, timeout=5) as r:
        return json.load(r)

async def send(ws, mid, method, params=None):
    await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            return msg

async def connect():
    ts = [t for t in targets() if t.get("type") == "page"]
    return await websockets.connect(ts[0]["webSocketDebuggerUrl"], max_size=100 * 1024 * 1024)

async def ev(ws, mid, expr, await_promise=False, byvalue=True):
    r = await send(ws, mid, "Runtime.evaluate", {
        "expression": expr, "returnByValue": byvalue, "awaitPromise": await_promise, "userGesture": True})
    res = r.get("result", {})
    if res.get("exceptionDetails"):
        return "EXC: " + json.dumps(res["exceptionDetails"].get("exception", {}).get("description", ""))[:300]
    return res.get("result", {}).get("value")

JS_COLLECT = r"""
(() => {
  window.__els = [];
  const out = [];
  const push = (el, path) => {
    if (!el) return;
    window.__els.push(el);
    const i = window.__els.length - 1;
    const r = el.getBoundingClientRect();
    let label = (el.getAttribute('aria-label') || el.placeholder || el.name || el.id || '');
    let txt = (el.innerText || el.textContent || '').trim().slice(0, 60);
    out.push({i, tag: el.tagName.toLowerCase(), type: (el.type||''), name: el.name||'', id: el.id||'',
      ph: el.placeholder||'', value: (el.value||'').toString().slice(0,50), label, text: txt,
      x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
      vis: !!(r.width && r.height)});
  };
  const walk = (root, depth) => {
    if (depth > 25) return;
    root.querySelectorAll('*').forEach(el => {
      const tag = el.tagName.toLowerCase();
      const isField = ['input','textarea','select','button'].includes(tag);
      const isCand = isField || el.getAttribute('role') === 'button' ||
        (tag === 'a' && el.getAttribute('href')) || tag === 'label' || tag === 'summary';
      if (isCand) push(el, '');
      if (el.shadowRoot) walk(el.shadowRoot, depth + 1);
    });
  };
  walk(document, 0);
  return JSON.stringify({count: out.length, els: out});
})()
"""

JS_TEXT = """JSON.stringify({url:location.href, title:document.title,
 body:(document.body.innerText||'').replace(/\\n{2,}/g,'\\n').slice(0,5000)})"""

async def mouse(ws, mid, typ, x, y, button="left", clickCount=0):
    p = {"type": typ, "x": x, "y": y, "button": button}
    if clickCount:
        p["clickCount"] = clickCount
    if typ == "mouseWheel":
        p.update({"deltaX": 0, "deltaY": y})
    return await send(ws, mid, "Input.dispatchMouseEvent", p)

async def type_char(ws, mid, ch):
    if ch == " ":
        key, code, vk, txt = " ", "Space", 32, " "
    elif ch.isalpha():
        key, code, vk, txt = ch, "Key" + ch.upper(), ord(ch.upper()), ch
    elif ch.isdigit():
        key, code, vk, txt = ch, "Digit" + ch, ord(ch), ch
    else:
        key, code, vk, txt = ch, "", 0, ch
    await send(ws, mid, "Input.dispatchKeyEvent", {"type": "keyDown", "key": key, "code": code, "text": txt, "windowsVirtualKeyCode": vk})
    await send(ws, mid + 1, "Input.dispatchKeyEvent", {"type": "keyUp", "key": key, "code": code, "windowsVirtualKeyCode": vk})

WRONG = {"a":"s","b":"v","c":"x","d":"f","e":"w","f":"g","g":"h","h":"j","i":"u","j":"k","k":"l","l":"k",
         "m":"n","n":"m","o":"i","p":"o","q":"w","r":"e","s":"a","t":"y","u":"y","v":"c","w":"q","x":"z",
         "y":"t","z":"x","A":"S","B":"V","C":"X","D":"F","E":"W","F":"G","G":"H","H":"J","I":"U","J":"K",
         "K":"L","L":"K","M":"N","N":"M","O":"I","P":"O","Q":"W","R":"E","S":"A","T":"Y","U":"Y","V":"C",
         "W":"Q","X":"Z","Y":"T","Z":"X"}

async def human_type(ws, mid, text):
    for ch in text:
        if random.random() < 0.02 and ch in WRONG:
            await type_char(ws, mid, WRONG[ch]); mid += 5
            await asyncio.sleep(random.uniform(0.12, 0.18))
            await send(ws, mid, "Input.dispatchKeyEvent", {"type": "keyDown", "key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8})
            await send(ws, mid + 1, "Input.dispatchKeyEvent", {"type": "keyUp", "key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8})
            mid += 5
            await asyncio.sleep(random.uniform(0.06, 0.14))
        await type_char(ws, mid, ch); mid += 5
        if random.random() < 0.08:
            await asyncio.sleep(random.uniform(0.3, 0.8))
        else:
            await asyncio.sleep(min(0.25, max(0.02, random.gauss(0.08, 0.03))))
    return mid

async def get_rect(ws, mid, idx):
    return await ev(ws, mid, """(() => { const el = window.__els[%d]; if(!el) return 'NOEL';
      el.scrollIntoView({block:'center', behavior:'instant'});
      const r = el.getBoundingClientRect();
      return JSON.stringify({x:r.x+Math.min(r.width/2, 30), y:r.y+r.height/2, w:r.width, h:r.height}); })()""" % idx)

async def main():
    cmd = sys.argv[1]
    ws = await connect()
    mid = 100
    try:
        await send(ws, 1, "Page.enable")
        await send(ws, 2, "Runtime.enable")
        if cmd == "collect":
            r = await ev(ws, 3, JS_COLLECT)
            print(r)
        elif cmd == "text":
            r = await ev(ws, 4, JS_TEXT)
            print(r)
        elif cmd == "clickidx":
            idx = int(sys.argv[2])
            rect = await get_rect(ws, 5, idx)
            if not rect or rect == 'NOEL':
                print("NOEL"); return
            pt = json.loads(rect)
            if pt["w"] == 0:
                print("INVISIBLE " + str(rect)); return
            await mouse(ws, 6, "mouseMoved", pt["x"], pt["y"])
            await asyncio.sleep(random.uniform(0.06, 0.18))
            await mouse(ws, 7, "mousePressed", pt["x"], pt["y"], clickCount=1)
            await asyncio.sleep(random.uniform(0.03, 0.12))
            await mouse(ws, 8, "mouseReleased", pt["x"], pt["y"], clickCount=1)
            await asyncio.sleep(random.uniform(0.15, 0.4))
            print("CLICKED idx=%d %s" % (idx, rect))
        elif cmd == "clicktext":
            needle = sys.argv[2]
            r = await ev(ws, 9, """(() => { const n = %s;
               const all = window.__els || [];
               let best = -1;
               all.forEach((el, i) => { const t = ((el.innerText||'')+' '+(el.getAttribute('aria-label')||'')+' '+(el.value||'')).trim();
                 if (best<0 && t.toLowerCase().includes(n.toLowerCase())) best = i; });
               return best; })()""" % json.dumps(needle))
            if r is None or int(r) < 0:
                print("NOTEXT " + str(r)); return
            await main_click(ws, int(r))
        elif cmd == "typeidx":
            idx = int(sys.argv[2])
            text = sys.argv[3]
            setup = await ev(ws, 10, """(() => { const el = window.__els[%d]; if(!el) return 'NOEL';
              el.scrollIntoView({block:'center', behavior:'instant'});
              el.focus(); if (el.select) el.select();
              if ('value' in el && !('disabled' in el && false)) { }
              const r = el.getBoundingClientRect();
              return JSON.stringify({x:r.x+Math.min(r.width/2,30), y:r.y+r.height/2}); })()""" % idx)
            if not setup or setup == 'NOEL':
                print("NOEL"); return
            pt = json.loads(setup)
            await mouse(ws, 11, "mouseMoved", pt["x"], pt["y"])
            await asyncio.sleep(random.uniform(0.05, 0.15))
            await mouse(ws, 12, "mousePressed", pt["x"], pt["y"], clickCount=1)
            await asyncio.sleep(random.uniform(0.03, 0.1))
            await mouse(ws, 13, "mouseReleased", pt["x"], pt["y"], clickCount=1)
            await asyncio.sleep(random.uniform(0.1, 0.3))
            await ev(ws, 14, """(() => { const el = window.__els[%d]; if(el && 'value' in el){ el.focus(); document.execCommand && (el.value=''); el.dispatchEvent(new Event('input',{bubbles:true})); } return 'cleared'; })()""" % idx)
            await human_type(ws, 15, text)
            await asyncio.sleep(random.uniform(0.2, 0.5))
            v = await ev(ws, 16, """(() => { const el = window.__els[%d]; return el ? (el.value!==undefined? String(el.value).slice(0,80) : 'novalue') : 'NOEL'; })()""" % idx)
            print("TYPED idx=%d value=%s" % (idx, v))
        elif cmd == "setval":
            # non-human fast fill (for selects/datalist)
            idx = int(sys.argv[2]); text = sys.argv[3]
            r = await ev(ws, 17, """(() => { const el = window.__els[%d]; if(!el) return 'NOEL';
              el.focus(); const s = Object.getOwnPropertyDescriptor(el.tagName==='SELECT'?HTMLSelectElement.prototype:HTMLInputElement.prototype, 'value');
              el.value = %s; el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true}));
              return String(el.value).slice(0,80); })()""" % (idx, json.dumps(text)))
            print("SETVAL " + str(r))
        elif cmd == "checkidx":
            idx = int(sys.argv[2])
            r = await ev(ws, 18, """(() => { const el = window.__els[%d]; if(!el) return 'NOEL';
              if (el.type === 'checkbox') { if (!el.checked) { el.click(); }
                el.dispatchEvent(new Event('change',{bubbles:true})); return String(el.checked); }
              el.click(); return 'clicked'; })()""" % idx)
            print("CHECK " + str(r))
        elif cmd == "setfiles":
            # argv[2] = file input index in __els OR css-ish fallback; argv[3] = path
            idx = int(sys.argv[2]); path = sys.argv[3]
            r = await ev(ws, 19, """(() => { const el = window.__els[%d]; if(!el) return JSON.stringify({ok:false,why:'NOEL'});
              return JSON.stringify({ok:true, tag:el.tagName, type:el.type}); })()""" % idx)
            info = json.loads(r) if r and not r.startswith("EXC") else {"ok": False}
            if not info.get("ok"):
                print("SETFILE " + str(r)); return
            r3 = await send(ws, 23, "Runtime.evaluate", {"expression": "window.__els[%d]" % idx, "returnByValue": False})
            oid = r3.get("result", {}).get("result", {}).get("objectId")
            r4 = await send(ws, 24, "DOM.resolveNode", {"objectId": oid})
            boid = r4.get("result", {}).get("object", {}).get("backendNodeId")
            r5 = await send(ws, 25, "DOM.setFileInputFiles", {"files": [path], "backendNodeId": boid})
            await asyncio.sleep(0.8)
            fname = await ev(ws, 26, """(() => { const el = window.__els[%d]; if(!el) return 'NOEL';
              if (el.files && el.files.length) return el.files[0].name;
              const host = el.getRootNode().host || el.parentElement;
              return ((host && host.innerText) || '').slice(0,150); })()""" % idx)
            print("SETFILES " + json.dumps(r5.get("result", {})) + " ui=" + str(fname))
        elif cmd == "press":
            key = sys.argv[2]
            codes = {"Enter": (13,), "Tab": (9,), "Escape": (27,), "Backspace": (8,), "ArrowDown": (40,), "ArrowUp": (38,)}
            vk = codes.get(key, (0,))[0]
            await send(ws, 40, "Input.dispatchKeyEvent", {"type": "keyDown", "key": key, "code": key, "windowsVirtualKeyCode": vk})
            await send(ws, 41, "Input.dispatchKeyEvent", {"type": "keyUp", "key": key, "code": key, "windowsVirtualKeyCode": vk})
            time.sleep(random.uniform(0.1, 0.3))
            print("pressed " + key)
        elif cmd == "sleep":
            time.sleep(float(sys.argv[2])); print("slept")
        elif cmd == "scroll":
            dy = int(sys.argv[2])
            await mouse(ws, 50, "mouseMoved", 400, 300)
            await send(ws, 51, "Input.dispatchMouseEvent", {"type": "mouseWheel", "x": 400, "y": 300, "deltaX": 0, "deltaY": dy})
            time.sleep(random.uniform(0.8, 1.6))
            print("scrolled " + str(dy))
        else:
            print("unknown cmd " + cmd)
    finally:
        await ws.close()

async def main_click(ws, idx):
    rect = await get_rect(ws, 60, idx)
    if not rect or rect == 'NOEL':
        print("NOEL"); return
    pt = json.loads(rect)
    await mouse(ws, 61, "mouseMoved", pt["x"], pt["y"])
    await asyncio.sleep(random.uniform(0.06, 0.18))
    await mouse(ws, 62, "mousePressed", pt["x"], pt["y"], clickCount=1)
    await asyncio.sleep(random.uniform(0.03, 0.12))
    await mouse(ws, 63, "mouseReleased", pt["x"], pt["y"], clickCount=1)
    await asyncio.sleep(random.uniform(0.15, 0.4))
    print("CLICKED idx=%d %s" % (idx, rect))

asyncio.run(main())
