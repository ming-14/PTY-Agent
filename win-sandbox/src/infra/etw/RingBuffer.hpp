// =============================================================================
// RingBuffer - 无锁 SPSC 环形缓冲（infra 层）
//
// 单生产者（ETW 回调线程）单消费者（Dispatch 线程）无锁环形缓冲。
// 固定大小，满时丢弃最旧事件 + 丢包计数。
//
// 设计要点：
//   1. power-of-2 大小：用 mask 代替 modulo，提高性能
//   2. head/tail 用 atomic + memory_order：head 由生产者写，消费者读；
//      tail 由消费者写，生产者读
//   3. 缓存行对齐：避免 false sharing（head/tail 分在不同 cache line）
//   4. 满时策略：覆盖最旧事件（tail 前进），dropped_count 递增
//   5. 序号：每个事件携带递增 seq，消费者检测 seq 跳跃 → 丢包
//
// 线程模型：
//   - Push：仅在生产者线程调用
//   - Pop/PopBatch：仅在消费者线程调用
//   - GetDroppedCount：任意线程（atomic 读取）
// =============================================================================
#pragma once

#include "core/entities/BehaviorEvent.hpp"

#include <atomic>
#include <cstddef>
#include <vector>
#include <algorithm>

namespace winsandbox {

// 缓存行大小（x64: 64 bytes）
constexpr size_t CACHELINE_SIZE = 64;

class RingBuffer {
public:
    // capacity 会被向上取整到 2 的幂
    explicit RingBuffer(size_t capacity)
        : mask_(next_pow2(capacity) - 1)
        , buffer_(next_pow2(capacity))
    {
    }

    // 禁止拷贝/移动
    RingBuffer(const RingBuffer&) = delete;
    RingBuffer& operator=(const RingBuffer&) = delete;

    // ---- 生产者接口 ----

    // 推入单个事件（覆盖式：满时丢弃最旧）
    void Push(BehaviorEvent&& event) {
        // 分配 seq
        event.seq = next_seq_.fetch_add(1, std::memory_order_relaxed);

        size_t h = head_.load(std::memory_order_relaxed);
        size_t t = tail_.load(std::memory_order_acquire);

        // 检查是否满
        size_t size = h - t;
        if (size >= buffer_.size()) {
            // 满：丢弃最旧事件（tail 前进）
            dropped_count_.fetch_add(1, std::memory_order_relaxed);
            tail_.store(t + 1, std::memory_order_release);
        }

        buffer_[h & mask_] = std::move(event);
        head_.store(h + 1, std::memory_order_release);
    }

    // ---- 消费者接口 ----

    // 批量弹出事件
    // 返回实际弹出的事件数（0 表示缓冲为空）
    size_t PopBatch(std::vector<BehaviorEvent>& out, size_t max_count) {
        size_t h = head_.load(std::memory_order_acquire);
        size_t t = tail_.load(std::memory_order_relaxed);

        size_t available = h - t;
        if (available == 0) return 0;

        size_t count = std::min(available, max_count);
        out.reserve(out.size() + count);

        for (size_t i = 0; i < count; ++i) {
            out.push_back(std::move(buffer_[(t + i) & mask_]));
        }

        tail_.store(t + count, std::memory_order_release);
        return count;
    }

    // 是否为空
    bool Empty() const {
        return head_.load(std::memory_order_acquire) ==
               tail_.load(std::memory_order_acquire);
    }

    // 可读事件数（近似值，多线程下可能不准）
    size_t Size() const {
        return head_.load(std::memory_order_acquire) -
               tail_.load(std::memory_order_acquire);
    }

    // ---- 统计 ----

    uint64_t GetDroppedCount() const {
        return dropped_count_.load(std::memory_order_relaxed);
    }

    uint64_t GetNextSeq() const {
        return next_seq_.load(std::memory_order_relaxed);
    }

private:
    // 向上取整到 2 的幂
    static size_t next_pow2(size_t v) {
        if (v < 2) return 2;
        --v;
        v |= v >> 1;  v |= v >> 2;
        v |= v >> 4;  v |= v >> 8;
        v |= v >> 16; v |= v >> 32;
        return v + 1;
    }

    size_t mask_;
    std::vector<BehaviorEvent> buffer_;

    // alignas 避免 false sharing
    alignas(CACHELINE_SIZE) std::atomic<size_t> head_{0};  // 生产者写
    alignas(CACHELINE_SIZE) std::atomic<size_t> tail_{0};  // 消费者写

    alignas(CACHELINE_SIZE) std::atomic<uint64_t> next_seq_{0};
    alignas(CACHELINE_SIZE) std::atomic<uint64_t> dropped_count_{0};
};

} // namespace winsandbox
