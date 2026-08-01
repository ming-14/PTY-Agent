#pragma once

#include "common.h"
#include <d3d11.h>
#include <atomic>
#include <mutex>
#include <thread>
#include <condition_variable>

#define _SILENCE_EXPERIMENTAL_COROUTINE_DEPRECATION_WARNINGS
#include <winrt/Windows.Graphics.Capture.h>
#include <winrt/Windows.Graphics.DirectX.Direct3D11.h>

namespace fs {

class WGCCapture {
public:
    WGCCapture();
    ~WGCCapture();

    ErrorCode initialize();
    void shutdown();

    ErrorCode capture_window(void* hwnd, FrameData& frame);
    ErrorCode capture_monitor(int monitor_index, FrameData& frame);

    ErrorCode start_window_session(void* hwnd);
    ErrorCode start_monitor_session(int monitor_index);
    ErrorCode stop_session();
    ErrorCode capture_from_session(FrameData& frame);

    static bool is_supported();

private:
    ErrorCode ensure_d3d();
    void session_thread_func(void* hwnd_or_null, int monitor_index, bool is_window);

    bool d3d_ready_ = false;

    ID3D11Device* d3d_device_ = nullptr;
    ID3D11DeviceContext* d3d_context_ = nullptr;

    ID3D11Texture2D* staging_texture_ = nullptr;
    int staging_width_ = 0;
    int staging_height_ = 0;

    std::mutex session_mutex_;
    std::condition_variable session_cv_;
    std::condition_variable frame_cv_;
    std::atomic<bool> session_active_{ false };
    std::atomic<bool> session_stop_requested_{ false };
    std::atomic<bool> session_initialized_{ false };
    std::atomic<bool> frame_available_{ false };

    winrt::Windows::Graphics::Capture::Direct3D11CaptureFramePool session_pool_{ nullptr };
    winrt::Windows::Graphics::Capture::GraphicsCaptureSession session_{ nullptr };

    // GraphicsCaptureItem Closed event revoker (auto-managed: local item destructs on thread exit)
    winrt::Windows::Graphics::Capture::GraphicsCaptureItem::Closed_revoker closed_revoker_;

    // Resize 请求标志：capture_from_session（capture_loop 线程）检测到 ContentSize 变化时设置，
    // session_thread_func（STA 线程）消息循环检查并执行 Recreate。
    // 当前 SDK 的 GraphicsCaptureItem 没有 SizeChanged 事件，用此机制替代。
    std::atomic<bool> resize_pending_{ false };
    winrt::Windows::Graphics::SizeInt32 resize_target_size_{};  // 由 session_mutex_ 保护

    // D3D device and current pool size (needed to Recreate FramePool on window resize)
    winrt::Windows::Graphics::DirectX::Direct3D11::IDirect3DDevice session_device_{ nullptr };
    winrt::Windows::Graphics::SizeInt32 pool_size_{};

    // Cached window handle for IsIconic check in capture_from_session (window sessions only)
    void* session_hwnd_ = nullptr;

    std::thread session_thread_;
    ErrorCode session_start_result_ = ErrorCode::OK;
};

}
