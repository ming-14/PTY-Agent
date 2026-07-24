"""
@file config.py
@brief 线程安全配置管理，JSON 持久化
"""

import threading
import os
import json

_CONFIG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'cursorlocator_config.json')


class Config:
    def __init__(self):
        self._lock = threading.Lock()
        self.outer_radius = 16
        self.inner_radius = 8
        self.alpha = 90
        self.last_pixel_color = (0, 0, 0)
        self.last_complement_color = (0, 0, 0)
        self.on_ring = False
        self.track_interval = 20
        self.sample_interval = 100
        self.force_refresh = False
        self.timer_mode = 'auto'
        self.timer_interval_custom = 16
        self.timer_multiplier = 1.0
        self._load()

    def _load(self):
        try:
            with open(_CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return
        with self._lock:
            tuple_keys = {'last_pixel_color', 'last_complement_color'}
            for key, value in data.items():
                if key == 'force_refresh':
                    continue
                if hasattr(self, key):
                    if key in tuple_keys and isinstance(value, list):
                        value = tuple(value)
                    setattr(self, key, value)

    def save(self):
        d = self.snapshot()
        for k in ('last_pixel_color', 'last_complement_color'):
            if isinstance(d.get(k), tuple):
                d[k] = list(d[k])
        try:
            with open(_CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def get(self, key):
        with self._lock:
            return getattr(self, key)

    def set(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                setattr(self, k, v)

    def snapshot(self):
        with self._lock:
            return {
                'outer_radius':           self.outer_radius,
                'inner_radius':           self.inner_radius,
                'alpha':                  self.alpha,
                'last_pixel_color':       self.last_pixel_color,
                'last_complement_color':  self.last_complement_color,
                'on_ring':                self.on_ring,
                'track_interval':         self.track_interval,
                'sample_interval':        self.sample_interval,
                'force_refresh':          self.force_refresh,
                'timer_mode':             self.timer_mode,
                'timer_interval_custom':  self.timer_interval_custom,
                'timer_multiplier':       self.timer_multiplier,
            }
