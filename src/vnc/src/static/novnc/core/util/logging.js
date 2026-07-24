/*
 * noVNC: HTML5 VNC client
 * Copyright (C) 2019 The noVNC authors
 * Licensed under MPL 2.0 (see LICENSE.txt)
 *
 * See README.md for usage and integration instructions.
 */

/*
 * Logging/debug routines
 */

let _logLevel = 'warn';

const LEVEL_ORDER = ['none', 'error', 'warn', 'info', 'debug'];

function _timestamp() {
    const d = new Date();
    const h = String(d.getHours()).padStart(2, '0');
    const m = String(d.getMinutes()).padStart(2, '0');
    const s = String(d.getSeconds()).padStart(2, '0');
    const ms = String(d.getMilliseconds()).padStart(3, '0');
    return `${h}:${m}:${s}.${ms}`;
}

function _makeLogFn(level, consoleFn) {
    return function (...args) {
        consoleFn(`[${_timestamp()}] [${level.toUpperCase()}]`, ...args);
    };
}

let Debug = () => {};
let Info = () => {};
let Warn = () => {};
let Error = () => {};

export function initLogging(level) {
    if (typeof level === 'undefined') {
        level = _logLevel;
    } else {
        _logLevel = level;
    }

    Debug = Info = Warn = Error = () => {};

    if (typeof window.console !== "undefined") {
        /* eslint-disable no-fallthrough */
        switch (level) {
            case 'debug':
                Debug = _makeLogFn('debug', console.debug.bind(window.console));
            case 'info':
                Info = _makeLogFn('info', console.info.bind(window.console));
            case 'warn':
                Warn = _makeLogFn('warn', console.warn.bind(window.console));
            case 'error':
                Error = _makeLogFn('error', console.error.bind(window.console));
            case 'none':
                break;
            default:
                throw new window.Error("invalid logging type '" + level + "'");
        }
        /* eslint-enable no-fallthrough */
    }
}

export function getLogging() {
    return _logLevel;
}

export function getLevelOrder() {
    return [...LEVEL_ORDER];
}

export { Debug, Info, Warn, Error };

initLogging();
