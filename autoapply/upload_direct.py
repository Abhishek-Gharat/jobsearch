import asyncio, json, sys, base64, urllib.request, websockets

async def send(ws, mid, method, params=None):
    await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            return msg

async def main():
    idx = int(sys.argv[1])
    path = sys.argv[2]
    with open(path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode()
    js = """(() => {
      const b64 = "%s";
      const bin = atob(b64);
      const arr = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
      const fname = %s;
      const file = new File([arr], fname, {type: "application/pdf"});
      const dt = new DataTransfer();
      dt.items.add(file);
      const el = window.__els[%d];
      if (!el) return "NOEL";
      el.files = dt.files;
      el.dispatchEvent(new Event("input", {bubbles: true}));
      el.dispatchEvent(new Event("change", {bubbles: true}));
      return JSON.stringify({n: el.files.length, name: el.files[0] ? el.files[0].name : null, size: el.files[0] ? el.files[0].size : 0});
    })()""" % (b64, json.dumps(path.split("\\")[-1].split("/")[-1]), idx)
    with urllib.request.urlopen("http://127.0.0.1:9222/json/list", timeout=5) as r:
        ts = [t for t in json.load(r) if t.get("type") == "page"]
    async with websockets.connect(ts[0]["webSocketDebuggerUrl"], max_size=20 * 1024 * 1024) as ws:
        r1 = await send(ws, 1, "Runtime.evaluate", {"expression": js, "returnByValue": True, "awaitPromise": False})
        res = r1.get("result", {}).get("result", {})
        print("set:", res.get("value") or json.dumps(r1.get("result", {}))[:300])
        await asyncio.sleep(1.5)
        r2 = await send(ws, 2, "Runtime.evaluate", {"expression": """(()=>{const el=window.__els[%d];
           let n = el && el.files ? el.files.length : -1;
           let host = el ? (el.getRootNode().host || el.parentElement) : null;
           let ctx = '';
           for (let i=0; i<6 && host; i++) { ctx = (host.innerText||'').replace(/\\s+/g,' ').slice(0,200); host = host.getRootNode().host || host.parentElement; }
           return JSON.stringify({n, name: el&&el.files[0]?el.files[0].name:null, ctx});})()""" % idx, "returnByValue": True})
        print("check:", r2.get("result", {}).get("result", {}).get("value"))

asyncio.run(main())
