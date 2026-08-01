#include "capture_session.h"
#include <chrono>
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

namespace fs {

CaptureSession::CaptureSession() {}
CaptureSession::~CaptureSession() {
    stop();
}

ErrorCode CaptureSession::start(TargetType type, int target_id, CaptureMethod method, int fps) {
    if (running_.load()) return ErrorCode::AlreadyRunning;

    target_type_ = type;
    target_id_ = target_id;
    method_ = method;
    target_fps_ = fps;

    if (method_ == CaptureMethod::Auto) {
        if (type == TargetType::Monitor && DXGICapture::is_supported()) {
            active_method_ = CaptureMethod::DXGI;
        } else if (type == TargetType::Window && WGCCapture::is_supported()) {
            active_method_ = CaptureMethod::WGC;
        } else {
            active_method_ = CaptureMethod::BitBlt;
        }
    } else {
        if (method_ == CaptureMethod::DXGI && type == TargetType::Window) {
            FS_LOG_ERROR("CaptureSession: DXGI does not support window capture");
            return ErrorCode::Unsupported;
        }
        active_method_ = method_;
    }

    if (active_method_ == CaptureMethod::DXGI) {
        ErrorCode err = dxgi_.initialize(target_id);
        if (err != ErrorCode::OK) {
            if (method_ == CaptureMethod::Auto) {
                FS_LOG("CaptureSession: DXGI init failed, falling back to BitBlt");
                active_method_ = CaptureMethod::BitBlt;
            } else {
                return err;
            }
        }
    }

    if (active_method_ == CaptureMethod::WGC) {
        ErrorCode err;
        if (target_type_ == TargetType::Window) {
            err = wgc_.start_window_session(reinterpret_cast<void*>(static_cast<intptr_t>(target_id_)));
        } else {
            err = wgc_.start_monitor_session(target_id_);
        }
        if (err != ErrorCode::OK) {
            if (method_ == CaptureMethod::Auto) {
                FS_LOG("CaptureSession: WGC session start failed (err=%d), falling back to BitBlt", (int)err);
                active_method_ = CaptureMethod::BitBlt;
            } else {
                return err;
            }
        } else {
            wgc_session_started_ = true;
        }
    }

    running_.store(true);
    capture_thread_ = std::thread(&CaptureSession::capture_loop, this);

    return ErrorCode::OK;
}

ErrorCode CaptureSession::stop() {
    running_.store(false);
    if (capture_thread_.joinable()) {
        capture_thread_.join();
    }

    if (wgc_session_started_) {
        wgc_.stop_session();
        wgc_session_started_ = false;
    }

    if (active_method_ == CaptureMethod::DXGI) {
        dxgi_.shutdown();
    }

    return ErrorCode::OK;
}

ErrorCode CaptureSession::do_capture(FrameData& frame) {
    switch (active_method_) {
    case CaptureMethod::DXGI:
        return dxgi_.capture_frame(frame);

    case CaptureMethod::WGC:
        if (wgc_session_started_) {
            return wgc_.capture_from_session(frame);
        }
        if (target_type_ == TargetType::Window)
            return wgc_.capture_window(reinterpret_cast<void*>(static_cast<intptr_t>(target_id_)), frame);
        return wgc_.capture_monitor(target_id_, frame);

    case CaptureMethod::BitBlt:
        if (target_type_ == TargetType::Window)
            return bitblt_.capture_window(reinterpret_cast<void*>(static_cast<intptr_t>(target_id_)), frame);
        return bitblt_.capture_monitor(target_id_, frame);

    default:
        return ErrorCode::Unsupported;
    }
}

ErrorCode CaptureSession::capture_single(FrameData& frame) {
    ErrorCode err = do_capture(frame);

    if (err != ErrorCode::OK && method_ == CaptureMethod::Auto) {
        FS_LOG("CaptureSession: capture failed (err=%d), falling back to BitBlt", (int)err);

        if (wgc_session_started_) {
            wgc_.stop_session();
            wgc_session_started_ = false;
        }

        active_method_ = CaptureMethod::BitBlt;
        if (target_type_ == TargetType::Window)
            return bitblt_.capture_window(reinterpret_cast<void*>(static_cast<intptr_t>(target_id_)), frame);
        return bitblt_.capture_monitor(target_id_, frame);
    }

    return err;
}

void CaptureSession::capture_loop() {
    auto frame_duration = std::chrono::nanoseconds(static_cast<int64_t>(1000000000.0 / target_fps_));
    auto next_time = std::chrono::steady_clock::now();
    int consecutive_failures = 0;
    constexpr int MAX_CONSECUTIVE_FAILURES = 10;

    FS_LOG("CAPTURE: capture_loop START, target_type=%d, target_id=%d, method=%d, active_method=%d, fps=%d",
           (int)target_type_, target_id_, (int)method_, (int)active_method_, target_fps_);

    int loop_tick = 0;
    auto last_log_time = std::chrono::steady_clock::now();

    while (running_.load()) {
        next_time += frame_duration;
        loop_tick++;

        if (target_type_ == TargetType::Window) {
            HWND hwnd = reinterpret_cast<HWND>(static_cast<intptr_t>(target_id_));
            if (!IsWindow(hwnd)) {
                FS_LOG("CaptureSession: target window no longer exists, stopping");
                running_.store(false);
                break;
            }
        }

        // Log each loop tick (rate-limited to ~1 per second to avoid spamming)
        auto now_for_log = std::chrono::steady_clock::now();
        auto since_last_log_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
            now_for_log - last_log_time).count();
        if (since_last_log_ms >= 1000) {
            FS_LOG("CAPTURE: loop tick=%d, calling do_capture (active_method=%d, wgc_session=%d)",
                   loop_tick, (int)active_method_, wgc_session_started_ ? 1 : 0);
            last_log_time = now_for_log;
        }

        FrameData frame;
        ErrorCode err = do_capture(frame);

        if (err == ErrorCode::OK) {
            consecutive_failures = 0;
            FS_LOG("CAPTURE: frame captured OK, %dx%d, stride=%d, calling callback, tick=%d",
                   frame.width, frame.height, frame.stride, loop_tick);
            if (callback_) {
                FrameData cb_frame = std::move(frame);
                callback_(cb_frame);
                if (cb_frame.owns_data && cb_frame.data) {
                    fs::frame_free(cb_frame.data, cb_frame.stride * cb_frame.height);
                }
                FS_LOG("CAPTURE: callback returned, tick=%d", loop_tick);
            } else {
                FS_LOG("CAPTURE: no callback set, frame dropped, tick=%d", loop_tick);
            }
        } else if (err == ErrorCode::NoOutput) {
            consecutive_failures = 0;
            FS_LOG("CAPTURE: NoOutput (window minimized or no frame), continuing loop, tick=%d", loop_tick);
        } else {
            consecutive_failures++;
            FS_LOG("CAPTURE: capture failed (err=%d, consecutive=%d/%d), tick=%d",
                (int)err, consecutive_failures, MAX_CONSECUTIVE_FAILURES, loop_tick);

            if (method_ == CaptureMethod::Auto && consecutive_failures >= 3) {
                FS_LOG("CaptureSession: falling back to BitBlt (consecutive=%d)", consecutive_failures);

                if (wgc_session_started_) {
                    wgc_.stop_session();
                    wgc_session_started_ = false;
                }

                active_method_ = CaptureMethod::BitBlt;
                consecutive_failures = 0;
            }

            if (consecutive_failures >= MAX_CONSECUTIVE_FAILURES) {
                FS_LOG_ERROR("CaptureSession: %d consecutive failures, auto-stopping", consecutive_failures);
                running_.store(false);
                break;
            }
        }

        auto now = std::chrono::steady_clock::now();
        if (now < next_time) {
            std::this_thread::sleep_until(next_time);
        } else {
            // Frame ran over budget; log to detect slow capture (e.g. 3s WGC timeout)
            auto over_ms = std::chrono::duration_cast<std::chrono::milliseconds>(now - next_time).count();
            FS_LOG("CAPTURE: frame over budget by %lldms, tick=%d, err=%d",
                   (long long)over_ms, loop_tick, (int)err);
            next_time = now;
        }
    }

    FS_LOG("CAPTURE: capture_loop EXIT, ticks=%d", loop_tick);
}

ErrorCode CaptureSession::save_frame(FrameData& frame, const wchar_t* path, ImageFormat format, float quality) {
    return ImageEncoder::encode(frame, path, format, quality);
}

}
