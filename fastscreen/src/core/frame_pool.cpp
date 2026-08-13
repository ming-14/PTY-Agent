#include "common.h"
#include <mutex>
#include <vector>

namespace fs {

// 帧内存池：缓存最近释放的缓冲供后续帧复用，减少反复 new/delete。
// frame_alloc 复用"容量>=size 且 <2*size"的缓存项；超大缓冲直接释放不入池。
static std::mutex g_pool_mutex;
static std::vector<std::pair<uint8_t*, size_t>> g_pool_free_list;
static constexpr size_t POOL_MAX_BUFFERS = 8;
static constexpr size_t POOL_MAX_SINGLE = 64 * 1024 * 1024;

uint8_t* frame_alloc(size_t size) {
    {
        std::lock_guard<std::mutex> lock(g_pool_mutex);
        for (size_t i = 0; i < g_pool_free_list.size(); i++) {
            if (g_pool_free_list[i].second >= size && g_pool_free_list[i].second < size * 2) {
                uint8_t* ptr = g_pool_free_list[i].first;
                if (i != g_pool_free_list.size() - 1) {
                    g_pool_free_list[i] = g_pool_free_list.back();
                }
                g_pool_free_list.pop_back();
                return ptr;
            }
        }
    }
    return new uint8_t[size];
}

void frame_free(uint8_t* ptr, size_t size) {
    if (!ptr) return;
    if (size > POOL_MAX_SINGLE) {
        delete[] ptr;
        return;
    }
    std::lock_guard<std::mutex> lock(g_pool_mutex);
    if (g_pool_free_list.size() < POOL_MAX_BUFFERS) {
        g_pool_free_list.push_back({ptr, size});
    } else {
        delete[] ptr;
    }
}

void frame_pool_cleanup() {
    std::lock_guard<std::mutex> lock(g_pool_mutex);
    for (auto& p : g_pool_free_list) {
        delete[] p.first;
    }
    g_pool_free_list.clear();
}

}
