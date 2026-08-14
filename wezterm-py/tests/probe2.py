import os
import sys
import time

import pywezterm

print("spawn_failure...", flush=True)
p = pywezterm.Pty(cols=80, rows=24)
try:
    p.spawn([r"C:\nonexistent_program_xyz_12345.exe"])
    print("  !! spawn 未抛异常", flush=True)
except Exception as e:
    print("  spawn raised:", type(e).__name__, e, flush=True)
print("  closing...", flush=True)
p.close()
print("  closed", flush=True)

print("close_idempotent...", flush=True)
p = pywezterm.Pty(cols=80, rows=24)
shell = os.environ.get("COMSPEC", "cmd.exe")
print("  spawn echo...", flush=True)
p.spawn([shell, "/c", "echo hi"])
print("  drain...", flush=True)
for i in range(20):
    c = p.read(4096, timeout=0.2)
    print(f"    read{i}: {len(c)}", flush=True)
    if not c:
        break
print("  close...", flush=True)
p.close()
print("  close2...", flush=True)
p.close()
print("  read after close:", p.read(100, timeout=0.1), flush=True)
print("  size:", p.get_size(), flush=True)
print("DONE", flush=True)
