# 诊断：close 死锁挂起点定位（临时脚本，测试后删除）
import os
import threading
import time

import pywezterm

p = pywezterm.Pty(cols=80, rows=24)
shell = os.environ.get("COMSPEC", "cmd.exe")
p.spawn([shell, "/c", "echo hi"])
time.sleep(0.5)
p.read(4096, timeout=0.3)
print("calling close ...", flush=True)

result = {}

def do_close():
    t0 = time.time()
    p.close()
    result["close_returned"] = time.time() - t0

t = threading.Thread(target=do_close, daemon=True)
t.start()
for i in range(50):
    time.sleep(0.2)
    if t.is_alive():
        pass
print(f"after 10s, close_thread_alive={t.is_alive()}", flush=True)
print(f"result={result}", flush=True)
print("done", flush=True)
