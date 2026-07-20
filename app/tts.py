"""直连 Edge-TTS 合成：整章文本一次调用，生成单个 mp3。

借鉴本地脚本的简洁做法——edge-tts 一次吃整章文本，无需分段拼接。
rate/volume/pitch 为 None 表示不调整。
"""
import asyncio
import logging
import os
from pathlib import Path

log = logging.getLogger("tts")

# 单次合成超时（秒）。edge-tts 的 WebSocket 在长时间高频请求下偶发「挂死」——
# 既不返回数据、也不报错、也不关闭，comm.save() 会无限期 await。没有超时的话，
# 卡死的 worker 线程永远不会释放，多次发生会耗尽整个 worker 池，表现为
# 「生成卡住、点重生只有 303 无后续」。加超时后抛错 → 走 worker 的退避重试兜底。
#
# 超时按文本长度自适应：基础超时 + 每千字额外宽限。短章节挂死能尽快脱困，
# 长章节（网文动辄数千字）又不会被误判超时连续失败。
_TTS_TIMEOUT = float(os.getenv("TTS_TIMEOUT", "120"))           # 基础超时（秒）
_TTS_TIMEOUT_PER_1K = float(os.getenv("TTS_TIMEOUT_PER_1K", "60"))  # 每千字额外宽限（秒）


def _norm_percent(v):
    """规范化为 edge-tts 要求的 `[+-]\\d+%` 格式；空/0 → '+0%'（不调整）。

    新版 edge-tts 的 validate_string_param 不接受 None，必须是合法字符串。
    """
    if v in (None, "", "0", "0%", "+0%"):
        return "+0%"
    return v


def _norm_pitch(v):
    """规范化为 edge-tts 要求的 `[+-]\\d+Hz` 格式；空/0 → '+0Hz'（不调整）。"""
    if v in (None, "", "0", "0Hz", "+0Hz"):
        return "+0Hz"
    return v


def synthesize(text: str, output_path: Path, voice: str,
               rate=None, volume=None, pitch=None) -> Path:
    """合成整章文本到 output_path；成功返回路径，失败抛 RuntimeError。"""
    import edge_tts  # 延迟导入

    if not text or not text.strip():
        raise RuntimeError("无有效文本")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(output_path.suffix + ".part")
    rate = _norm_percent(rate)
    volume = _norm_percent(volume)
    pitch = _norm_pitch(pitch)

    async def _run():
        comm = edge_tts.Communicate(text, voice, rate=rate, volume=volume, pitch=pitch)
        await comm.save(str(tmp))

    # 按文本长度自适应超时：120s 基础 + 每千字 60s 宽限。
    timeout = _TTS_TIMEOUT + len(text) / 1000 * _TTS_TIMEOUT_PER_1K
    try:
        asyncio.run(asyncio.wait_for(_run(), timeout=timeout))
    except Exception as e:  # noqa: BLE001
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise RuntimeError(f"edge-tts 合成失败: {e}") from e

    if not tmp.exists() or tmp.stat().st_size == 0:
        raise RuntimeError("edge-tts 返回空音频")
    tmp.replace(output_path)
    log.info("合成成功 %s (%d bytes)", output_path, output_path.stat().st_size)
    return output_path
