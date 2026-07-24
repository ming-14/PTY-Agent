#pragma once

#include "common.h"
#include <vector>

namespace fs {

class EnumHelper {
public:
    static std::vector<MonitorInfo> enumerate_monitors();
    static std::vector<WindowInfo> enumerate_windows();
};

}
