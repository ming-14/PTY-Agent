$ErrorActionPreference = "Continue"

Write-Host "=== 1. subprocess 模式: py 输出, 等 trigger ==="
python app.py exec sp_s1 -c 'py -u -c "import sys; sys.stdout.write(''hello-subprocess'')"' --subprocess -t "hello" --timeout 10

Write-Host "=== 2. subprocess 模式: 逐行增量输出 ==="
python app.py exec sp_s2 -c 'py -u -c "import time; [print(f''line-{i}'') or time.sleep(0.5) for i in range(5)]"' --subprocess -t "line-4" --timeout 15

Write-Host "=== 3. 清理 ==="
python app.py kill sp_s1
python app.py kill sp_s2