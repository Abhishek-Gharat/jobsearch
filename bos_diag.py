#!/usr/bin/env python3
"""Diagnose BrowserOS tab 12: real URL, text, redirect status."""
import sys, time, os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, 'D:\\newjobs')
os.environ['BROWSER_ENGINE'] = 'browseros'
import bos

b = bos.BOS('h385-bos-diag')
tl, _ = b.call("tabs", {"action": "list"})
print('TABS: ' + str(tl)[:800], flush=True)
for pid in (12,):
    u, _ = b.call("evaluate", {"page": pid, "func": "() => window.location.href + ' ||| ' + document.title"})
    print('PAGE12 url: ' + str(u)[:300], flush=True)
    t = b.read(pid)
    print('PAGE12 text head: ' + str(t)[:1500], flush=True)
