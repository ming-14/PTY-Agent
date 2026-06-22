"""调试：子进程退出后 stdin.write() 仍会阻塞"""
import sys
import time
import subprocess

p = subprocess.Popen(
    [sys.executable, "-c", "import sys; sys.exit(42)"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    stdin=subprocess.PIPE,
    bufsize=0,
)
time.sleep(0.3)
print("poll:", p.poll())
print("stdout.read:", repr(p.stdout.read(65536)))
print("poll after read:", p.poll())
print("stdin.write...")
try:
    p.stdin.write(b"test\n")
    p.stdin.flush()
    print("  ok")
except Exception as e:
    print(f"  error: {e}")
p.terminate()
p.wait()
