// DXGI Desktop Duplication 捕获：性能最优的整屏捕获方案。
// 采集到的纹理拷贝到 staging 纹理后映射回读，并按格式转换为统一 BGRA 输出。
#include "dxgi_capture.h"
#include <chrono>
#include <cstdio>
#include <immintrin.h>

#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "d3d11.lib")

namespace fs {

static bool g_dxgi_supported_cached = false;
static bool g_dxgi_support_checked = false;

DXGICapture::DXGICapture() {}

DXGICapture::~DXGICapture() {
    shutdown();
}

bool DXGICapture::is_supported() {
    if (g_dxgi_support_checked) return g_dxgi_supported_cached;

    IDXGIFactory1* factory = nullptr;
    HRESULT hr = CreateDXGIFactory1(__uuidof(IDXGIFactory1), (void**)&factory);
    if (FAILED(hr)) {
        g_dxgi_supported_cached = false;
        g_dxgi_support_checked = true;
        return false;
    }
    factory->Release();
    g_dxgi_supported_cached = true;
    g_dxgi_support_checked = true;
    return true;
}

int DXGICapture::get_monitor_count() {
    IDXGIFactory1* factory = nullptr;
    HRESULT hr = CreateDXGIFactory1(__uuidof(IDXGIFactory1), (void**)&factory);
    if (FAILED(hr)) return 0;

    int count = 0;
    IDXGIAdapter* adapter = nullptr;
    for (UINT i = 0; SUCCEEDED(factory->EnumAdapters(i, &adapter)); i++) {
        IDXGIOutput* output = nullptr;
        for (UINT j = 0; SUCCEEDED(adapter->EnumOutputs(j, &output)); j++) {
            count++;
            output->Release();
        }
        adapter->Release();
    }
    factory->Release();
    return count > 0 ? count : 1;
}

ErrorCode DXGICapture::initialize(int monitor_index) {
    if (initialized_) return ErrorCode::OK;

    monitor_index_ = monitor_index;

    ErrorCode err = init_d3d();
    if (err != ErrorCode::OK) return err;

    err = init_duplication(monitor_index);
    if (err != ErrorCode::OK) {
        shutdown();
        return err;
    }

    initialized_ = true;
    return ErrorCode::OK;
}

ErrorCode DXGICapture::init_d3d() {
    D3D_FEATURE_LEVEL feature_level;
    HRESULT hr = D3D11CreateDevice(
        nullptr,
        D3D_DRIVER_TYPE_HARDWARE,
        nullptr,
        0,
        nullptr,
        0,
        D3D11_SDK_VERSION,
        &d3d_device_,
        &feature_level,
        &d3d_context_
    );

    if (FAILED(hr)) {
        FS_LOG_ERROR("DXGI: D3D11CreateDevice(HARDWARE) failed hr=0x%08x, trying WARP", (unsigned)hr);
        hr = D3D11CreateDevice(
            nullptr,
            D3D_DRIVER_TYPE_WARP,
            nullptr,
            0,
            nullptr,
            0,
            D3D11_SDK_VERSION,
            &d3d_device_,
            &feature_level,
            &d3d_context_
        );
        if (FAILED(hr)) {
            FS_LOG_ERROR("DXGI: D3D11CreateDevice(WARP) also failed hr=0x%08x", (unsigned)hr);
            return ErrorCode::CaptureFailed;
        }
    }

    FS_LOG("DXGI: D3D11 device created OK");
    return ErrorCode::OK;
}

ErrorCode DXGICapture::init_duplication(int monitor_index) {
    IDXGIDevice* dxgi_device = nullptr;
    HRESULT hr = d3d_device_->QueryInterface(__uuidof(IDXGIDevice), (void**)&dxgi_device);
    if (FAILED(hr)) {
        FS_LOG_ERROR("DXGI: QueryInterface IDXGIDevice failed hr=0x%08x", (unsigned)hr);
        return ErrorCode::CaptureFailed;
    }

    IDXGIAdapter* adapter = nullptr;
    hr = dxgi_device->GetAdapter(&adapter);
    dxgi_device->Release();
    if (FAILED(hr)) {
        FS_LOG_ERROR("DXGI: GetAdapter failed hr=0x%08x", (unsigned)hr);
        return ErrorCode::CaptureFailed;
    }

    IDXGIOutput* output = nullptr;
    hr = adapter->EnumOutputs(monitor_index, &output);
    adapter->Release();
    if (FAILED(hr)) {
        FS_LOG_ERROR("DXGI: EnumOutputs(%d) failed hr=0x%08x", monitor_index, (unsigned)hr);
        return ErrorCode::CaptureFailed;
    }

    IDXGIOutput1* output1 = nullptr;
    hr = output->QueryInterface(__uuidof(IDXGIOutput1), (void**)&output1);
    output->Release();
    if (FAILED(hr)) {
        FS_LOG_ERROR("DXGI: QueryInterface IDXGIOutput1 failed hr=0x%08x", (unsigned)hr);
        return ErrorCode::CaptureFailed;
    }

    hr = output1->DuplicateOutput(d3d_device_, &duplication_);
    output1->Release();
    if (FAILED(hr)) {
        if (hr == DXGI_ERROR_NOT_CURRENTLY_AVAILABLE) {
            FS_LOG_ERROR("DXGI: DuplicateOutput NOT_CURRENTLY_AVAILABLE (already duplicated?)");
            return ErrorCode::NoOutput;
        }
        FS_LOG_ERROR("DXGI: DuplicateOutput failed hr=0x%08x", (unsigned)hr);
        return ErrorCode::CaptureFailed;
    }

    FS_LOG("DXGI: Desktop Duplication initialized for monitor %d", monitor_index);
    return ErrorCode::OK;
}

void DXGICapture::release_duplication() {
    if (duplication_) {
        duplication_->ReleaseFrame();
        duplication_->Release();
        duplication_ = nullptr;
    }
}

void DXGICapture::shutdown() {
    if (!initialized_) return;

    if (staging_texture_) {
        staging_texture_->Release();
        staging_texture_ = nullptr;
    }

    release_duplication();

    if (d3d_context_) {
        d3d_context_->Release();
        d3d_context_ = nullptr;
    }
    if (d3d_device_) {
        d3d_device_->Release();
        d3d_device_ = nullptr;
    }

    initialized_ = false;
}

ErrorCode DXGICapture::capture_frame(FrameData& frame) {
    if (!initialized_ || !duplication_) {
        FS_LOG_ERROR("DXGI: capture_frame called but not initialized");
        return ErrorCode::NotInitialized;
    }

    DXGI_OUTDUPL_FRAME_INFO frame_info;
    IDXGIResource* resource = nullptr;

    HRESULT hr = duplication_->AcquireNextFrame(0, &frame_info, &resource);

    if (hr == DXGI_ERROR_WAIT_TIMEOUT) {
        hr = duplication_->AcquireNextFrame(50, &frame_info, &resource);
    }

    if (hr == DXGI_ERROR_WAIT_TIMEOUT) {
        return ErrorCode::NoOutput;
    }
    if (hr == DXGI_ERROR_ACCESS_LOST) {
        FS_LOG("DXGI: ACCESS_LOST, reinitializing duplication");
        release_duplication();
        ErrorCode err = init_duplication(monitor_index_);
        if (err != ErrorCode::OK) return err;
        hr = duplication_->AcquireNextFrame(50, &frame_info, &resource);
        if (hr == DXGI_ERROR_WAIT_TIMEOUT) return ErrorCode::NoOutput;
    }
    if (FAILED(hr)) {
        FS_LOG_ERROR("DXGI: AcquireNextFrame failed hr=0x%08x", (unsigned)hr);
        return ErrorCode::CaptureFailed;
    }

    ID3D11Texture2D* texture = nullptr;
    hr = resource->QueryInterface(__uuidof(ID3D11Texture2D), (void**)&texture);
    resource->Release();
    if (FAILED(hr)) {
        FS_LOG_ERROR("DXGI: QI ID3D11Texture2D failed hr=0x%08x", (unsigned)hr);
        duplication_->ReleaseFrame();
        return ErrorCode::CaptureFailed;
    }

    ErrorCode err = copy_texture_to_frame(texture, frame);

    texture->Release();
    duplication_->ReleaseFrame();

    if (err != ErrorCode::OK) return err;

    bool all_zero = true;
    int check_bytes = frame.stride * frame.height;
    int check_limit = check_bytes > 1024 ? 1024 : check_bytes;
    for (int i = 0; i < check_limit; i++) {
        if (frame.data[i] != 0) { all_zero = false; break; }
    }

    // 全 0 帧检测：部分显卡会周期性返回全黑帧，识别后重试最多 5 次，仍全 0 则报 NoOutput。
    if (all_zero) {
        fs::frame_free(frame.data, frame.stride * frame.height);
        frame.data = nullptr;
        frame.owns_data = false;
        frame.width = 0;
        frame.height = 0;
        frame.stride = 0;

        for (int retry = 0; retry < 5; retry++) {
            DXGI_OUTDUPL_FRAME_INFO retry_info;
            IDXGIResource* retry_res = nullptr;
            hr = duplication_->AcquireNextFrame(16, &retry_info, &retry_res);
            if (hr == DXGI_ERROR_WAIT_TIMEOUT) continue;
            if (FAILED(hr)) break;

            ID3D11Texture2D* retry_tex = nullptr;
            hr = retry_res->QueryInterface(__uuidof(ID3D11Texture2D), (void**)&retry_tex);
            retry_res->Release();
            if (FAILED(hr)) {
                duplication_->ReleaseFrame();
                continue;
            }

            err = copy_texture_to_frame(retry_tex, frame);
            retry_tex->Release();
            duplication_->ReleaseFrame();

            if (err != ErrorCode::OK) return err;

            all_zero = true;
            check_limit = (frame.stride * frame.height > 1024) ? 1024 : frame.stride * frame.height;
            for (int i = 0; i < check_limit; i++) {
                if (frame.data[i] != 0) { all_zero = false; break; }
            }
            if (!all_zero) return ErrorCode::OK;

            fs::frame_free(frame.data, frame.stride * frame.height);
            frame.data = nullptr;
            frame.owns_data = false;
            frame.width = 0;
            frame.height = 0;
            frame.stride = 0;
        }

        return ErrorCode::NoOutput;
    }

    return ErrorCode::OK;
}

// R8G8B8A8 → BGRA：使用 AVX2 通道重排（pshufb）批量完成 RGBA→BGRA 字节交换。
static void convert_r8g8b8a8_to_bgra(const uint8_t* src, uint8_t* dst, int pixel_count) {
    int i = 0;
    const int simd_end = pixel_count & ~7;
    __m128i shuffle_mask = _mm_setr_epi8(2,1,0,3, 6,5,4,7, 10,9,8,11, 14,13,12,15);
    for (; i < simd_end; i += 8) {
        __m256i v = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(src + i * 4));
        __m128i lo = _mm256_extracti128_si256(v, 0);
        __m128i hi = _mm256_extracti128_si256(v, 1);
        lo = _mm_shuffle_epi8(lo, shuffle_mask);
        hi = _mm_shuffle_epi8(hi, shuffle_mask);
        __m256i result = _mm256_set_m128i(hi, lo);
        _mm256_storeu_si256(reinterpret_cast<__m256i*>(dst + i * 4), result);
    }
    for (; i < pixel_count; i++) {
        dst[i * 4 + 0] = src[i * 4 + 2];
        dst[i * 4 + 1] = src[i * 4 + 1];
        dst[i * 4 + 2] = src[i * 4 + 0];
        dst[i * 4 + 3] = src[i * 4 + 3];
    }
}

// IEEE 754 half-float（16 位）→ float（32 位）位运算转换，供 R16G16B16A16_FLOAT 使用。
static inline uint16_t half_to_float_bits(uint16_t h) {
    uint32_t sign = (h >> 15) & 0x1;
    uint32_t exponent = (h >> 10) & 0x1F;
    uint32_t mantissa = h & 0x3FF;
    uint32_t f;
    if (exponent == 0) {
        if (mantissa == 0) {
            f = sign << 31;
        } else {
            while (!(mantissa & 0x400)) {
                mantissa <<= 1;
                exponent--;
            }
            exponent++;
            mantissa &= ~0x400;
            f = (sign << 31) | ((exponent + 112) << 23) | (mantissa << 13);
        }
    } else if (exponent == 31) {
        f = (sign << 31) | (0xFF << 23) | (mantissa << 13);
    } else {
        f = (sign << 31) | ((exponent + 112) << 23) | (mantissa << 13);
    }
    return f;
}

static void convert_r10g10b10a2_to_bgra(const uint8_t* src, uint8_t* dst, int pixel_count) {
    const uint32_t* src32 = reinterpret_cast<const uint32_t*>(src);
    for (int i = 0; i < pixel_count; i++) {
        uint32_t v = src32[i];
        uint8_t r = (uint8_t)(((v >> 0) & 0x3FF) * 255 / 1023);
        uint8_t g = (uint8_t)(((v >> 10) & 0x3FF) * 255 / 1023);
        uint8_t b = (uint8_t)(((v >> 20) & 0x3FF) * 255 / 1023);
        uint8_t a = (uint8_t)(((v >> 30) & 0x3) * 255 / 3);
        dst[i * 4 + 0] = b;
        dst[i * 4 + 1] = g;
        dst[i * 4 + 2] = r;
        dst[i * 4 + 3] = a;
    }
}

static void convert_r16g16b16a16_to_bgra(const uint8_t* src, uint8_t* dst, int pixel_count, int src_stride, int dst_stride, int width, int height) {
    const uint16_t* src16 = reinterpret_cast<const uint16_t*>(src);
    for (int y = 0; y < height; y++) {
        for (int x = 0; x < width; x++) {
            int si = (y * src_stride / 2) + x * 4;
            int di = y * (dst_stride / 4) + x;
            float r = src16[si + 0] / 65535.0f;
            float g = src16[si + 1] / 65535.0f;
            float b = src16[si + 2] / 65535.0f;
            float a = src16[si + 3] / 65535.0f;
            dst[di * 4 + 0] = (uint8_t)(b * 255.0f);
            dst[di * 4 + 1] = (uint8_t)(g * 255.0f);
            dst[di * 4 + 2] = (uint8_t)(r * 255.0f);
            dst[di * 4 + 3] = (uint8_t)(a * 255.0f);
        }
    }
}

static void convert_r16g16b16a16_float_to_bgra(const uint8_t* src, uint8_t* dst, int src_stride, int dst_stride, int width, int height) {
    for (int y = 0; y < height; y++) {
        const uint16_t* src_row = reinterpret_cast<const uint16_t*>(src + y * src_stride);
        uint8_t* dst_row = dst + y * dst_stride;
        for (int x = 0; x < width; x++) {
            uint32_t rf = half_to_float_bits(src_row[x * 4 + 0]);
            uint32_t gf = half_to_float_bits(src_row[x * 4 + 1]);
            uint32_t bf = half_to_float_bits(src_row[x * 4 + 2]);
            uint32_t af = half_to_float_bits(src_row[x * 4 + 3]);
            float r, g, b, a;
            memcpy(&r, &rf, 4);
            memcpy(&g, &gf, 4);
            memcpy(&b, &bf, 4);
            memcpy(&a, &af, 4);
            dst_row[x * 4 + 0] = (uint8_t)(b * 255.0f);
            dst_row[x * 4 + 1] = (uint8_t)(g * 255.0f);
            dst_row[x * 4 + 2] = (uint8_t)(r * 255.0f);
            dst_row[x * 4 + 3] = (uint8_t)(a * 255.0f);
        }
    }
}

ErrorCode DXGICapture::copy_texture_to_frame(ID3D11Texture2D* texture, FrameData& frame) {
    D3D11_TEXTURE2D_DESC src_desc;
    texture->GetDesc(&src_desc);

    int width = src_desc.Width;
    int height = src_desc.Height;

    if (!staging_texture_ || staging_width_ != width || staging_height_ != height ||
        staging_format_ != src_desc.Format) {
        if (staging_texture_) {
            staging_texture_->Release();
            staging_texture_ = nullptr;
        }

        D3D11_TEXTURE2D_DESC staging_desc = {};
        staging_desc.Width = width;
        staging_desc.Height = height;
        staging_desc.MipLevels = 1;
        staging_desc.ArraySize = 1;
        staging_desc.Format = src_desc.Format;
        staging_desc.SampleDesc.Count = 1;
        staging_desc.Usage = D3D11_USAGE_STAGING;
        staging_desc.BindFlags = 0;
        staging_desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        staging_desc.MiscFlags = 0;

        HRESULT hr = d3d_device_->CreateTexture2D(&staging_desc, nullptr, &staging_texture_);
        if (FAILED(hr)) return ErrorCode::CaptureFailed;

        staging_width_ = width;
        staging_height_ = height;
        staging_format_ = src_desc.Format;
    }

    d3d_context_->CopyResource(staging_texture_, texture);

    D3D11_MAPPED_SUBRESOURCE mapped;
    HRESULT hr = d3d_context_->Map(staging_texture_, 0, D3D11_MAP_READ, 0, &mapped);
    if (FAILED(hr)) return ErrorCode::CaptureFailed;

    int dst_stride = width * 4;
    int data_size = dst_stride * height;

    frame.data = fs::frame_alloc(data_size);
    frame.owns_data = true;
    frame.width = width;
    frame.height = height;
    frame.stride = dst_stride;
    frame.bpp = 4;
    frame.timestamp_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now().time_since_epoch()
    ).count();

    int pixel_count = width * height;

    switch (src_desc.Format) {
    case DXGI_FORMAT_B8G8R8A8_UNORM:
    case DXGI_FORMAT_B8G8R8A8_UNORM_SRGB:
        if (mapped.RowPitch == dst_stride) {
            memcpy(frame.data, mapped.pData, data_size);
        } else {
            uint8_t* src = (uint8_t*)mapped.pData;
            uint8_t* dst = frame.data;
            for (int y = 0; y < height; y++) {
                memcpy(dst, src, dst_stride);
                src += mapped.RowPitch;
                dst += dst_stride;
            }
        }
        break;

    case DXGI_FORMAT_R8G8B8A8_UNORM:
    case DXGI_FORMAT_R8G8B8A8_UNORM_SRGB:
        for (int y = 0; y < height; y++) {
            const uint8_t* src_row = (const uint8_t*)mapped.pData + y * mapped.RowPitch;
            uint8_t* dst_row = frame.data + y * dst_stride;
            convert_r8g8b8a8_to_bgra(src_row, dst_row, width);
        }
        break;

    case DXGI_FORMAT_R10G10B10A2_UNORM:
        for (int y = 0; y < height; y++) {
            const uint8_t* src_row = (const uint8_t*)mapped.pData + y * mapped.RowPitch;
            uint8_t* dst_row = frame.data + y * dst_stride;
            convert_r10g10b10a2_to_bgra(src_row, dst_row, width);
        }
        break;

    case DXGI_FORMAT_R16G16B16A16_FLOAT:
        convert_r16g16b16a16_float_to_bgra(
            (const uint8_t*)mapped.pData, frame.data,
            mapped.RowPitch, dst_stride, width, height);
        break;

    default:
        for (int y = 0; y < height; y++) {
            const uint8_t* src_row = (const uint8_t*)mapped.pData + y * mapped.RowPitch;
            uint8_t* dst_row = frame.data + y * dst_stride;
            int copy_bytes = (mapped.RowPitch < dst_stride) ? mapped.RowPitch : dst_stride;
            memcpy(dst_row, src_row, copy_bytes);
        }
        break;
    }

    d3d_context_->Unmap(staging_texture_, 0);

    return ErrorCode::OK;
}

}
