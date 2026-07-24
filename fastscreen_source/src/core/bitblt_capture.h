#pragma once

#include "common.h"
#include <Windows.h>

namespace fs {

class BitBltCapture {
public:
    BitBltCapture();
    ~BitBltCapture();

    ErrorCode capture_monitor(int monitor_index, FrameData& frame);
    ErrorCode capture_window(void* hwnd, FrameData& frame);

    static bool is_supported() { return true; }

private:
    ErrorCode capture_rect(int x, int y, int w, int h, FrameData& frame);
};

}
