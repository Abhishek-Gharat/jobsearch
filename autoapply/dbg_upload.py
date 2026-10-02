import asyncio, json, urllib.request, websockets

async def send(ws, mid, method, params=None):
    await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            return msg

async def main():
    with urllib.request.urlopen("http://127.0.0.1:9222/json/list", timeout=5) as r:
        ts = [t for t in json.load(r) if t.get("type") == "page"]
    async with websockets.connect(ts[0]["webSocketDebuggerUrl"], max_size=50*1024*1024) as ws:
        r1 = await send(ws, 1, "Runtime.evaluate", {"expression": "window.__els[31]", "returnByValue": False})
        print("eval:", json.dumps(r1)[:400])
        oid = r1["result"]["result"].get("objectId")
        r2 = await send(ws, 2, "DOM.requestNode", {"objectId": oid})
        print("requestNode:", json.dumps(r2)[:400])
        nid = r2["result"]["nodeId"]
        r3 = await send(ws, 3, "DOM.setFileInputFiles", {"files": [r"D:\newjobs\Resume.pdf"], "nodeId": nid})
        print("setfiles:", json.dumps(r3))
        r4 = await send(ws, 4, "Runtime.evaluate", {"expression": "(()=>{const f=window.__els[31]; return JSON.stringify({tag:f.tagName,type:f.type,n:f.files?f.files.length:-1,connected:f.isConnected});})()", "returnByValue": True})
        print("check:", json.dumps(r4)[:500])

asyncio.run(main())
