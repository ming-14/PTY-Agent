// Windows.Graphics.Capture 捕获：支持窗口与显示器捕获，可为指定目标建立
// 持久会话（独立 STA 线程 + 消息泵），或一次性捕获（FrameArrived 等待）。
// WinRT 对象的线程模型与资源释放详见 session_thread_func 内的注释。
#include "wgc_capture.h"
#include <chrono>
#include <cstring>

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "runtimeobject.lib")
#pragma comment(lib, "user32.lib")  // IsIconic

#include <winrt/Windows.Foundation.h>
#include <windows.graphics.capture.interop.h>
#include <windows.graphics.directx.direct3d11.interop.h>

namespace fs {

static bool g_wgc_supported_cached = false;
static bool g_wgc_support_checked = false;

WGCCapture::WGCCapture() {}

WGCCapture::~WGCCapture() {
    shutdown();
}

static void safe_init_apartment() {
    HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (FAILED(hr) && hr != RPC_E_CHANGED_MODE && hr != S_FALSE) {
        FS_LOG_ERROR("WGC: CoInitializeEx failed hr=0x%08x", (unsigned)hr);
    }
}

bool WGCCapture::is_supported() {
    if (g_wgc_support_checked) return g_wgc_supported_cached;

    safe_init_apartment();

    try {
        g_wgc_supported_cached = winrt::Windows::Graphics::Capture::GraphicsCaptureSession::IsSupported();
    } catch (...) {
        g_wgc_supported_cached = false;
    }
    g_wgc_support_checked = true;
    return g_wgc_supported_cached;
}

ErrorCode WGCCapture::initialize() {
    if (d3d_ready_) return ErrorCode::OK;
    return ensure_d3d();
}

void WGCCapture::shutdown() {
    stop_session();

    if (staging_texture_) {
        staging_texture_->Release();
        staging_texture_ = nullptr;
    }
    if (d3d_context_) {
        d3d_context_->Release();
        d3d_context_ = nullptr;
    }
    if (d3d_device_) {
        d3d_device_->Release();
        d3d_device_ = nullptr;
    }
    d3d_ready_ = false;
}

ErrorCode WGCCapture::ensure_d3d() {
    if (d3d_ready_) return ErrorCode::OK;

    D3D_FEATURE_LEVEL feature_level;
    HRESULT hr = D3D11CreateDevice(
        nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0,
        nullptr, 0, D3D11_SDK_VERSION,
        &d3d_device_, &feature_level, &d3d_context_
    );
    if (FAILED(hr)) {
        hr = D3D11CreateDevice(
            nullptr, D3D_DRIVER_TYPE_WARP, nullptr, 0,
            nullptr, 0, D3D11_SDK_VERSION,
            &d3d_device_, &feature_level, &d3d_context_
        );
        if (FAILED(hr)) return ErrorCode::CaptureFailed;
    }

    d3d_ready_ = true;
    return ErrorCode::OK;
}

static winrt::Windows::Graphics::DirectX::Direct3D11::IDirect3DDevice create_d3d_device(ID3D11Device* d3d_device) {
    IDXGIDevice* dxgi_device = nullptr;
    HRESULT hr = d3d_device->QueryInterface(__uuidof(IDXGIDevice), (void**)&dxgi_device);
    if (FAILED(hr)) {
        FS_LOG_ERROR("WGC: QI IDXGIDevice failed hr=0x%08x", (unsigned)hr);
        return nullptr;
    }

    ::IInspectable* inspectable = nullptr;
    hr = CreateDirect3D11DeviceFromDXGIDevice(dxgi_device, &inspectable);
    dxgi_device->Release();
    if (FAILED(hr)) {
        FS_LOG_ERROR("WGC: CreateDirect3D11DeviceFromDXGIDevice failed hr=0x%08x", (unsigned)hr);
        return nullptr;
    }

    winrt::Windows::Graphics::DirectX::Direct3D11::IDirect3DDevice result = nullptr;
    if (inspectable) {
        try {
            winrt::copy_from_abi(result, inspectable);
        } catch (winrt::hresult_error const& e) {
            FS_LOG_ERROR("WGC: copy_from_abi failed hr=0x%08x", (unsigned)e.code().value);
        }
        inspectable->Release();
    }
    return result;
}

static winrt::Windows::Graphics::Capture::GraphicsCaptureItem create_item_for_window(void* hwnd) {
    auto factory = winrt::get_activation_factory<winrt::Windows::Graphics::Capture::GraphicsCaptureItem>();
    auto interop = factory.as<IGraphicsCaptureItemInterop>();
    winrt::Windows::Graphics::Capture::GraphicsCaptureItem item = nullptr;
    interop->CreateForWindow(
        reinterpret_cast<HWND>(hwnd),
        winrt::guid_of<winrt::Windows::Graphics::Capture::GraphicsCaptureItem>(),
        winrt::put_abi(item)
    );
    return item;
}

static winrt::Windows::Graphics::Capture::GraphicsCaptureItem create_item_for_monitor(HMONITOR monitor) {
    auto factory = winrt::get_activation_factory<winrt::Windows::Graphics::Capture::GraphicsCaptureItem>();
    auto interop = factory.as<IGraphicsCaptureItemInterop>();
    winrt::Windows::Graphics::Capture::GraphicsCaptureItem item = nullptr;
    interop->CreateForMonitor(
        monitor,
        winrt::guid_of<winrt::Windows::Graphics::Capture::GraphicsCaptureItem>(),
        winrt::put_abi(item)
    );
    return item;
}

static bool extract_texture_from_surface(
    winrt::Windows::Graphics::DirectX::Direct3D11::IDirect3DSurface const& surface,
    ID3D11Texture2D** out_texture)
{
    auto surface_unknown = winrt::get_unknown(surface);
    if (!surface_unknown) return false;

    using namespace Windows::Graphics::DirectX::Direct3D11;
    IDirect3DDxgiInterfaceAccess* dxgi_access = nullptr;
    HRESULT hr = surface_unknown->QueryInterface(__uuidof(IDirect3DDxgiInterfaceAccess), (void**)&dxgi_access);
    if (FAILED(hr)) return false;

    hr = dxgi_access->GetInterface(__uuidof(ID3D11Texture2D), (void**)out_texture);
    dxgi_access->Release();
    return SUCCEEDED(hr) && *out_texture;
}

static void disable_border(winrt::Windows::Graphics::Capture::GraphicsCaptureSession const& session) {
    try { session.IsBorderRequired(false); } catch (...) {}
    try { session.IsCursorCaptureEnabled(false); } catch (...) {}
}

static ErrorCode wgc_copy_frame_to_output(
    ID3D11Device* d3d_device,
    ID3D11DeviceContext* d3d_context,
    ID3D11Texture2D* src_texture,
    ID3D11Texture2D** staging_texture,
    int* staging_width,
    int* staging_height,
    FrameData& frame)
{
    D3D11_TEXTURE2D_DESC desc;
    src_texture->GetDesc(&desc);

    int width = desc.Width;
    int height = desc.Height;

    if (!*staging_texture || *staging_width != width || *staging_height != height) {
        if (*staging_texture) {
            (*staging_texture)->Release();
            *staging_texture = nullptr;
        }

        D3D11_TEXTURE2D_DESC staging_desc = {};
        staging_desc.Width = width;
        staging_desc.Height = height;
        staging_desc.MipLevels = 1;
        staging_desc.ArraySize = 1;
        staging_desc.Format = desc.Format;
        staging_desc.SampleDesc.Count = 1;
        staging_desc.Usage = D3D11_USAGE_STAGING;
        staging_desc.BindFlags = 0;
        staging_desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        staging_desc.MiscFlags = 0;

        HRESULT hr = d3d_device->CreateTexture2D(&staging_desc, nullptr, staging_texture);
        if (FAILED(hr)) return ErrorCode::CaptureFailed;
        *staging_width = width;
        *staging_height = height;
    }

    d3d_context->CopyResource(*staging_texture, src_texture);

    D3D11_MAPPED_SUBRESOURCE mapped;
    HRESULT hr = d3d_context->Map(*staging_texture, 0, D3D11_MAP_READ, 0, &mapped);
    if (FAILED(hr)) return ErrorCode::CaptureFailed;

    int stride = width * 4;
    int data_size = stride * height;

    frame.data = fs::frame_alloc(data_size);
    frame.owns_data = true;
    frame.width = width;
    frame.height = height;
    frame.stride = stride;
    frame.bpp = 4;
    frame.timestamp_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now().time_since_epoch()
    ).count();

    if (mapped.RowPitch == stride) {
        memcpy(frame.data, mapped.pData, data_size);
    } else {
        uint8_t* src = (uint8_t*)mapped.pData;
        uint8_t* dst = frame.data;
        for (int y = 0; y < height; y++) {
            memcpy(dst, src, stride);
            src += mapped.RowPitch;
            dst += stride;
        }
    }

    d3d_context->Unmap(*staging_texture, 0);
    return ErrorCode::OK;
}

void WGCCapture::session_thread_func(void* hwnd_or_null, int monitor_index, bool is_window) {
    // CoInitializeEx must be called at thread entry; CoUninitialize must be called
    // on all exit paths. WinRT deferred destruction depends on apartment uninit;
    // without CoUninitialize, FramePool D3D textures leak (~5MB per start/stop,
    // = 2 * window_width * 4 * window_height bytes).
    //
    // IMPORTANT: must use COINIT_APARTMENTTHREADED (STA/ASTA), NOT
    // COINIT_MULTITHREADED (MTA). WGC GraphicsCaptureItem creation via
    // IGraphicsCaptureItemInterop::CreateForWindow requires an STA/ASTA
    // apartment; in MTA it returns a null item, which surfaces as
    // ErrorCode::Unsupported (-6). Before the leak fix this thread had no
    // explicit CoInitializeEx, so C++/WinRT auto-initialised it to ASTA and
    // WGC worked. The message loop below (PeekMessage + MsgWaitForMultipleObjectsEx)
    // is also the standard STA pump pattern required by WGC frame delivery.
    HRESULT co_hr = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    bool co_need_uninit = (SUCCEEDED(co_hr) && co_hr != S_FALSE);
    if (co_hr == RPC_E_CHANGED_MODE) {
        FS_LOG_ERROR("WGC: CoInitializeEx RPC_E_CHANGED_MODE (existing apartment mismatch)");
    } else if (FAILED(co_hr)) {
        FS_LOG_ERROR("WGC: CoInitializeEx failed hr=0x%08x", (unsigned)co_hr);
    }

    // Core logic is wrapped in a lambda: early returns inside the lambda only
    // exit the lambda, not the function, so CoUninitialize is always reached.
    auto run_session_core = [this, hwnd_or_null, monitor_index, is_window]() {
    try {
        winrt::Windows::Graphics::Capture::GraphicsCaptureItem item = nullptr;

        if (is_window) {
            item = create_item_for_window(hwnd_or_null);
        } else {
            HMONITOR target_monitor = nullptr;
            struct MonitorEnumCtx {
                int target_index;
                int current_index;
                HMONITOR monitor;
            };
            MonitorEnumCtx ctx = { monitor_index, 0, nullptr };
            auto callback = [](HMONITOR hMonitor, HDC, LPRECT, LPARAM lParam) -> BOOL {
                auto* ctx = reinterpret_cast<MonitorEnumCtx*>(lParam);
                if (ctx->current_index == ctx->target_index) {
                    ctx->monitor = hMonitor;
                    return FALSE;
                }
                ctx->current_index++;
                return TRUE;
            };
            EnumDisplayMonitors(nullptr, nullptr, callback, reinterpret_cast<LPARAM>(&ctx));
            target_monitor = ctx.monitor;

            if (!target_monitor) {
                session_start_result_ = ErrorCode::InvalidParam;
                session_initialized_.store(true);
                session_cv_.notify_one();
                return;
            }
            item = create_item_for_monitor(target_monitor);
        }

        if (!item) {
            session_start_result_ = is_window ? ErrorCode::Unsupported : ErrorCode::CaptureFailed;
            session_initialized_.store(true);
            session_cv_.notify_one();
            return;
        }

        auto device = create_d3d_device(d3d_device_);
        if (!device) {
            session_start_result_ = ErrorCode::CaptureFailed;
            session_initialized_.store(true);
            session_cv_.notify_one();
            return;
        }

        {
            std::lock_guard<std::mutex> lock(session_mutex_);
            session_device_ = device;
            pool_size_ = item.Size();
            session_pool_ = winrt::Windows::Graphics::Capture::Direct3D11CaptureFramePool::Create(
                device,
                winrt::Windows::Graphics::DirectX::DirectXPixelFormat::B8G8R8A8UIntNormalized,
                2,
                pool_size_
            );
            if (!session_pool_) {
                session_start_result_ = ErrorCode::CaptureFailed;
                session_initialized_.store(true);
                session_cv_.notify_one();
                return;
            }

            session_ = session_pool_.CreateCaptureSession(item);
            disable_border(session_);
            session_.StartCapture();

            // Register Closed: stop session gracefully when capture item is closed
            closed_revoker_ = item.Closed(winrt::auto_revoke,
                [this](winrt::Windows::Graphics::Capture::GraphicsCaptureItem const&,
                       winrt::Windows::Foundation::IInspectable const&) {
                    FS_LOG("WGC: GraphicsCaptureItem Closed");
                    session_stop_requested_.store(true);
                });
        }

        session_start_result_ = ErrorCode::OK;
        session_initialized_.store(true);
        session_cv_.notify_one();

        FS_LOG("WGC: persistent session started (dedicated thread), size=%dx%d", item.Size().Width, item.Size().Height);

        while (!session_stop_requested_.load()) {
            MSG msg;
            while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
                TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }

            // 检查 resize 请求：capture_from_session 在 capture_loop 线程检测到 ContentSize
            // 变化时设置 resize_pending_。此 STA 线程可安全调用 Recreate（同线程创建 session_pool_）。
            if (resize_pending_.load()) {
                std::lock_guard<std::mutex> lock(session_mutex_);
                resize_pending_.store(false);
                if (session_pool_) {
                    auto& target = resize_target_size_;
                    FS_LOG("WGC: STA thread processing resize, target=%dx%d, current pool=%dx%d",
                           target.Width, target.Height, pool_size_.Width, pool_size_.Height);
                    try {
                        session_pool_.Recreate(
                            session_device_,
                            winrt::Windows::Graphics::DirectX::DirectXPixelFormat::B8G8R8A8UIntNormalized,
                            2,
                            target);
                        pool_size_ = target;
                        FS_LOG("WGC: STA Recreate OK, new pool_size=%dx%d",
                               pool_size_.Width, pool_size_.Height);
                    } catch (winrt::hresult_error const& e) {
                        FS_LOG_ERROR("WGC: STA Recreate failed 0x%08x", (unsigned)e.code().value);
                    } catch (...) {
                        FS_LOG_ERROR("WGC: STA Recreate unknown exception");
                    }
                }
            }

            frame_cv_.notify_one();
            MsgWaitForMultipleObjectsEx(0, nullptr, 4, QS_ALLINPUT, MWMO_INPUTAVAILABLE);
        }

        {
            std::lock_guard<std::mutex> lock(session_mutex_);
            if (session_) {
                session_.Close();
                session_ = nullptr;
            }
            if (session_pool_) {
                session_pool_.Close();
                session_pool_ = nullptr;
            }
            session_device_ = nullptr;
        }

        // Explicitly revoke closed_revoker_ (which holds a reference to item)
        // and release item. closed_revoker_ is a WGCCapture member; without
        // revoke, it keeps item alive until ~WGCCapture, preventing the WGC
        // capture pipeline from being torn down before CoUninitialize.
        closed_revoker_.revoke();
        item = nullptr;

        FS_LOG("WGC: persistent session thread exiting");
    } catch (winrt::hresult_error const& e) {
        FS_LOG_ERROR("WGC: session_thread hresult_error 0x%08x", (unsigned)e.code().value);
        // Revoke on exception path too, preventing closed_revoker_ from holding item
        closed_revoker_.revoke();
        session_start_result_ = ErrorCode::CaptureFailed;
        session_initialized_.store(true);
        session_cv_.notify_one();
    } catch (...) {
        FS_LOG_ERROR("WGC: session_thread unknown exception");
        closed_revoker_.revoke();
        session_start_result_ = ErrorCode::CaptureFailed;
        session_initialized_.store(true);
        session_cv_.notify_one();
    }
    }; // end of run_session_core lambda

    // Execute core logic (early returns inside lambda only exit the lambda)
    run_session_core();

    // CoUninitialize flushes WinRT deferred destruction, releasing D3D textures
    // held by FramePool. Must be called after session_pool_/session_/item are
    // all released, otherwise WinRT objects will not be destroyed.
    if (co_need_uninit) {
        CoUninitialize();
    }
}

ErrorCode WGCCapture::start_window_session(void* hwnd) {
    if (!hwnd) return ErrorCode::InvalidParam;

    ErrorCode err = ensure_d3d();
    if (err != ErrorCode::OK) return err;

    if (!is_supported()) return ErrorCode::Unsupported;

    if (session_active_.load()) {
        stop_session();
    }

    session_stop_requested_.store(false);
    session_initialized_.store(false);
    session_active_.store(true);
    session_hwnd_ = hwnd;  // cache hwnd for IsIconic check in capture_from_session

    session_thread_ = std::thread(&WGCCapture::session_thread_func, this, hwnd, 0, true);

    {
        std::unique_lock<std::mutex> lock(session_mutex_);
        session_cv_.wait(lock, [this] { return session_initialized_.load(); });
    }

    if (session_start_result_ != ErrorCode::OK) {
        if (session_thread_.joinable()) session_thread_.join();
        session_active_.store(false);
        return session_start_result_;
    }

    return ErrorCode::OK;
}

ErrorCode WGCCapture::start_monitor_session(int monitor_index) {
    ErrorCode err = ensure_d3d();
    if (err != ErrorCode::OK) return err;

    if (!is_supported()) return ErrorCode::Unsupported;

    if (session_active_.load()) {
        stop_session();
    }

    session_stop_requested_.store(false);
    session_initialized_.store(false);
    session_active_.store(true);
    session_hwnd_ = nullptr;  // monitor session does not use IsIconic check

    session_thread_ = std::thread(&WGCCapture::session_thread_func, this, nullptr, monitor_index, false);

    {
        std::unique_lock<std::mutex> lock(session_mutex_);
        session_cv_.wait(lock, [this] { return session_initialized_.load(); });
    }

    if (session_start_result_ != ErrorCode::OK) {
        if (session_thread_.joinable()) session_thread_.join();
        session_active_.store(false);
        return session_start_result_;
    }

    return ErrorCode::OK;
}

ErrorCode WGCCapture::stop_session() {
    if (!session_active_.load()) return ErrorCode::OK;

    session_stop_requested_.store(true);

    if (session_thread_.joinable()) {
        session_thread_.join();
    }

    session_active_.store(false);
    session_hwnd_ = nullptr;  // clear cached window handle
    session_device_ = nullptr;

    FS_LOG("WGC: persistent session stopped");
    return ErrorCode::OK;
}

ErrorCode WGCCapture::capture_from_session(FrameData& frame) {
    // Entry log: hwnd and current pool size to correlate with resize events
    FS_LOG("WGC: capture_from_session ENTER, hwnd=%p, pool_size=%dx%d",
           session_hwnd_, pool_size_.Width, pool_size_.Height);

    if (!session_active_.load()) {
        FS_LOG_ERROR("WGC: capture_from_session called but no active session");
        return ErrorCode::NotInitialized;
    }

    // When window is minimized WGC produces no frames; return NoOutput to avoid false stall detection
    if (session_hwnd_) {
        BOOL iconic = IsIconic(reinterpret_cast<HWND>(session_hwnd_));
        if (iconic) {
            FS_LOG("WGC: window is minimized (IsIconic=true), return NoOutput, hwnd=%p", session_hwnd_);
            return ErrorCode::NoOutput;
        }
    }

    std::unique_lock<std::mutex> lock(session_mutex_);

    if (!session_pool_) {
        FS_LOG_ERROR("WGC: capture_from_session pool is null");
        return ErrorCode::NotInitialized;
    }

    frame_available_.store(false);

    auto start = std::chrono::steady_clock::now();
    int attempt = 0;
    while (true) {
        attempt++;
        auto capture_frame = session_pool_.TryGetNextFrame();
        if (capture_frame) {
            // Log acquired frame and compare ContentSize with current pool size
            auto content_size = capture_frame.ContentSize();
            FS_LOG("WGC: frame acquired, attempt=%d, content_size=%dx%d, pool_size=%dx%d",
                   attempt, content_size.Width, content_size.Height,
                   pool_size_.Width, pool_size_.Height);

            // ContentSize 与 pool_size_ 不一致时设置 resize 请求标志。
            // Recreate 必须在创建 session_pool_ 的 STA 线程执行（跨线程会 RPC_E_WRONG_THREAD）。
            // 设置 resize_pending_ 让 session_thread_func 消息循环检查并执行 Recreate。
            // 当前帧仍按 pool_size_ 尺寸复制，Recreate 完成后后续帧自动使用新尺寸。
            if (content_size.Width != pool_size_.Width || content_size.Height != pool_size_.Height) {
                FS_LOG("WGC: ContentSize != pool_size (%dx%d vs %dx%d), requesting STA Recreate",
                       content_size.Width, content_size.Height,
                       pool_size_.Width, pool_size_.Height);
                if (!resize_pending_.load()) {
                    resize_target_size_ = content_size;
                    resize_pending_.store(true);
                }
            }

            auto surface = capture_frame.Surface();
            ID3D11Texture2D* tex = nullptr;
            if (extract_texture_from_surface(surface, &tex) && tex) {
                winrt::com_ptr<ID3D11Texture2D> texture;
                texture.copy_from(tex);

                FS_LOG("WGC: copying frame to output, size=%dx%d, attempt=%d",
                       content_size.Width, content_size.Height, attempt);
                ErrorCode copy_err = wgc_copy_frame_to_output(
                    d3d_device_, d3d_context_, texture.get(),
                    &staging_texture_, &staging_width_, &staging_height_, frame
                );
                if (copy_err == ErrorCode::OK) {
                    FS_LOG("WGC: copy OK, returning frame %dx%d", frame.width, frame.height);
                } else {
                    FS_LOG_ERROR("WGC: copy_frame_to_output failed err=%d, attempt=%d",
                                 (int)copy_err, attempt);
                }
                return copy_err;
            } else {
                FS_LOG_ERROR("WGC: extract_texture_from_surface failed, attempt=%d", attempt);
            }
        }

        auto elapsed = std::chrono::steady_clock::now() - start;
        auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(elapsed).count();
        if (elapsed >= std::chrono::seconds(3)) {
            FS_LOG_ERROR("WGC: capture_from_session TIMEOUT after 3s, attempts=%d, pool_size=%dx%d, hwnd=%p",
                         attempt, pool_size_.Width, pool_size_.Height, session_hwnd_);
            break;
        }

        // Log waiting state every 50 attempts to avoid spamming
        if (attempt % 50 == 0) {
            FS_LOG("WGC: still waiting for frame, attempt=%d, elapsed=%lldms, pool_size=%dx%d",
                   attempt, (long long)elapsed_ms, pool_size_.Width, pool_size_.Height);
        }

        frame_cv_.wait_for(lock, std::chrono::milliseconds(4));
        if (!session_pool_) {
            FS_LOG_ERROR("WGC: session_pool_ became null while waiting, attempt=%d", attempt);
            return ErrorCode::NotInitialized;
        }
    }

    FS_LOG_ERROR("WGC: capture_from_session returning NoOutput (timeout), attempts=%d", attempt);
    return ErrorCode::NoOutput;
}

ErrorCode WGCCapture::capture_window(void* hwnd, FrameData& frame) {
    if (!hwnd) {
        FS_LOG_ERROR("WGC: capture_window hwnd is null");
        return ErrorCode::InvalidParam;
    }

    ErrorCode err = ensure_d3d();
    if (err != ErrorCode::OK) {
        FS_LOG_ERROR("WGC: ensure_d3d failed err=%d", (int)err);
        return err;
    }

    if (!is_supported()) {
        FS_LOG_ERROR("WGC: not supported on this system");
        return ErrorCode::Unsupported;
    }

    safe_init_apartment();

    try {
        auto item = create_item_for_window(hwnd);
        if (!item) {
            FS_LOG_ERROR("WGC: CreateForWindow failed for hwnd=%p (window not supported by WGC)", hwnd);
            return ErrorCode::Unsupported;
        }
        FS_LOG("WGC: CreateForWindow OK hwnd=%p size=%dx%d", hwnd, item.Size().Width, item.Size().Height);

        auto device = create_d3d_device(d3d_device_);
        if (!device) {
            FS_LOG_ERROR("WGC: create_d3d_device failed");
            return ErrorCode::CaptureFailed;
        }
        FS_LOG("WGC: create_d3d_device OK (window)");

        auto pool = winrt::Windows::Graphics::Capture::Direct3D11CaptureFramePool::Create(
            device,
            winrt::Windows::Graphics::DirectX::DirectXPixelFormat::B8G8R8A8UIntNormalized,
            2,
            item.Size()
        );
        if (!pool) {
            FS_LOG_ERROR("WGC: FramePool::Create failed");
            return ErrorCode::CaptureFailed;
        }

        auto session = pool.CreateCaptureSession(item);

        disable_border(session);

        volatile long frame_ready = 0;
        winrt::com_ptr<ID3D11Texture2D> pending_texture;

        pool.FrameArrived([&](auto& p, auto const&) {
            if (InterlockedCompareExchange(&frame_ready, 0, 0) != 0) return;

            auto capture_frame = p.TryGetNextFrame();
            if (!capture_frame) return;

            auto surface = capture_frame.Surface();
            ID3D11Texture2D* tex = nullptr;
            if (extract_texture_from_surface(surface, &tex) && tex) {
                pending_texture.copy_from(tex);
                InterlockedExchange(&frame_ready, 1);
            }
        });

        session.StartCapture();
        FS_LOG("WGC: StartCapture OK, waiting for frame...");

        DWORD start = GetTickCount();
        bool got_frame = false;
        while (GetTickCount() - start < 3000) {
            MSG msg;
            while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
                TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
            if (InterlockedCompareExchange(&frame_ready, 0, 1) == 1) {
                got_frame = true;
                break;
            }
            MsgWaitForMultipleObjectsEx(0, nullptr, 4, QS_ALLINPUT, MWMO_INPUTAVAILABLE);
        }

        if (!got_frame || !pending_texture) {
            FS_LOG_ERROR("WGC: timed out waiting for FrameArrived (3s)");
            session.Close();
            pool.Close();
            return ErrorCode::NoOutput;
        }

        FS_LOG("WGC: frame received, copying...");

        ErrorCode copy_err = wgc_copy_frame_to_output(
            d3d_device_, d3d_context_, pending_texture.get(),
            &staging_texture_, &staging_width_, &staging_height_, frame
        );

        if (copy_err != ErrorCode::OK) {
            FS_LOG_ERROR("WGC: copy_frame_to_output failed err=%d", (int)copy_err);
        }

        session.Close();
        pool.Close();

        return copy_err;
    } catch (winrt::hresult_error const& e) {
        FS_LOG_ERROR("WGC: hresult_error 0x%08x", (unsigned)e.code().value);
        return ErrorCode::CaptureFailed;
    } catch (std::exception const& e) {
        FS_LOG_ERROR("WGC: exception: %s", e.what());
        return ErrorCode::CaptureFailed;
    } catch (...) {
        FS_LOG_ERROR("WGC: unknown exception");
        return ErrorCode::CaptureFailed;
    }
}

ErrorCode WGCCapture::capture_monitor(int monitor_index, FrameData& frame) {
    ErrorCode err = ensure_d3d();
    if (err != ErrorCode::OK) {
        FS_LOG_ERROR("WGC: ensure_d3d failed err=%d", (int)err);
        return err;
    }

    if (!is_supported()) {
        FS_LOG_ERROR("WGC: not supported on this system");
        return ErrorCode::Unsupported;
    }

    safe_init_apartment();

    HMONITOR target_monitor = nullptr;
    {
        struct MonitorEnumCtx {
            int target_index;
            int current_index;
            HMONITOR monitor;
        };

        MonitorEnumCtx ctx = { monitor_index, 0, nullptr };
        auto callback = [](HMONITOR hMonitor, HDC, LPRECT, LPARAM lParam) -> BOOL {
            auto* ctx = reinterpret_cast<MonitorEnumCtx*>(lParam);
            if (ctx->current_index == ctx->target_index) {
                ctx->monitor = hMonitor;
                return FALSE;
            }
            ctx->current_index++;
            return TRUE;
        };

        EnumDisplayMonitors(nullptr, nullptr, callback, reinterpret_cast<LPARAM>(&ctx));
        target_monitor = ctx.monitor;
    }

    if (!target_monitor) {
        FS_LOG_ERROR("WGC: monitor %d not found", monitor_index);
        return ErrorCode::InvalidParam;
    }

    try {
        auto item = create_item_for_monitor(target_monitor);
        if (!item) {
            FS_LOG_ERROR("WGC: CreateForMonitor failed for monitor %d", monitor_index);
            return ErrorCode::CaptureFailed;
        }
        FS_LOG("WGC: CreateForMonitor OK monitor=%d size=%dx%d", monitor_index, item.Size().Width, item.Size().Height);

        auto device = create_d3d_device(d3d_device_);
        if (!device) {
            FS_LOG_ERROR("WGC: create_d3d_device failed");
            return ErrorCode::CaptureFailed;
        }
        FS_LOG("WGC: create_d3d_device OK");

        auto pool = winrt::Windows::Graphics::Capture::Direct3D11CaptureFramePool::Create(
            device,
            winrt::Windows::Graphics::DirectX::DirectXPixelFormat::B8G8R8A8UIntNormalized,
            2,
            item.Size()
        );
        if (!pool) {
            FS_LOG_ERROR("WGC: FramePool::Create failed");
            return ErrorCode::CaptureFailed;
        }
        FS_LOG("WGC: FramePool::Create OK");

        auto session = pool.CreateCaptureSession(item);
        FS_LOG("WGC: CreateCaptureSession OK");

        disable_border(session);

        volatile long frame_ready = 0;
        winrt::com_ptr<ID3D11Texture2D> pending_texture;

        pool.FrameArrived([&](auto& p, auto const&) {
            if (InterlockedCompareExchange(&frame_ready, 0, 0) != 0) return;

            auto capture_frame = p.TryGetNextFrame();
            if (!capture_frame) return;

            auto surface = capture_frame.Surface();
            ID3D11Texture2D* tex = nullptr;
            if (extract_texture_from_surface(surface, &tex) && tex) {
                pending_texture.copy_from(tex);
                InterlockedExchange(&frame_ready, 1);
            }
        });

        session.StartCapture();
        FS_LOG("WGC: StartCapture OK (monitor), waiting for frame...");

        DWORD start = GetTickCount();
        bool got_frame = false;
        while (GetTickCount() - start < 3000) {
            MSG msg;
            while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
                TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
            if (InterlockedCompareExchange(&frame_ready, 0, 1) == 1) {
                got_frame = true;
                break;
            }
            MsgWaitForMultipleObjectsEx(0, nullptr, 4, QS_ALLINPUT, MWMO_INPUTAVAILABLE);
        }

        if (!got_frame || !pending_texture) {
            FS_LOG_ERROR("WGC: timed out waiting for FrameArrived (3s, monitor)");
            session.Close();
            pool.Close();
            return ErrorCode::NoOutput;
        }

        ErrorCode copy_err = wgc_copy_frame_to_output(
            d3d_device_, d3d_context_, pending_texture.get(),
            &staging_texture_, &staging_width_, &staging_height_, frame
        );

        if (copy_err != ErrorCode::OK) {
            FS_LOG_ERROR("WGC: copy_frame_to_output failed err=%d", (int)copy_err);
        }

        session.Close();
        pool.Close();

        return copy_err;
    } catch (winrt::hresult_error const& e) {
        FS_LOG_ERROR("WGC: hresult_error 0x%08x", (unsigned)e.code().value);
        return ErrorCode::CaptureFailed;
    } catch (std::exception const& e) {
        FS_LOG_ERROR("WGC: exception: %s", e.what());
        return ErrorCode::CaptureFailed;
    } catch (...) {
        FS_LOG_ERROR("WGC: unknown exception");
        return ErrorCode::CaptureFailed;
    }
}

}
