#pragma once

#include "common.h"
#include <dxgi1_2.h>
#include <d3d11.h>

namespace fs {

class DXGICapture {
public:
    DXGICapture();
    ~DXGICapture();

    ErrorCode initialize(int monitor_index);
    void shutdown();

    ErrorCode capture_frame(FrameData& frame);
    bool is_available() const { return initialized_; }
    int get_monitor_count();

    static bool is_supported();

private:
    ErrorCode init_d3d();
    ErrorCode init_duplication(int monitor_index);
    void release_duplication();
    ErrorCode copy_texture_to_frame(ID3D11Texture2D* texture, FrameData& frame);

    bool initialized_ = false;
    int monitor_index_ = 0;

    ID3D11Device* d3d_device_ = nullptr;
    ID3D11DeviceContext* d3d_context_ = nullptr;
    IDXGIOutputDuplication* duplication_ = nullptr;
    ID3D11Texture2D* staging_texture_ = nullptr;

    int staging_width_ = 0;
    int staging_height_ = 0;
    DXGI_FORMAT staging_format_ = DXGI_FORMAT_UNKNOWN;
};

}
