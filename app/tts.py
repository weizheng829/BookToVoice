"""直连 Edge-TTS 合成：整章文本一次调用，生成单个 mp3。

借鉴本地脚本的简洁做法——edge-tts 一次吃整章文本，无需分段拼接。
rate/volume/pitch 为 None 表示不调整。
"""
import asyncio
import logging
from pathlib import Path

log = logging.getLogger("tts")


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

    try:
        asyncio.run(_run())
    except Exception as e:  # noqa: BLE001
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise RuntimeError(f"edge-tts 合成失败: {e}") from e

    if not tmp.exists() or tmp.stat().st_size == 0:
        raise RuntimeError("edge-tts 返回空音频")
    tmp.replace(output_path)
    log.info("合成成功 %s (%d bytes)", output_path, output_path.stat().st_size)
    return output_path
