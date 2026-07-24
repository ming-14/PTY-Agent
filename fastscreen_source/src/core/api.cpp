#include "common.h"
#include "capture_session.h"
#include "enum_helper.h"
#include "image_encoder.h"
#include "dxgi_capture.h"
#include "wgc_capture.h"
#include "bitblt_capture.h"
#include <cstdlib>
#include <cstring>
#include <unordered_map>
#include <mutex>

extern "C" {

struct FsMonitorInfo {
    int32_t id;
    wchar_t name[128];
    int32_t left;
    int32_t top;
    int32_t width;
    int32_t height;
    int32_t primary;
};

struct FsWindowInfo {
    void* hwnd;
    wchar_t title[256];
    wchar_t class_name[256];
    int32_t left;
    int32_t top;
    int32_t width;
    int32_t height;
    int32_t visible;
};

struct FsFrame {
    uint8_t* data;
    int32_t width;
    int32_t height;
    int32_t stride;
    int32_t bpp;
    int64_t timestamp_ms;
    int32_t owns_data;
};

static std::unordered_map<int64_t, fs::CaptureSession*> g_sessions;
static std::mutex g_sessions_mutex;
static int64_t g_next_session_id = 1;
static bool g_initialized = false;

static bool g_dxgi_supported = false;
static bool g_wgc_supported = false;
static bool g_support_checked = false;

static std::mutex g_dxgi_mutex;
static fs::DXGICapture* g_dxgi_capture = nullptr;
static int g_dxgi_monitor_index = -1;
static std::mutex g_wgc_mutex;
static fs::WGCCapture* g_wgc_capture = nullptr;
static std::mutex g_bitblt_mutex;
static fs::BitBltCapture* g_bitblt_capture = nullptr;

typedef void (*FsFrameCallbackC)(FsFrame* frame, void* user_data);

struct ContinuousCaptureCtx {
    fs::CaptureSession* session;
    FsFrameCallbackC c_callback;
    void* user_data;
};

static std::unordered_map<int64_t, ContinuousCaptureCtx*> g_ctx_map;
static std::mutex g_ctx_mutex;

static void ensure_support_checked() {
    if (g_support_checked) return;
    g_dxgi_supported = fs::DXGICapture::is_supported();
    g_wgc_supported = fs::WGCCapture::is_supported();
    g_support_checked = true;
}

static fs::DXGICapture* get_dxgi_capture(int monitor_index) {
    std::lock_guard<std::mutex> lock(g_dxgi_mutex);
    if (g_dxgi_capture && g_dxgi_monitor_index == monitor_index) {
        return g_dxgi_capture;
    }
    delete g_dxgi_capture;
    g_dxgi_capture = new fs::DXGICapture();
    g_dxgi_monitor_index = monitor_index;
    return g_dxgi_capture;
}

static fs::WGCCapture* get_wgc_capture() {
    std::lock_guard<std::mutex> lock(g_wgc_mutex);
    if (!g_wgc_capture) {
        g_wgc_capture = new fs::WGCCapture();
    }
    return g_wgc_capture;
}

static fs::BitBltCapture* get_bitblt_capture() {
    std::lock_guard<std::mutex> lock(g_bitblt_mutex);
    if (!g_bitblt_capture) {
        g_bitblt_capture = new fs::BitBltCapture();
    }
    return g_bitblt_capture;
}

static void cleanup_cached_captures() {
    {
        std::lock_guard<std::mutex> lock(g_dxgi_mutex);
        delete g_dxgi_capture;
        g_dxgi_capture = nullptr;
        g_dxgi_monitor_index = -1;
    }
    {
        std::lock_guard<std::mutex> lock(g_wgc_mutex);
        delete g_wgc_capture;
        g_wgc_capture = nullptr;
    }
    {
        std::lock_guard<std::mutex> lock(g_bitblt_mutex);
        delete g_bitblt_capture;
        g_bitblt_capture = nullptr;
    }
}

static void release_dxgi_duplication() {
    std::lock_guard<std::mutex> lock(g_dxgi_mutex);
    if (g_dxgi_capture && g_dxgi_capture->is_available()) {
        g_dxgi_capture->shutdown();
    }
}

FS_API int fs_init() {
    if (g_initialized) return 0;
    CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    fs::ImageEncoder::init();
    ensure_support_checked();
    g_initialized = true;
    return 0;
}

FS_API void fs_shutdown() {
    if (!g_initialized) return;

    {
        std::lock_guard<std::mutex> lock(g_sessions_mutex);
        for (auto& pair : g_sessions) {
            pair.second->stop();
            delete pair.second;
        }
        g_sessions.clear();
    }

    cleanup_cached_captures();
    fs::frame_pool_cleanup();

    fs::ImageEncoder::shutdown();
    CoUninitialize();
    g_initialized = false;
    g_support_checked = false;
}

FS_API int fs_enum_monitors(FsMonitorInfo** out, int* count) {
    if (!out || !count) return -2;

    auto monitors = fs::EnumHelper::enumerate_monitors();
    *count = (int)monitors.size();

    if (monitors.empty()) {
        *out = nullptr;
        return 0;
    }

    *out = (FsMonitorInfo*)CoTaskMemAlloc(sizeof(FsMonitorInfo) * monitors.size());
    if (!*out) return -4;

    for (size_t i = 0; i < monitors.size(); i++) {
        auto& src = monitors[i];
        auto& dst = (*out)[i];
        dst.id = src.id;
        memcpy(dst.name, src.name, sizeof(src.name));
        dst.left = src.left;
        dst.top = src.top;
        dst.width = src.width;
        dst.height = src.height;
        dst.primary = src.primary ? 1 : 0;
    }

    return 0;
}

FS_API int fs_enum_windows(FsWindowInfo** out, int* count) {
    if (!out || !count) return -2;

    auto windows = fs::EnumHelper::enumerate_windows();
    *count = (int)windows.size();

    if (windows.empty()) {
        *out = nullptr;
        return 0;
    }

    *out = (FsWindowInfo*)CoTaskMemAlloc(sizeof(FsWindowInfo) * windows.size());
    if (!*out) return -4;

    for (size_t i = 0; i < windows.size(); i++) {
        auto& src = windows[i];
        auto& dst = (*out)[i];
        dst.hwnd = src.hwnd;
        memcpy(dst.title, src.title, sizeof(src.title));
        memcpy(dst.class_name, src.class_name, sizeof(src.class_name));
        dst.left = src.left;
        dst.top = src.top;
        dst.width = src.width;
        dst.height = src.height;
        dst.visible = src.visible ? 1 : 0;
    }

    return 0;
}

FS_API void fs_free_enum(void* ptr) {
    if (ptr) CoTaskMemFree(ptr);
}

FS_API int64_t fs_create_session(int target_type, int target_id, int method) {
    auto* session = new fs::CaptureSession();

    fs::TargetType tt = static_cast<fs::TargetType>(target_type);
    fs::CaptureMethod cm = static_cast<fs::CaptureMethod>(method);

    if (cm == fs::CaptureMethod::DXGI || cm == fs::CaptureMethod::Auto) {
        release_dxgi_duplication();
    }

    int64_t id;
    {
        std::lock_guard<std::mutex> lock(g_sessions_mutex);
        id = g_next_session_id++;
        g_sessions[id] = session;
    }

    return id;
}

FS_API int fs_destroy_session(int64_t session_id) {
    {
        std::lock_guard<std::mutex> lock(g_ctx_mutex);
        auto it = g_ctx_map.find(session_id);
        if (it != g_ctx_map.end()) {
            delete it->second;
            g_ctx_map.erase(it);
        }
    }
    std::lock_guard<std::mutex> lock(g_sessions_mutex);
    auto it = g_sessions.find(session_id);
    if (it == g_sessions.end()) return -2;
    it->second->stop();
    delete it->second;
    g_sessions.erase(it);
    return 0;
}

FS_API int fs_capture_frame(int64_t session_id, int target_type, int target_id, int method, FsFrame* out_frame) {
    if (!out_frame) return -2;

    fs::TargetType tt = static_cast<fs::TargetType>(target_type);
    fs::CaptureMethod cm = static_cast<fs::CaptureMethod>(method);
    bool is_auto = (cm == fs::CaptureMethod::Auto);

    if (is_auto) {
        ensure_support_checked();
        if (tt == fs::TargetType::Monitor) {
            cm = g_dxgi_supported ? fs::CaptureMethod::DXGI : (g_wgc_supported ? fs::CaptureMethod::WGC : fs::CaptureMethod::BitBlt);
        } else {
            cm = g_wgc_supported ? fs::CaptureMethod::WGC : fs::CaptureMethod::BitBlt;
        }
        FS_LOG("Auto: selected method=%s for %s",
            cm == fs::CaptureMethod::DXGI ? "DXGI" : cm == fs::CaptureMethod::WGC ? "WGC" : "BitBlt",
            tt == fs::TargetType::Monitor ? "monitor" : "window");
    }

    fs::FrameData frame;
    fs::ErrorCode err;

    switch (cm) {
    case fs::CaptureMethod::DXGI: {
        if (tt == fs::TargetType::Window) {
            FS_LOG_ERROR("DXGI does not support window capture");
            err = fs::ErrorCode::Unsupported;
            break;
        }
        auto* dxgi = get_dxgi_capture(target_id);
        if (!dxgi->is_available()) {
            err = dxgi->initialize(target_id);
            if (err != fs::ErrorCode::OK) {
                if (is_auto) {
                    FS_LOG("DXGI init failed (err=%d), falling back", (int)err);
                    cm = fs::CaptureMethod::BitBlt;
                } else {
                    break;
                }
            } else {
                err = dxgi->capture_frame(frame);
                if (err != fs::ErrorCode::OK) {
                    if (is_auto) {
                        FS_LOG("DXGI capture failed (err=%d), falling back to BitBlt", (int)err);
                        cm = fs::CaptureMethod::BitBlt;
                    } else {
                        break;
                    }
                } else {
                    break;
                }
            }
        } else {
            err = dxgi->capture_frame(frame);
            if (err != fs::ErrorCode::OK) {
                if (is_auto) {
                    FS_LOG("DXGI capture failed (err=%d), falling back to BitBlt", (int)err);
                    cm = fs::CaptureMethod::BitBlt;
                } else {
                    break;
                }
            } else {
                break;
            }
        }
    }
    case fs::CaptureMethod::WGC: {
        auto* wgc = get_wgc_capture();
        if (tt == fs::TargetType::Window) {
            err = wgc->capture_window(reinterpret_cast<void*>(static_cast<intptr_t>(target_id)), frame);
        } else {
            err = wgc->capture_monitor(target_id, frame);
        }
        if (err != fs::ErrorCode::OK) {
            if (is_auto) {
                FS_LOG("WGC failed (err=%d), falling back to BitBlt", (int)err);
                cm = fs::CaptureMethod::BitBlt;
            } else {
                break;
            }
        } else {
            break;
        }
    }
    case fs::CaptureMethod::BitBlt: {
        auto* bitblt = get_bitblt_capture();
        if (tt == fs::TargetType::Window) {
            err = bitblt->capture_window(reinterpret_cast<void*>(static_cast<intptr_t>(target_id)), frame);
        } else {
            err = bitblt->capture_monitor(target_id, frame);
        }
        break;
    }
    default:
        err = fs::ErrorCode::Unsupported;
    }

    if (err != fs::ErrorCode::OK) {
        FS_LOG_ERROR("capture_frame failed: method=%s target=%s err=%d",
            cm == fs::CaptureMethod::DXGI ? "DXGI" : cm == fs::CaptureMethod::WGC ? "WGC" : "BitBlt",
            tt == fs::TargetType::Monitor ? "monitor" : "window",
            (int)err);
        return (int)err;
    }

    out_frame->data = frame.data;
    out_frame->width = frame.width;
    out_frame->height = frame.height;
    out_frame->stride = frame.stride;
    out_frame->bpp = frame.bpp;
    out_frame->timestamp_ms = frame.timestamp_ms;
    out_frame->owns_data = frame.owns_data ? 1 : 0;

    frame.owns_data = false;

    return 0;
}

FS_API int fs_frame_save(FsFrame* frame, const wchar_t* path, int format, float quality) {
    if (!frame || !frame->data || !path) return -2;

    fs::FrameData fd;
    fd.data = frame->data;
    fd.width = frame->width;
    fd.height = frame->height;
    fd.stride = frame->stride;
    fd.bpp = frame->bpp;
    fd.timestamp_ms = frame->timestamp_ms;
    fd.owns_data = false;

    return (int)fs::ImageEncoder::encode(fd, path, static_cast<fs::ImageFormat>(format), quality);
}

FS_API int fs_frame_encode_to_buffer(FsFrame* frame, int format, float quality, uint8_t** out_buf, int* out_size) {
    if (!frame || !frame->data || !out_buf || !out_size) return -2;

    fs::FrameData fd;
    fd.data = frame->data;
    fd.width = frame->width;
    fd.height = frame->height;
    fd.stride = frame->stride;
    fd.bpp = frame->bpp;
    fd.timestamp_ms = frame->timestamp_ms;
    fd.owns_data = false;

    return (int)fs::ImageEncoder::encode_to_buffer(fd, static_cast<fs::ImageFormat>(format), quality, out_buf, out_size);
}

FS_API int fs_frame_encode_to_buffer_scaled(FsFrame* frame, int format, float quality, int target_width, int target_height, uint8_t** out_buf, int* out_size) {
    if (!frame || !frame->data || !out_buf || !out_size) return -2;

    fs::FrameData fd;
    fd.data = frame->data;
    fd.width = frame->width;
    fd.height = frame->height;
    fd.stride = frame->stride;
    fd.bpp = frame->bpp;
    fd.timestamp_ms = frame->timestamp_ms;
    fd.owns_data = false;

    return (int)fs::ImageEncoder::encode_to_buffer_scaled(fd, static_cast<fs::ImageFormat>(format), quality, target_width, target_height, out_buf, out_size);
}

FS_API void fs_free_buffer(void* ptr) {
    if (ptr) CoTaskMemFree(ptr);
}

FS_API int fs_frame_copy_data(FsFrame* frame, uint8_t* buf, int buf_size) {
    if (!frame || !frame->data || !buf) return -2;

    int required = frame->stride * frame->height;
    if (buf_size < required) return -2;

    memcpy(buf, frame->data, required);
    return required;
}

FS_API void fs_frame_release(FsFrame* frame) {
    if (frame && frame->owns_data && frame->data) {
        fs::frame_free(frame->data, (size_t)frame->stride * frame->height);
        frame->data = nullptr;
        frame->owns_data = 0;
    }
}

FS_API int fs_start_continuous(int64_t session_id, int target_type, int target_id, int method, int fps, FsFrameCallbackC callback, void* user_data) {
    fs::CaptureSession* session = nullptr;
    {
        std::lock_guard<std::mutex> lock(g_sessions_mutex);
        auto it = g_sessions.find(session_id);
        if (it == g_sessions.end()) return -2;
        session = it->second;
    }

    if (!callback) return -2;

    auto* ctx = new ContinuousCaptureCtx();
    ctx->session = session;
    ctx->c_callback = callback;
    ctx->user_data = user_data;

    {
        std::lock_guard<std::mutex> lock(g_ctx_mutex);
        g_ctx_map[session_id] = ctx;
    }

    session->set_callback([ctx](fs::FrameData& frame) {
        FsFrame f;
        f.data = frame.data;
        f.width = frame.width;
        f.height = frame.height;
        f.stride = frame.stride;
        f.bpp = frame.bpp;
        f.timestamp_ms = frame.timestamp_ms;
        f.owns_data = frame.owns_data ? 1 : 0;

        if (frame.owns_data) {
            frame.owns_data = false;
            frame.data = nullptr;
        }

        ctx->c_callback(&f, ctx->user_data);
    });

    return (int)session->start(
        static_cast<fs::TargetType>(target_type),
        target_id,
        static_cast<fs::CaptureMethod>(method),
        fps
    );
}

FS_API int fs_stop_continuous(int64_t session_id) {
    {
        std::lock_guard<std::mutex> lock(g_ctx_mutex);
        auto it = g_ctx_map.find(session_id);
        if (it != g_ctx_map.end()) {
            delete it->second;
            g_ctx_map.erase(it);
        }
    }

    fs::CaptureSession* session = nullptr;
    {
        std::lock_guard<std::mutex> lock(g_sessions_mutex);
        auto it = g_sessions.find(session_id);
        if (it == g_sessions.end()) return -2;
        session = it->second;
    }
    return (int)session->stop();
}

FS_API int fs_session_is_running(int64_t session_id) {
    fs::CaptureSession* session = nullptr;
    {
        std::lock_guard<std::mutex> lock(g_sessions_mutex);
        auto it = g_sessions.find(session_id);
        if (it == g_sessions.end()) return 0;
        session = it->second;
    }
    return session->is_running() ? 1 : 0;
}

}
