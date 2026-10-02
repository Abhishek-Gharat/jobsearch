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
    if not ts:
        raise SystemExit("no page target")
    return await websockets.connect(ts[0]["webSocketDebuggerUrl"], max_size=100 * 1024 * 1024)

async def ev(ws, mid, expr, await_promise=False):
    r = await send(ws, mid, "Runtime.evaluate", {
        "expression": expr, "returnByValue": True, "awaitPromise": await_promise,
        "userGesture": True})
    return r.get("result", {}).get("result", {}).get("value")

JS_SNAP = r"""() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none'; };
  const out = [];
  const sel = el => {
    if (el.id) return '#'+CSS.escape(el.id);
    let p = el.tagName.toLowerCase();
    if (el.name) p += '[name="'+el.name+'"]';
    else if (el.type) p += '[type="'+el.type+'"]';
    else if (el.className && typeof el.className==='string') p += '.'+el.className.trim().split(/\s+/).slice(0,2).join('.');
    return p;
  };
  document.querySelectorAll('input,textarea,select,button,a[href],label,[role=button],[role=dialog],[role=tab]').forEach(el=>{
    if (!vis(el)) return;
    const r = el.getBoundingClientRect();
    out.push({tag:el.tagName.toLowerCase(), type:el.type||'', name:el.name||'', id:el.id||'',
      ph:el.placeholder||'', value:(el.value||'').slice(0,60), text:(el.innerText||el.textContent||'').trim().slice(0,80),
      aria:el.getAttribute('aria-label')||'', sel:sel(el),
      x:Math.round(r.x), y:Math.round(r.y), w:Math.round(r.width), h:Math.round(r.height)});
  });
  const heads = [...document.querySelectorAll('h1,h2,h3,h4')].filter(vis).map(h=>h.innerText.trim().slice(0,120));
  const bodyTxt = document.body.innerText.replace(/\s+/g,' ').slice(0, 4000);
  return JSON.stringify({url:location.href, title:document.title, heads, controls:out, body:bodyTxt}, null, 1);
}"""

async def main():
    cmd = sys.argv[1]
    ws = await connect()
    mid = 100
    try:
        await send(ws, 1, "Page.enable")
        await send(ws, 2, "Runtime.enable")
        if cmd == "nav":
            await send(ws, 3, "Page.navigate", {"url": sys.argv[2]})
            time.sleep(float(sys.argv[3]) if len(sys.argv) > 3 else 3)
            print(await ev(ws, 4, "JSON.stringify({title:document.title,url:location.href,ready:document.readyState})"))
        elif cmd == "eval":
            ap = len(sys.argv) > 3 and sys.argv[3] == "await"
            print(await ev(ws, 5, sys.argv[2], ap))
        elif cmd == "snap":
            print(await ev(ws, 6, "(" + JS_SNAP + ")()"))
        elif cmd == "sleep":
            time.sleep(float(sys.argv[2]))
            print("slept")
        elif cmd == "click":
            selx = sys.argv[2]
            expr = """(() => { let el;
              if (%s) { try { el = document.querySelector(%s); } catch(e){} }
              if (!el) { el = [...document.querySelectorAll('*')].find(e => (e.innerText||'').trim() === %s && e.offsetParent!==null); }
              if (!el) return 'NOTFOUND';
              el.scrollIntoView({block:'center'});
              const r = el.getBoundingClientRect();
              return JSON.stringify({x:r.x+r.width/2, y:r.y+r.height/2, tag:el.tagName}); })()""" % (
                json.dumps(selx.startswith('#') or selx.startswith('.') or '[' in selx or selx.startswith('input') or selx.startswith('button') or selx.startswith('a') or selx.startswith('textarea') or selx.startswith('select')),
                json.dumps(selx), json.dumps(selx))
            res = await ev(ws, 7, expr)
            if not res or res == 'NOTFOUND':
                print("CLICK_FAIL:" + str(res)); return
            pt = json.loads(res)
            x, y = pt["x"], pt["y"]
            await send(ws, 8, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
            await asyncio.sleep(random.uniform(0.05, 0.15))
            await send(ws, 9, "Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
            await asyncio.sleep(random.uniform(0.04, 0.12))
            await send(ws, 10, "Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
            print("CLICKED " + str(pt))
        elif cmd == "type":
            # human-like typing into selector; clears existing value first
            target = sys.argv[2]
            text = sys.argv[3]
            setup = """(() => { let el = document.querySelector(%s); if (!el) return 'NOTFOUND';
              el.scrollIntoView({block:'center'});
              const r = el.getBoundingClientRect();
              const opts = {bubbles:true, cancelable:true, view:window};
              el.focus();
              if ('value' in el) { const s = el.selectionStart, e2 = el.selectionEnd;
                document.execCommand('selectAll', false, null); document.execCommand('delete', false, null); }
              return JSON.stringify({x:Math.round(r.x+Math.min(r.width/2,40)), y:Math.round(r.y+r.height/2), tag:el.tagName, name:el.name||el.id||''}); })()""" % json.dumps(target)
            res = await ev(ws, 11, setup)
            if not res or res == 'NOTFOUND':
                print("TYPE_FAIL:" + str(res)); return
            pt = json.loads(res)
            x, y = pt["x"], pt["y"]
            await send(ws, 12, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
            await asyncio.sleep(random.uniform(0.05, 0.15))
            await send(ws, 13, "Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
            await asyncio.sleep(random.uniform(0.03, 0.1))
            await send(ws, 14, "Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
            await asyncio.sleep(random.uniform(0.1, 0.3))
            wrong_keys = {"a":"s","b":"v","c":"x","d":"f","e":"w","f":"g","g":"h","h":"j","i":"u","j":"k","k":"l","l":"k",
                          "m":"n","n":"m","o":"i","p":"o","q":"w","r":"e","s":"a","t":"y","u":"y","v":"c","w":"q","x":"z",
                          "y":"t","z":"x","A":"S","B":"V","C":"X","D":"F","E":"W","F":"G","G":"H","H":"J","I":"U","J":"K",
                          "K":"L","L":"K","M":"N","N":"M","O":"I","P":"O","Q":"W","R":"E","S":"A","T":"Y","U":"Y","V":"C",
                          "W":"Q","X":"Z","Y":"T","Z":"X"," ":" "}
            for ch in text:
                key = ch
                code = "Key" + ch.upper() if ch.isalpha() else ("Space" if ch == " " else (ch.isdigit() and ("Digit" + ch) or ""))
                text_val = ch
                if ch == " ":
                    key, code, text_val = " ", "Space", " "
                mods = 0
                # ~2% typo then backspace
                if random.random() < 0.02 and ch.lower() in wrong_keys:
                    wch = wrong_keys[ch.lower()] if wrong_keys[ch.lower()] != " " else "x"
                    await type_char(ws, mid, wch, mods)
                    mid += 10
                    await asyncio.sleep(random.uniform(0.12, 0.18))
                    await send(ws, mid, "Input.dispatchKeyEvent", {"type": "keyDown", "key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8})
                    await send(ws, mid + 1, "Input.dispatchKeyEvent", {"type": "keyUp", "key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8})
                    mid += 2
                    await asyncio.sleep(random.uniform(0.06, 0.14))
                await type_char(ws, mid, ch, 0)
                mid += 10
                if random.random() < 0.08:
                    await asyncio.sleep(random.uniform(0.3, 0.8))
                else:
                    d = random.gauss(0.08, 0.03)
                    await asyncio.sleep(min(0.25, max(0.02, d)))
            await asyncio.sleep(random.uniform(0.2, 0.5))
            v = await ev(ws, mid + 1, "(document.querySelector(%s)||{}).value" % json.dumps(target))
            print("TYPED len=" + str(len(text)) + " value=" + str(v)[:120])
        elif cmd == "setfiles":
            selx, path = sys.argv[2], sys.argv[3]
            r = await send(ws, 20, "Runtime.evaluate", {"expression": """(() => { const el = document.querySelector(%s); if(!el) return null;
               el.scrollIntoView({block:'center'});
               const p = el;
               let node = p; return node ? (function(){ let rid = window.__nodeId; return null; })() : null; })()""" % json.dumps(selx), "returnByValue": False})
            r2 = await send(ws, 21, "DOM.getDocument", {"depth": -1, "pierce": True})
            node_id = await ev(ws, 22, """(() => { const el = document.querySelector(%s); if(!el) return null;
               let d=null; const f=n=>{ if(n.nodeType===1 && n===el){d=n;} }; return null; })()""" % json.dumps(selx))
            # resolve backend node id via RemoteObject
            r3 = await send(ws, 23, "Runtime.evaluate", {"expression": "document.querySelector(%s)" % json.dumps(selx), "returnByValue": False})
            obj = r3.get("result", {}).get("result", {})
            oid = obj.get("objectId")
            if not oid:
                print("SETFILE_FAIL no element"); return
            r4 = await send(ws, 24, "DOM.resolveNode", {"objectId": oid})
            boid = r4.get("result", {}).get("object", {}).get("backendNodeId")
            r5 = await send(ws, 25, "DOM.setFileInputFiles", {"files": [path], "backendNodeId": boid})
            await asyncio.sleep(0.5)
            fname = await ev(ws, 26, "(()=>{const el=document.querySelector(%s); if(!el)return 'NOEL'; if(el.files&&el.files.length) return el.files[0].name; const lab=el.closest('label')||document.body; return (lab.innerText||'').slice(0,100);})()" % json.dumps(selx))
            print("SETFILES " + json.dumps(r5.get("result", {})) + " ui=" + str(fname))
        elif cmd == "scroll":
            dy = int(sys.argv[2])
            await send(ws, 30, "Input.dispatchMouseEvent", {"type": "mouseWheel", "x": 400, "y": 300, "deltaX": 0, "deltaY": dy})
            time.sleep(random.uniform(0.8, 1.6))
            print("scrolled " + str(dy))
        elif cmd == "press":
            key = sys.argv[2]
            codes = {"Enter": ("Enter", 13), "Tab": ("Tab", 9), "Escape": ("Escape", 27), "Backspace": ("Backspace", 8),
                     "ArrowDown": ("ArrowDown", 40), "ArrowUp": ("ArrowUp", 38)}
            k, vk = codes.get(key, (key, 0))
            await send(ws, 40, "Input.dispatchKeyEvent", {"type": "keyDown", "key": k, "code": k, "windowsVirtualKeyCode": vk})
            await send(ws, 41, "Input.dispatchKeyEvent", {"type": "keyUp", "key": k, "code": k, "windowsVirtualKeyCode": vk})
            time.sleep(random.uniform(0.1, 0.3))
            print("pressed " + key)
        else:
            print("unknown cmd")
    finally:
        await ws.close()

async def type_char(ws, mid, ch, mods):
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

asyncio.run(main())
