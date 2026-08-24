/**
 * PTY-Agent Service Worker
 *
 * 缓存策略：
 * - 安装时预缓存所有静态资源（CSS、vendor JS、字体、RIME 输入法文件）
 * - 运行时动态加载的 JS 模块、字典文件等走缓存优先（stale-while-revalidate）
 * - API 请求（/ws, /api/*）直接走网络，不缓存
 */

const CACHE_NAME = 'pty-agent-v30';

// 安装时预缓存的资源列表
const PRECACHE_URLS = [
  '/',
  '/index.html',
  '/login.html',
  '/css/theme.css',
  '/css/base.css',
  '/css/layout.css',
  '/css/sidebar.css',
  '/css/tabbar.css',
  '/css/terminal.css',
  '/css/components.css',
  '/css/vnc.css',
  '/css/fastscreen.css',
  '/css/dialogs.css',
  '/css/responsive.css',
  '/css/settings.css',
  '/vendor/xterm/xterm.css',
  '/vendor/xterm/xterm.js',
  '/vendor/xterm/xterm-addon-fit.js',
  '/vendor/xterm/xterm-addon-web-links.js',
  '/vendor/rime/rime-plugin.js',
  '/vendor/rime/wasm/rime.js',
  '/vendor/rime/wasm/rime.wasm',
  '/vendor/rime/wasm/rime.data',
  '/vendor/rime/dict/luna-pinyin/luna_pinyin.prism.bin',
  '/vendor/rime/dict/luna-pinyin/luna_pinyin.reverse.bin',
  '/vendor/rime/dict/luna-pinyin/luna_pinyin.schema.yaml',
  '/vendor/rime/dict/luna-pinyin/luna_pinyin.table.bin',
  '/vendor/rime/dict/luna-pinyin/luna_pinyin_fluency.schema.yaml',
  '/vendor/rime/dict/luna-pinyin/luna_quanpin.prism.bin',
  '/vendor/rime/dict/luna-pinyin/luna_quanpin.schema.yaml',
  '/vendor/rime/dict/luna-pinyin/package.json',
  '/vendor/rime/dict/stroke/stroke.prism.bin',
  '/vendor/rime/dict/stroke/stroke.reverse.bin',
  '/vendor/rime/dict/stroke/stroke.schema.yaml',
  '/vendor/rime/dict/stroke/stroke.table.bin',
  '/vendor/rime/dict/stroke/package.json',
  'https://fontsapi.zeoseven.com/442/main/result.css',
  '/js/app.js',
  '/js/application/messageHandlers.js',
  '/js/application/ports.js',
  '/js/application/settingsStore.js',
  '/js/domain/constants.js',
  '/js/domain/formatters.js',
  '/js/domain/logger.js',
  '/js/domain/settingsSchema.js',
  '/js/domain/state.js',
  '/js/infrastructure/auth.js',
  '/js/infrastructure/domUtils.js',
  '/js/infrastructure/fontLoader.js',
  '/js/infrastructure/logPanelAdapter.js',
  '/js/infrastructure/rimeManager.js',
  '/js/infrastructure/settingsStorage.js',
  '/js/infrastructure/storage.js',
  '/js/infrastructure/terminalAdapter.js',
  '/js/infrastructure/wsClient.js',
  '/js/infrastructure/terminal/cursorDebug.js',
  '/js/infrastructure/terminal/events.js',
  '/js/infrastructure/terminal/input.js',
  '/js/infrastructure/terminal/lifecycle.js',
  '/js/infrastructure/terminal/mouseMode.js',
  '/js/infrastructure/terminal/scale.js',
  '/js/infrastructure/terminal/scroll.js',
  '/js/infrastructure/terminal/shared.js',
  '/js/presentation/controllers/events.js',
  '/js/presentation/views/autohide.js',
  '/js/presentation/views/detail.js',
  '/js/presentation/views/fastscreen.js',
  '/js/presentation/views/sessionHandlers.js',
  '/js/presentation/views/settings.js',
  '/js/presentation/views/sizeSelector.js',
  '/js/presentation/views/ui.js',
  '/js/presentation/views/vnc.js',
  '/vendor/logpanel/index.js',
  '/vendor/logpanel/LogPanel.js',
  '/vendor/logpanel/styles.js',
  '/vendor/logpanel/defaultRules.js',
  '/vendor/logpanel/icons.js',
];

// 安装：预缓存所有静态资源
self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE_NAME);
      await cache.addAll(PRECACHE_URLS);
      // 跳过 waiting，立即激活
      self.skipWaiting();
    })()
  );
});

// 激活：清理旧缓存，接管页面
self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      // 删除旧版本缓存
      const keys = await caches.keys();
      await Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
      );
      // 立即控制所有页面
      self.clients.claim();
    })()
  );
});

// 判断是否为 API/WebSocket 请求（不应缓存）
function _isApiRequest(url) {
  const path = url.pathname;
  return (
    path.startsWith('/ws') ||
    path.startsWith('/api/') ||
    path.startsWith('/vnc/websockify') ||
    path.startsWith('/fs/')
  );
}

// 判断是否为静态资源
function _isStaticAsset(url) {
  const path = url.pathname;
  return (
    path.startsWith('/css/') ||
    path.startsWith('/js/') ||
    path.startsWith('/vendor/')
  );
}

// 请求拦截：缓存优先，网络回退
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // 处理字体 CDN 资源（跨域）
  if (url.href === 'https://fontsapi.zeoseven.com/442/main/result.css') {
    event.respondWith(
      (async () => {
        const cache = await caches.open(CACHE_NAME);
        const cached = await cache.match(event.request);
        if (cached) {
          // 后台异步更新缓存
          fetch(event.request).then((response) => {
            if (response && response.ok) {
              cache.put(event.request, response);
            }
          }).catch(() => {});
          return cached;
        }
        // 缓存未命中，从网络获取并缓存
        try {
          const response = await fetch(event.request);
          if (response && response.ok) {
            cache.put(event.request, response.clone());
          }
          return response;
        } catch (e) {
          // 离线且无缓存：返回 fallback
          return new Response('Offline', { status: 503 });
        }
      })()
    );
    return;
  }

  // 只处理同源请求
  if (url.origin !== location.origin) return;

  // API 请求直接走网络
  if (_isApiRequest(url)) return;

  // 静态资源：缓存优先（stale-while-revalidate）
  if (_isStaticAsset(url) || url.pathname === '/' || url.pathname === '/index.html' || url.pathname === '/login.html') {
    event.respondWith(
      (async () => {
        const cache = await caches.open(CACHE_NAME);
        const cached = await cache.match(event.request);
        if (cached) {
          // 后台异步更新缓存
          fetch(event.request).then((response) => {
            if (response && response.ok) {
              cache.put(event.request, response);
            }
          }).catch(() => {});
          return cached;
        }
        // 缓存未命中，从网络获取并缓存
        try {
          const response = await fetch(event.request);
          if (response && response.ok) {
            cache.put(event.request, response.clone());
          }
          return response;
        } catch (e) {
          // 离线且无缓存：返回 fallback
          return new Response('Offline', { status: 503 });
        }
      })()
    );
    return;
  }

  // 其他请求（如 noVNC 资源等）走网络优先
  event.respondWith(
    (async () => {
      try {
        return await fetch(event.request);
      } catch (e) {
        const cached = await caches.match(event.request);
        return cached || new Response('Offline', { status: 503 });
      }
    })()
  );
});