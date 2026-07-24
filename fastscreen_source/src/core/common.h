#pragma once

#include <cstdint>
#include <cstring>
#include <cstdio>
#include <string>
#include <vector>
#include <memory>
#include <functional>
#include <atomic>
#include <mutex>
#include <thread>
#include <condition_variable>

#ifdef FASTSCREEN_EXPORTS
#define FS_API __declspec(dllexport)
#else
#define FS_API __declspec(dllimport)
#endif

#define FS_LOG(fmt, ...) fprintf(stderr, "[FastScreen] " fmt "\n", ##__VA_ARGS__)
#define FS_LOG_ERROR(fmt, ...) fprintf(stderr, "[FastScreen ERROR] " fmt "\n", ##__VA_ARGS__)

namespace fs {

enum class CaptureMethod : int32_t {
    Auto = 0,
    DXGI = 1,
    WGC = 2,
    BitBlt = 3,
};

enum class TargetType : int32_t {
    Monitor = 0,
    Window = 1,
};

enum class ImageFormat : int32_t {
    PNG = 0,
    JPEG = 1,
    BMP = 2,
};

enum class ErrorCode : int32_t {
    OK = 0,
    NotInitialized = -1,
    InvalidParam = -2,
    CaptureFailed = -3,
    EncodeFailed = -4,
    NoOutput = -5,
    Unsupported = -6,
    AlreadyRunning = -7,
    NotRunning = -8,
};

struct MonitorInfo {
    int32_t id;
    wchar_t name[128];
    int32_t left;
    int32_t top;
    int32_t width;
    int32_t height;
    bool primary;
};

struct WindowInfo {
    void* hwnd;
    wchar_t title[256];
    wchar_t class_name[256];
    int32_t left;
    int32_t top;
    int32_t width;
    int32_t height;
    bool visible;
};

struct FrameData {
    uint8_t* data;
    int32_t width;
    int32_t height;
    int32_t stride;
    int32_t bpp;
    int64_t timestamp_ms;
    bool owns_data;

    FrameData() : data(nullptr), width(0), height(0), stride(0), bpp(4), timestamp_ms(0), owns_data(false) {}

    ~FrameData() {
        if (owns_data && data) {
            delete[] data;
            data = nullptr;
        }
    }

    FrameData(const FrameData&) = delete;
    FrameData& operator=(const FrameData&) = delete;

    FrameData(FrameData&& other) noexcept
        : data(other.data), width(other.width), height(other.height),
          stride(other.stride), bpp(other.bpp), timestamp_ms(other.timestamp_ms),
          owns_data(other.owns_data) {
        other.data = nullptr;
        other.owns_data = false;
    }

    FrameData& operator=(FrameData&& other) noexcept {
        if (this != &other) {
            if (owns_data && data) delete[] data;
            data = other.data;
            width = other.width;
            height = other.height;
            stride = other.stride;
            bpp = other.bpp;
            timestamp_ms = other.timestamp_ms;
            owns_data = other.owns_data;
            other.data = nullptr;
            other.owns_data = false;
        }
        return *this;
    }
};

using FrameCallback = std::function<void(FrameData&)>;

uint8_t* frame_alloc(size_t size);
void frame_free(uint8_t* ptr, size_t size);
void frame_pool_cleanup();

}
