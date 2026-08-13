$vcvars = "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat"
$cmd = "call `"$vcvars`" >nul 2>&1 && cmake --build build --config Debug 2>&1"
powershell -NoProfile -Command "cmd /c `"$cmd`""
