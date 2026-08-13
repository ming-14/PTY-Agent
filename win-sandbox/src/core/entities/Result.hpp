// =============================================================================
// Result<T> - 错误处理模板（core 层）
//
// 用于跨模块边界返回值，携带成功值或错误信息。
// 灵感来自 Rust 的 Result<T, E> 与 C++23 的 std::expected。
//
// 用法：
//   Result<int> r = SomeFunc();
//   if (r) { use(r.Value()); }
//   else   { log(r.Message()); }
//
//   Result<void> rv = AnotherFunc();
//   if (!rv) { ... }
// =============================================================================
#pragma once

#include "core/entities/ErrorCode.hpp"

#include <optional>
#include <stdexcept>
#include <string>
#include <utility>

namespace winsandbox {

// 错误信息
struct Error {
    ErrorCode code = ErrorCode::Ok;
    std::string message;

    static Error Make(ErrorCode c, std::string m = "") {
        return Error{c, std::move(m)};
    }
};

// 通用 Result<T> 模板
template <typename T>
class Result {
public:
    // 成功构造（携带值）
    Result(T v) : value_(std::move(v)) {}  // NOLINT(google-explicit-constructor)
    // 失败构造（携带错误）
    Result(Error e) : error_(std::move(e)) {}  // NOLINT(google-explicit-constructor)

    static Result Ok(T v) { return Result(std::move(v)); }
    static Result Err(ErrorCode c, std::string m = "") {
        return Result(Error::Make(c, std::move(m)));
    }

    bool IsOk() const { return value_.has_value(); }
    bool IsErr() const { return !value_.has_value(); }
    explicit operator bool() const { return IsOk(); }

    // 在错误状态下调用 Value() 会触发 std::optional 空值解引用断言崩溃
    // （MSVC hardening 下 operator*() 对空 optional 直接崩溃）。
    // 这里改为抛出 std::logic_error，让调用方可以捕获并诊断，
    // 而不是进程直接崩溃。调用方应始终先检查 IsOk() 再调用 Value()。
    const T& Value() const {
        if (!value_.has_value()) {
            throw std::logic_error(
                "Result<T>::Value() called on Err result: [" +
                std::to_string(static_cast<int>(error_.code)) + "] " + error_.message);
        }
        return value_.value();
    }
    T& Value() {
        if (!value_.has_value()) {
            throw std::logic_error(
                "Result<T>::Value() called on Err result: [" +
                std::to_string(static_cast<int>(error_.code)) + "] " + error_.message);
        }
        return value_.value();
    }
    const Error& Err() const { return error_; }
    ErrorCode Code() const { return error_.code; }
    const std::string& Message() const { return error_.message; }

private:
    std::optional<T> value_;
    Error error_;
};

// Result<void> 特化
template <>
class Result<void> {
public:
    Result() : ok_(true) {}
    Result(Error e) : ok_(false), error_(std::move(e)) {}  // NOLINT(google-explicit-constructor)

    static Result Ok() { return Result(); }
    static Result Err(ErrorCode c, std::string m = "") {
        return Result(Error::Make(c, std::move(m)));
    }

    bool IsOk() const { return ok_; }
    bool IsErr() const { return !ok_; }
    explicit operator bool() const { return ok_; }

    const Error& Err() const { return error_; }
    ErrorCode Code() const { return error_.code; }
    const std::string& Message() const { return error_.message; }

private:
    bool ok_;
    Error error_;
};

} // namespace winsandbox
