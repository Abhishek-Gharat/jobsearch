import asyncio, json, sys, random, urllib.request
import websockets

async def send(ws, mid, method, params=None):
    await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            return msg

async def main():
    x, y = float(sys.argv[1]), float(sys.argv[2])
    with urllib.request.urlopen("http://127.0.0.1:9222/json/list", timeout=5) as r:
        ts = [t for t in json.load(r) if t.get("type") == "page"]
    async with websockets.connect(ts[0]["webSocketDebuggerUrl"], max_size=50*1024*1024) as ws:
        await send(ws, 1, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        await asyncio.sleep(random.uniform(0.05, 0.15))
        await send(ws, 2, "Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
        await asyncio.sleep(random.uniform(0.03, 0.12))
        await send(ws, 3, "Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
        await asyncio.sleep(0.4)
        print("clicked", x, y)

asyncio.run(main())
