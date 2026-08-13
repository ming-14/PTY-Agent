#pragma once

#include "common.h"
#include <atomic>
#include <mutex>
#include <condition_variable>
#include <thread>
#include <array>

namespace fs {

// 固定容量、线程安全的环形缓冲，用于捕获线程与消费线程之间传递帧数据。
template<typename T, size_t N>
class RingBuffer {
public:
    RingBuffer() : head_(0), tail_(0), count_(0) {}

    bool push(T&& item) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (count_ >= N) return false;
        buffer_[head_] = std::move(item);
        head_ = (head_ + 1) % N;
        count_++;
        cv_.notify_one();
        return true;
    }

    bool pop(T& item) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (count_ == 0) return false;
        item = std::move(buffer_[tail_]);
        tail_ = (tail_ + 1) % N;
        count_--;
        return true;
    }

    bool wait_pop(T& item, int timeout_ms) {
        std::unique_lock<std::mutex> lock(mutex_);
        if (cv_.wait_for(lock, std::chrono::milliseconds(timeout_ms),
            [this] { return count_ > 0; })) {
            item = std::move(buffer_[tail_]);
            tail_ = (tail_ + 1) % N;
            count_--;
            return true;
        }
        return false;
    }

    bool empty() const { return count_.load() == 0; }
    size_t size() const { return count_.load(); }

    void clear() {
        std::lock_guard<std::mutex> lock(mutex_);
        while (count_ > 0) {
            buffer_[tail_] = T();
            tail_ = (tail_ + 1) % N;
            count_--;
        }
    }

private:
    std::array<T, N> buffer_;
    size_t head_;
    size_t tail_;
    size_t count_;
    std::mutex mutex_;
    std::condition_variable cv_;
};

}
