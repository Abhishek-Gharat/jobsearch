import asyncio, json, sys, urllib.request

def targets():
    with urllib.request.urlopen("http://127.0.0.1:9222/json/list", timeout=5) as r:
        return json.load(r)

async def send(ws, mid, method, params=None):
    await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            return msg

async def main():
    url = sys.argv[1]
    ts = targets()
    page = None
    for t in ts:
        if t.get("type") == "page" and t.get("id") == "74":
            page = t
    if page is None:
        for t in ts:
            if t.get("type") == "page":
                page = t
                break
    ws_url = page["webSocketDebuggerUrl"]
    import websockets
    async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
        r1 = await send(ws, 1, "Page.enable")
        r2 = await send(ws, 2, "Page.navigate", {"url": url})
        await asyncio.sleep(3)
        r3 = await send(ws, 3, "Runtime.evaluate", {
            "expression": "JSON.stringify({title:document.title,url:location.href,ready:document.readyState})",
            "returnByValue": True})
        print(json.dumps({"nav": r2.get("result"), "state": r3.get("result", {}).get("result", {}).get("value")}, indent=1))

asyncio.run(main())
