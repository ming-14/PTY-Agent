/**
 * 基础设施层：认证处理模块
 *
 * 提供统一的未授权处理入口，供 wsClient / settingsStorage / fastscreen 等模块调用。
 * 检测到未授权时跳转 /login，防重复跳转。
 *
 * 双通道认证：
 * 1. Cookie（pty_session）：同源请求自动携带
 * 2. X-Auth-Token 头 / authToken query param：跨域场景，token 存 localStorage
 *
 * 支持自定义服务端地址：与 wsClient 共享 localStorage.pty_server_address，
 * 认证状态检查和登录请求均发送到自定义服务端。
 */

const LS_SERVER_ADDR_KEY = 'pty_server_address';
const LS_AUTH_TOKEN_KEY = 'pty_auth_token';

let _redirecting = false;

/**
 * 获取认证 API 的 base URL。
 * 若 localStorage 中有自定义服务端地址则使用，否则为空（同源）。
 * @returns {string} base URL 如 "http://127.0.0.1:18766" 或空串
 */
function _getAuthBaseUrl() {
  const addr = (localStorage.getItem(LS_SERVER_ADDR_KEY) || '').trim();
  if (!addr) return '';
  const proto = location.protocol === 'https:' ? 'https:' : 'http:';
  return proto + '//' + addr;
}

/**
 * 获取存储的认证 token。
 * @returns {string} token 或空串
 */
export function getAuthToken() {
  return (localStorage.getItem(LS_AUTH_TOKEN_KEY) || '').trim();
}

/**
 * 存储认证 token。
 * @param {string} token
 */
export function setAuthToken(token) {
  if (token) {
    localStorage.setItem(LS_AUTH_TOKEN_KEY, token);
  } else {
    localStorage.removeItem(LS_AUTH_TOKEN_KEY);
  }
}

/**
 * 构造认证 headers（供 fetch 使用）。
 * 若 localStorage 中有 token，添加 X-Auth-Token 头。
 * @returns {Object} headers 对象
 */
export function authHeaders() {
  const h = { 'Accept': 'application/json' };
  const token = getAuthToken();
  if (token) h['X-Auth-Token'] = token;
  return h;
}

/**
 * 处理未授权事件：跳转到登录页。
 * 防止多个模块同时触发导致重复跳转。
 */
export function handleUnauthorized() {
  if (_redirecting) return;
  _redirecting = true;
  location.href = '/login';
}

/**
 * 检查认证状态（启动时调用）。
 * 调用 GET /api/auth/status，若 enabled && !authenticated 则跳转登录页。
 * 若端点不存在（认证未启用）则正常继续。
 * 请求发送到自定义服务端地址（若有），与 WebSocket 连接目标一致。
 * @returns {Promise<{enabled: boolean, authenticated: boolean}>}
 */
export async function checkAuthStatus() {
  try {
    const baseUrl = _getAuthBaseUrl();
    const url = baseUrl ? baseUrl + '/api/auth/status' : '/api/auth/status';
    const resp = await fetch(url, {
      method: 'GET',
      headers: authHeaders(),
      credentials: 'include',
    });
    if (resp.ok) {
      const data = await resp.json();
      if (data.enabled && !data.authenticated) {
        handleUnauthorized();
      }
      return data;
    }
    // 端点不存在(404) = 认证未启用，正常继续
    return { enabled: false, authenticated: true };
  } catch (e) {
    // 网络错误：不阻断启动（可能是离线场景）
    return { enabled: false, authenticated: true };
  }
}
