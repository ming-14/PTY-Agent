// 基于 WIC 的图像编码：将 BGRA 帧编码为 PNG/JPEG/BMP 写入文件或内存缓冲，
// 内存编码路径支持按目标尺寸缩放（WICBitmapScaler，Fant 插值）。
#include "image_encoder.h"
#include <shlwapi.h>

#pragma comment(lib, "windowscodecs.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "shlwapi.lib")

namespace fs {

IWICImagingFactory* ImageEncoder::factory_ = nullptr;
bool ImageEncoder::initialized_ = false;

ErrorCode ImageEncoder::init() {
    if (initialized_) return ErrorCode::OK;
    return ensure_factory();
}

void ImageEncoder::shutdown() {
    if (factory_) {
        factory_->Release();
        factory_ = nullptr;
    }
    initialized_ = false;
}

ErrorCode ImageEncoder::ensure_factory() {
    if (factory_) return ErrorCode::OK;

    HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (FAILED(hr) && hr != RPC_E_CHANGED_MODE) {
        return ErrorCode::CaptureFailed;
    }

    hr = CoCreateInstance(
        CLSID_WICImagingFactory2,
        nullptr,
        CLSCTX_INPROC_SERVER,
        IID_PPV_ARGS(&factory_)
    );

    if (FAILED(hr)) {
        hr = CoCreateInstance(
            CLSID_WICImagingFactory,
            nullptr,
            CLSCTX_INPROC_SERVER,
            IID_PPV_ARGS(&factory_)
        );
        if (FAILED(hr)) return ErrorCode::CaptureFailed;
    }

    initialized_ = true;
    return ErrorCode::OK;
}

ErrorCode ImageEncoder::encode(const FrameData& frame, const wchar_t* path, ImageFormat format, float quality) {
    switch (format) {
    case ImageFormat::PNG: return encode_png(frame, path);
    case ImageFormat::JPEG: return encode_jpeg(frame, path, quality);
    case ImageFormat::BMP: return encode_bmp(frame, path);
    default: return encode_png(frame, path);
    }
}

ErrorCode ImageEncoder::encode_png(const FrameData& frame, const wchar_t* path) {
    ErrorCode err = ensure_factory();
    if (err != ErrorCode::OK) return err;

    if (!frame.data || frame.width <= 0 || frame.height <= 0) return ErrorCode::InvalidParam;

    IWICBitmapEncoder* encoder = nullptr;
    HRESULT hr = factory_->CreateEncoder(GUID_ContainerFormatPng, nullptr, &encoder);
    if (FAILED(hr)) return ErrorCode::EncodeFailed;

    IStream* stream = nullptr;
    hr = SHCreateStreamOnFileEx(path, STGM_CREATE | STGM_WRITE, 0, TRUE, nullptr, &stream);
    if (FAILED(hr)) {
        encoder->Release();
        return ErrorCode::EncodeFailed;
    }

    hr = encoder->Initialize(stream, WICBitmapEncoderNoCache);
    if (FAILED(hr)) {
        stream->Release();
        encoder->Release();
        return ErrorCode::EncodeFailed;
    }

    IWICBitmapFrameEncode* frame_encode = nullptr;
    hr = encoder->CreateNewFrame(&frame_encode, nullptr);
    if (FAILED(hr)) {
        stream->Release();
        encoder->Release();
        return ErrorCode::EncodeFailed;
    }

    hr = frame_encode->Initialize(nullptr);
    if (FAILED(hr)) {
        frame_encode->Release();
        stream->Release();
        encoder->Release();
        return ErrorCode::EncodeFailed;
    }

    hr = frame_encode->SetSize(frame.width, frame.height);
    if (FAILED(hr)) {
        frame_encode->Release();
        stream->Release();
        encoder->Release();
        return ErrorCode::EncodeFailed;
    }

    WICPixelFormatGUID pixel_format = GUID_WICPixelFormat32bppBGRA;
    hr = frame_encode->SetPixelFormat(&pixel_format);
    if (FAILED(hr)) {
        frame_encode->Release();
        stream->Release();
        encoder->Release();
        return ErrorCode::EncodeFailed;
    }

    hr = frame_encode->WritePixels(
        frame.height,
        frame.stride,
        frame.stride * frame.height,
        frame.data
    );

    if (FAILED(hr)) {
        frame_encode->Release();
        stream->Release();
        encoder->Release();
        return ErrorCode::EncodeFailed;
    }

    hr = frame_encode->Commit();
    frame_encode->Release();

    if (FAILED(hr)) {
        stream->Release();
        encoder->Release();
        return ErrorCode::EncodeFailed;
    }

    hr = encoder->Commit();
    stream->Release();
    encoder->Release();

    if (FAILED(hr)) return ErrorCode::EncodeFailed;

    return ErrorCode::OK;
}

ErrorCode ImageEncoder::encode_jpeg(const FrameData& frame, const wchar_t* path, float quality) {
    ErrorCode err = ensure_factory();
    if (err != ErrorCode::OK) return err;

    if (!frame.data || frame.width <= 0 || frame.height <= 0) return ErrorCode::InvalidParam;

    IWICBitmapEncoder* encoder = nullptr;
    HRESULT hr = factory_->CreateEncoder(GUID_ContainerFormatJpeg, nullptr, &encoder);
    if (FAILED(hr)) return ErrorCode::EncodeFailed;

    IStream* stream = nullptr;
    hr = SHCreateStreamOnFileEx(path, STGM_CREATE | STGM_WRITE, 0, TRUE, nullptr, &stream);
    if (FAILED(hr)) {
        encoder->Release();
        return ErrorCode::EncodeFailed;
    }

    hr = encoder->Initialize(stream, WICBitmapEncoderNoCache);
    if (FAILED(hr)) {
        stream->Release();
        encoder->Release();
        return ErrorCode::EncodeFailed;
    }

    IWICBitmapFrameEncode* frame_encode = nullptr;
    IPropertyBag2* props = nullptr;
    hr = encoder->CreateNewFrame(&frame_encode, &props);
    if (FAILED(hr)) {
        stream->Release();
        encoder->Release();
        return ErrorCode::EncodeFailed;
    }

    PROPBAG2 option = {};
    option.pstrName = (LPOLESTR)L"ImageQuality";
    VARIANT var_val;
    VariantInit(&var_val);
    var_val.vt = VT_R4;
    var_val.fltVal = quality;
    props->Write(1, &option, &var_val);
    VariantClear(&var_val);

    hr = frame_encode->Initialize(props);
    props->Release();
    if (FAILED(hr)) {
        frame_encode->Release();
        stream->Release();
        encoder->Release();
        return ErrorCode::EncodeFailed;
    }

    hr = frame_encode->SetSize(frame.width, frame.height);
    if (FAILED(hr)) {
        frame_encode->Release();
        stream->Release();
        encoder->Release();
        return ErrorCode::EncodeFailed;
    }

    WICPixelFormatGUID pixel_format = GUID_WICPixelFormat32bppBGRA;
    hr = frame_encode->SetPixelFormat(&pixel_format);
    if (FAILED(hr)) {
        frame_encode->Release();
        stream->Release();
        encoder->Release();
        return ErrorCode::EncodeFailed;
    }

    hr = frame_encode->WritePixels(
        frame.height,
        frame.stride,
        frame.stride * frame.height,
        frame.data
    );

    if (FAILED(hr)) {
        frame_encode->Release();
        stream->Release();
        encoder->Release();
        return ErrorCode::EncodeFailed;
    }

    hr = frame_encode->Commit();
    frame_encode->Release();
    if (FAILED(hr)) {
        stream->Release();
        encoder->Release();
        return ErrorCode::EncodeFailed;
    }

    hr = encoder->Commit();
    stream->Release();
    encoder->Release();

    if (FAILED(hr)) return ErrorCode::EncodeFailed;

    return ErrorCode::OK;
}

ErrorCode ImageEncoder::encode_bmp(const FrameData& frame, const wchar_t* path) {
    ErrorCode err = ensure_factory();
    if (err != ErrorCode::OK) return err;

    if (!frame.data || frame.width <= 0 || frame.height <= 0) return ErrorCode::InvalidParam;

    IWICBitmapEncoder* encoder = nullptr;
    HRESULT hr = factory_->CreateEncoder(GUID_ContainerFormatBmp, nullptr, &encoder);
    if (FAILED(hr)) return ErrorCode::EncodeFailed;

    IStream* stream = nullptr;
    hr = SHCreateStreamOnFileEx(path, STGM_CREATE | STGM_WRITE, 0, TRUE, nullptr, &stream);
    if (FAILED(hr)) {
        encoder->Release();
        return ErrorCode::EncodeFailed;
    }

    hr = encoder->Initialize(stream, WICBitmapEncoderNoCache);
    if (FAILED(hr)) {
        stream->Release();
        encoder->Release();
        return ErrorCode::EncodeFailed;
    }

    IWICBitmapFrameEncode* frame_encode = nullptr;
    hr = encoder->CreateNewFrame(&frame_encode, nullptr);
    if (FAILED(hr)) {
        stream->Release();
        encoder->Release();
        return ErrorCode::EncodeFailed;
    }

    hr = frame_encode->Initialize(nullptr);
    if (FAILED(hr)) {
        frame_encode->Release();
        stream->Release();
        encoder->Release();
        return ErrorCode::EncodeFailed;
    }

    hr = frame_encode->SetSize(frame.width, frame.height);
    if (FAILED(hr)) {
        frame_encode->Release();
        stream->Release();
        encoder->Release();
        return ErrorCode::EncodeFailed;
    }

    WICPixelFormatGUID pixel_format = GUID_WICPixelFormat32bppBGRA;
    hr = frame_encode->SetPixelFormat(&pixel_format);
    if (FAILED(hr)) {
        frame_encode->Release();
        stream->Release();
        encoder->Release();
        return ErrorCode::EncodeFailed;
    }

    hr = frame_encode->WritePixels(
        frame.height,
        frame.stride,
        frame.stride * frame.height,
        frame.data
    );

    if (FAILED(hr)) {
        frame_encode->Release();
        stream->Release();
        encoder->Release();
        return ErrorCode::EncodeFailed;
    }

    hr = frame_encode->Commit();
    frame_encode->Release();
    if (FAILED(hr)) {
        stream->Release();
        encoder->Release();
        return ErrorCode::EncodeFailed;
    }

    hr = encoder->Commit();
    stream->Release();
    encoder->Release();

    if (FAILED(hr)) return ErrorCode::EncodeFailed;

    return ErrorCode::OK;
}

ErrorCode ImageEncoder::encode_to_buffer(const FrameData& frame, ImageFormat format, float quality, uint8_t** out_buf, int* out_size) {
    return encode_to_buffer_scaled(frame, format, quality, frame.width, frame.height, out_buf, out_size);
}

ErrorCode ImageEncoder::encode_to_buffer_scaled(const FrameData& frame, ImageFormat format, float quality, int target_width, int target_height, uint8_t** out_buf, int* out_size) {
    if (!out_buf || !out_size) return ErrorCode::InvalidParam;
    *out_buf = nullptr;
    *out_size = 0;

    ErrorCode err = ensure_factory();
    if (err != ErrorCode::OK) return err;

    if (!frame.data || frame.width <= 0 || frame.height <= 0) return ErrorCode::InvalidParam;
    if (target_width <= 0 || target_height <= 0) return ErrorCode::InvalidParam;

    GUID container_format;
    switch (format) {
    case ImageFormat::JPEG: container_format = GUID_ContainerFormatJpeg; break;
    case ImageFormat::BMP: container_format = GUID_ContainerFormatBmp; break;
    default: container_format = GUID_ContainerFormatPng; break;
    }

    IWICBitmap* bitmap = nullptr;
    HRESULT hr = factory_->CreateBitmapFromMemory(
        frame.width, frame.height,
        GUID_WICPixelFormat32bppBGRA,
        frame.stride,
        frame.stride * frame.height,
        frame.data,
        &bitmap
    );
    if (FAILED(hr)) return ErrorCode::EncodeFailed;

    IWICBitmapSource* source = bitmap;

    IWICBitmapScaler* scaler = nullptr;
    bool need_scale = (target_width != frame.width || target_height != frame.height);
    if (need_scale) {
        hr = factory_->CreateBitmapScaler(&scaler);
        if (FAILED(hr)) {
            bitmap->Release();
            return ErrorCode::EncodeFailed;
        }
        hr = scaler->Initialize(bitmap, target_width, target_height, WICBitmapInterpolationModeFant);
        if (FAILED(hr)) {
            scaler->Release();
            bitmap->Release();
            return ErrorCode::EncodeFailed;
        }
        source = scaler;
    }

    IWICBitmapEncoder* encoder = nullptr;
    hr = factory_->CreateEncoder(container_format, nullptr, &encoder);
    if (FAILED(hr)) {
        if (scaler) scaler->Release();
        bitmap->Release();
        return ErrorCode::EncodeFailed;
    }

    IStream* stream = nullptr;
    hr = CreateStreamOnHGlobal(nullptr, TRUE, &stream);
    if (FAILED(hr)) {
        encoder->Release();
        if (scaler) scaler->Release();
        bitmap->Release();
        return ErrorCode::EncodeFailed;
    }

    hr = encoder->Initialize(stream, WICBitmapEncoderNoCache);
    if (FAILED(hr)) {
        stream->Release();
        encoder->Release();
        if (scaler) scaler->Release();
        bitmap->Release();
        return ErrorCode::EncodeFailed;
    }

    IWICBitmapFrameEncode* frame_encode = nullptr;
    IPropertyBag2* props = nullptr;
    hr = encoder->CreateNewFrame(&frame_encode, &props);
    if (FAILED(hr)) {
        stream->Release();
        encoder->Release();
        if (scaler) scaler->Release();
        bitmap->Release();
        return ErrorCode::EncodeFailed;
    }

    if (format == ImageFormat::JPEG && props) {
        PROPBAG2 option = {};
        option.pstrName = (LPOLESTR)L"ImageQuality";
        VARIANT var_val;
        VariantInit(&var_val);
        var_val.vt = VT_R4;
        var_val.fltVal = quality;
        props->Write(1, &option, &var_val);
        VariantClear(&var_val);
    }

    hr = frame_encode->Initialize(props);
    if (props) props->Release();
    if (FAILED(hr)) {
        frame_encode->Release();
        stream->Release();
        encoder->Release();
        if (scaler) scaler->Release();
        bitmap->Release();
        return ErrorCode::EncodeFailed;
    }

    hr = frame_encode->SetSize(target_width, target_height);
    if (FAILED(hr)) {
        frame_encode->Release();
        stream->Release();
        encoder->Release();
        if (scaler) scaler->Release();
        bitmap->Release();
        return ErrorCode::EncodeFailed;
    }

    WICPixelFormatGUID pixel_format = GUID_WICPixelFormat32bppBGRA;
    hr = frame_encode->SetPixelFormat(&pixel_format);
    if (FAILED(hr)) {
        frame_encode->Release();
        stream->Release();
        encoder->Release();
        if (scaler) scaler->Release();
        bitmap->Release();
        return ErrorCode::EncodeFailed;
    }

    hr = frame_encode->WriteSource(source, nullptr);
    if (FAILED(hr)) {
        frame_encode->Release();
        stream->Release();
        encoder->Release();
        if (scaler) scaler->Release();
        bitmap->Release();
        return ErrorCode::EncodeFailed;
    }

    hr = frame_encode->Commit();
    frame_encode->Release();
    if (FAILED(hr)) {
        stream->Release();
        encoder->Release();
        if (scaler) scaler->Release();
        bitmap->Release();
        return ErrorCode::EncodeFailed;
    }

    hr = encoder->Commit();
    encoder->Release();
    if (FAILED(hr)) {
        stream->Release();
        if (scaler) scaler->Release();
        bitmap->Release();
        return ErrorCode::EncodeFailed;
    }

    STATSTG stat = {};
    hr = stream->Stat(&stat, STATFLAG_DEFAULT);
    if (FAILED(hr) || stat.cbSize.HighPart != 0) {
        stream->Release();
        if (scaler) scaler->Release();
        bitmap->Release();
        return ErrorCode::EncodeFailed;
    }

    int size = (int)stat.cbSize.LowPart;
    uint8_t* buf = (uint8_t*)CoTaskMemAlloc(size);
    if (!buf) {
        stream->Release();
        if (scaler) scaler->Release();
        bitmap->Release();
        return ErrorCode::EncodeFailed;
    }

    LARGE_INTEGER offset = {};
    hr = stream->Seek(offset, STREAM_SEEK_SET, nullptr);
    if (FAILED(hr)) {
        CoTaskMemFree(buf);
        stream->Release();
        if (scaler) scaler->Release();
        bitmap->Release();
        return ErrorCode::EncodeFailed;
    }

    ULONG bytes_read = 0;
    hr = stream->Read(buf, size, &bytes_read);
    stream->Release();
    if (scaler) scaler->Release();
    bitmap->Release();

    if (FAILED(hr) || (int)bytes_read != size) {
        CoTaskMemFree(buf);
        return ErrorCode::EncodeFailed;
    }

    *out_buf = buf;
    *out_size = size;
    return ErrorCode::OK;
}

}
