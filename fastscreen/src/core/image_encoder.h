#pragma once

#include "common.h"
#include <wincodec.h>
#include <string>

namespace fs {

class ImageEncoder {
public:
    static ErrorCode encode_png(const FrameData& frame, const wchar_t* path);
    static ErrorCode encode_jpeg(const FrameData& frame, const wchar_t* path, float quality = 0.9f);
    static ErrorCode encode_bmp(const FrameData& frame, const wchar_t* path);
    static ErrorCode encode(const FrameData& frame, const wchar_t* path, ImageFormat format, float quality = 0.9f);

    static ErrorCode encode_to_buffer(const FrameData& frame, ImageFormat format, float quality, uint8_t** out_buf, int* out_size);
    static ErrorCode encode_to_buffer_scaled(const FrameData& frame, ImageFormat format, float quality, int target_width, int target_height, uint8_t** out_buf, int* out_size);

    static ErrorCode init();
    static void shutdown();

private:
    static IWICImagingFactory* factory_;
    static bool initialized_;

    static ErrorCode ensure_factory();
    static ErrorCode create_encoder_from_format(ImageFormat format, IWICBitmapEncoder** encoder, const GUID& container_format);
};

}
