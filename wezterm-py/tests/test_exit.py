import os
import time

import pywezterm

print("create pty", flush=True)
p = pywezterm.Pty(cols=80, rows=24)
shell = os.environ.get("COMSPEC", "cmd.exe")
print("spawn", flush=True)
p.spawn([shell, "/c", "echo hi"])
time.sleep(0.5)
print("drain", flush=True)
p.read(4096, timeout=0.3)
print("close", flush=True)
p.close()
print("DONE - process should now exit", flush=True)
