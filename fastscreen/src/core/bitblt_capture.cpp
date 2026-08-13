// GDI BitBlt 捕获：兼容性最好、无特殊依赖的基础方案，
// 作为 DXGI/WGC 初始化或采集失败时的最终回退。
#include "bitblt_capture.h"
#include <chrono>
#include <dwmapi.h>

#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "dwmapi.lib")

namespace fs {

struct MonitorEnumData {
    int target_index;
    int current_index;
    HMONITOR monitor;
};

BitBltCapture::BitBltCapture() {}
BitBltCapture::~BitBltCapture() {}

ErrorCode BitBltCapture::capture_monitor(int monitor_index, FrameData& frame) {
    MonitorEnumData data = { monitor_index, 0, nullptr };

    auto enum_callback = [](HMONITOR hMonitor, HDC, LPRECT, LPARAM lParam) -> BOOL {
        auto* d = reinterpret_cast<MonitorEnumData*>(lParam);
        if (d->current_index == d->target_index) {
            d->monitor = hMonitor;
            return FALSE;
        }
        d->current_index++;
        return TRUE;
    };

    EnumDisplayMonitors(nullptr, nullptr, enum_callback, reinterpret_cast<LPARAM>(&data));

    if (!data.monitor) return ErrorCode::InvalidParam;

    MONITORINFOEXW mi;
    mi.cbSize = sizeof(mi);
    GetMonitorInfoW(data.monitor, &mi);

    int x = mi.rcMonitor.left;
    int y = mi.rcMonitor.top;
    int w = mi.rcMonitor.right - mi.rcMonitor.left;
    int h = mi.rcMonitor.bottom - mi.rcMonitor.top;

    return capture_rect(x, y, w, h, frame);
}

ErrorCode BitBltCapture::capture_window(void* hwnd, FrameData& frame) {
    if (!hwnd) {
        FS_LOG_ERROR("BitBlt: capture_window hwnd is null");
        return ErrorCode::InvalidParam;
    }

    HWND hw = reinterpret_cast<HWND>(hwnd);
    if (!IsWindow(hw)) {
        FS_LOG_ERROR("BitBlt: hwnd=%p is not a valid window", hwnd);
        return ErrorCode::InvalidParam;
    }

    if (IsIconic(hw)) {
        FS_LOG_ERROR("BitBlt: hwnd=%p is minimized, cannot capture", hwnd);
        return ErrorCode::NoOutput;
    }

    RECT rect;
    HRESULT hr = DwmGetWindowAttribute(hw, DWMWA_EXTENDED_FRAME_BOUNDS, &rect, sizeof(rect));
    if (FAILED(hr) || rect.right <= rect.left || rect.bottom <= rect.top) {
        if (!GetWindowRect(hw, &rect)) {
            FS_LOG_ERROR("BitBlt: GetWindowRect failed for hwnd=%p", hwnd);
            return ErrorCode::CaptureFailed;
        }
    }

    int x = rect.left;
    int y = rect.top;
    int w = rect.right - rect.left;
    int h = rect.bottom - rect.top;

    return capture_rect(x, y, w, h, frame);
}

ErrorCode BitBltCapture::capture_rect(int x, int y, int w, int h, FrameData& frame) {
    HDC screen_dc = GetDC(nullptr);
    if (!screen_dc) {
        FS_LOG_ERROR("BitBlt: GetDC failed");
        return ErrorCode::CaptureFailed;
    }

    HDC mem_dc = CreateCompatibleDC(screen_dc);
    if (!mem_dc) {
        FS_LOG_ERROR("BitBlt: CreateCompatibleDC failed");
        ReleaseDC(nullptr, screen_dc);
        return ErrorCode::CaptureFailed;
    }

    HBITMAP bitmap = CreateCompatibleBitmap(screen_dc, w, h);
    if (!bitmap) {
        FS_LOG_ERROR("BitBlt: CreateCompatibleBitmap failed (%dx%d)", w, h);
        DeleteDC(mem_dc);
        ReleaseDC(nullptr, screen_dc);
        return ErrorCode::CaptureFailed;
    }

    HBITMAP old_bitmap = (HBITMAP)SelectObject(mem_dc, bitmap);

    BOOL result = BitBlt(mem_dc, 0, 0, w, h, screen_dc, x, y, SRCCOPY | CAPTUREBLT);

    if (!result) {
        FS_LOG_ERROR("BitBlt: BitBlt() failed");
        SelectObject(mem_dc, old_bitmap);
        DeleteObject(bitmap);
        DeleteDC(mem_dc);
        ReleaseDC(nullptr, screen_dc);
        return ErrorCode::CaptureFailed;
    }

    BITMAPINFOHEADER bi = {};
    bi.biSize = sizeof(BITMAPINFOHEADER);
    bi.biWidth = w;
    bi.biHeight = -h;
    bi.biPlanes = 1;
    bi.biBitCount = 32;
    bi.biCompression = BI_RGB;

    int stride = w * 4;
    int data_size = stride * h;
    uint8_t* pixels = fs::frame_alloc(data_size);

    int rows = GetDIBits(mem_dc, bitmap, 0, h, pixels, (BITMAPINFO*)&bi, DIB_RGB_COLORS);

    SelectObject(mem_dc, old_bitmap);
    DeleteObject(bitmap);
    DeleteDC(mem_dc);
    ReleaseDC(nullptr, screen_dc);

    if (rows == 0) {
        FS_LOG_ERROR("BitBlt: GetDIBits returned 0 rows");
        fs::frame_free(pixels, data_size);
        return ErrorCode::CaptureFailed;
    }

    frame.data = pixels;
    frame.owns_data = true;
    frame.width = w;
    frame.height = h;
    frame.stride = stride;
    frame.bpp = 4;
    frame.timestamp_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now().time_since_epoch()
    ).count();

    return ErrorCode::OK;
}

}
