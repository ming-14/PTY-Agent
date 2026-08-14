/**
 * 基础设施层：LogPanel source 适配器。
 *
 * 将领域层 logger.js 的日志接口适配为通用 LogPanel 插件所需的 source 接口。
 * logger.js 是 PTY-Agent 项目特有的日志系统，LogPanel 是通用插件，
 * 本适配器连接两者，保持依赖方向：基础设施 → 领域；插件不反向依赖项目。
 *
 * source 接口：
 *   subscribe(cb)   — 订阅新日志，返回取消订阅函数
 *   getEntries()    — 读取缓冲区快照
 *   clear()         — 清空缓冲区
 *   getSize()       — 当前条数
 *   getCapacity()   — 容量上限
 */
import {
  subscribe,
  getEntries,
  clearBuffer,
  getBufferSize,
  getBufferCapacity,
} from '../domain/logger.js';

export const loggerSource = {
  subscribe,
  getEntries,
  clear: clearBuffer,
  getSize: getBufferSize,
  getCapacity: getBufferCapacity,
};
