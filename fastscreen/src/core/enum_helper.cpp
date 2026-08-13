// 系统对象枚举：遍历显示器（EnumDisplayMonitors）与顶层可见窗口（EnumWindows）。
#include "enum_helper.h"
#include <Windows.h>
#include <dwmapi.h>

#pragma comment(lib, "user32.lib")
#pragma comment(lib, "dwmapi.lib")

namespace fs {

struct MonitorEnumCtx {
    std::vector<MonitorInfo>* monitors;
    int index;
};

std::vector<MonitorInfo> EnumHelper::enumerate_monitors() {
    std::vector<MonitorInfo> result;

    MonitorEnumCtx ctx = { &result, 0 };

    auto callback = [](HMONITOR hMonitor, HDC, LPRECT, LPARAM lParam) -> BOOL {
        auto* ctx = reinterpret_cast<MonitorEnumCtx*>(lParam);

        MONITORINFOEXW mi;
        mi.cbSize = sizeof(mi);
        GetMonitorInfoW(hMonitor, &mi);

        MonitorInfo info = {};
        info.id = ctx->index;
        wcsncpy_s(info.name, mi.szDevice, _TRUNCATE);
        info.left = mi.rcMonitor.left;
        info.top = mi.rcMonitor.top;
        info.width = mi.rcMonitor.right - mi.rcMonitor.left;
        info.height = mi.rcMonitor.bottom - mi.rcMonitor.top;
        info.primary = (mi.dwFlags & MONITORINFOF_PRIMARY) != 0;

        ctx->monitors->push_back(info);
        ctx->index++;
        return TRUE;
    };

    EnumDisplayMonitors(nullptr, nullptr, callback, reinterpret_cast<LPARAM>(&ctx));

    return result;
}

struct WindowEnumCtx {
    std::vector<WindowInfo>* windows;
};

static BOOL CALLBACK enum_windows_callback(HWND hwnd, LPARAM lParam) {
    auto* ctx = reinterpret_cast<WindowEnumCtx*>(lParam);

    if (!IsWindowVisible(hwnd)) return TRUE;

    wchar_t title[256] = {};
    GetWindowTextW(hwnd, title, 256);
    if (wcslen(title) == 0) return TRUE;

    wchar_t class_name[256] = {};
    GetClassNameW(hwnd, class_name, 256);

    RECT rect;
    GetWindowRect(hwnd, &rect);

    WindowInfo info = {};
    info.hwnd = reinterpret_cast<void*>(hwnd);
    wcsncpy_s(info.title, title, _TRUNCATE);
    wcsncpy_s(info.class_name, class_name, _TRUNCATE);
    info.left = rect.left;
    info.top = rect.top;
    info.width = rect.right - rect.left;
    info.height = rect.bottom - rect.top;
    info.visible = true;

    ctx->windows->push_back(info);
    return TRUE;
}

std::vector<WindowInfo> EnumHelper::enumerate_windows() {
    std::vector<WindowInfo> result;
    WindowEnumCtx ctx = { &result };
    EnumWindows(enum_windows_callback, reinterpret_cast<LPARAM>(&ctx));
    return result;
}

}
