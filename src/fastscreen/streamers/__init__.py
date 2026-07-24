"""FastScreen 流媒体子包。

包含三种流格式的 streamer 与共享会话管理器：
- MJPEG    — fastscreen.streamers.mjpeg.MjpegStreamer
- H264     — fastscreen.streamers.h264.H264Streamer
- H264 MSE — fastscreen.streamers.h264_mse.H264MSEStreamer
- 管理器   — fastscreen.streamers.manager.StreamManager

注意：本 __init__.py 刻意不执行子模块导入。
原因：H264 编码依赖 PyAV（av>=12.0）与 numpy，若未安装会导致整个
streamers 包加载失败，连带 MJPEG（仅需 Pillow）也无法使用。
所有使用方直接从子模块导入，例如：
    from fastscreen.streamers.mjpeg import MjpegStreamer
    from fastscreen.streamers.manager import StreamManager
"""
