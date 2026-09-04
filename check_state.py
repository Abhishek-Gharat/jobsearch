import urllib.request, json
data = urllib.request.urlopen('http://127.0.0.1:3000/api/state').read().decode()
state = json.loads(data)
print(f'Progress: {state["progress"]}')
print(f'Total events: {state["totalEvents"]}')
print(f'Recent events:')
for e in state['recentEvents'][-15:]:
    print(f'  - {e["company"]} - {e["role"]} ({e["platform"]}) - {e["event"]}')
