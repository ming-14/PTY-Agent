#pragma once

#include "common.h"
#include "dxgi_capture.h"
#include "bitblt_capture.h"
#include "wgc_capture.h"
#include "image_encoder.h"
#include "frame_buffer.h"
#include <atomic>
#include <thread>
#include <mutex>

namespace fs {

class CaptureSession {
public:
    CaptureSession();
    ~CaptureSession();

    ErrorCode start(TargetType type, int target_id, CaptureMethod method, int fps);
    ErrorCode stop();

    ErrorCode capture_single(FrameData& frame);

    void set_callback(FrameCallback cb) { callback_ = cb; }

    bool is_running() const { return running_.load(); }
    TargetType target_type() const { return target_type_; }
    int target_id() const { return target_id_; }

    ErrorCode save_frame(FrameData& frame, const wchar_t* path, ImageFormat format, float quality = 0.9f);

private:
    void capture_loop();
    ErrorCode do_capture(FrameData& frame);

    TargetType target_type_ = TargetType::Monitor;
    int target_id_ = 0;
    CaptureMethod method_ = CaptureMethod::Auto;
    int target_fps_ = 60;

    std::atomic<bool> running_{false};
    std::thread capture_thread_;
    FrameCallback callback_;

    DXGICapture dxgi_;
    BitBltCapture bitblt_;
    WGCCapture wgc_;

    CaptureMethod active_method_ = CaptureMethod::Auto;
    bool wgc_session_started_ = false;
};

}
