import os
import uuid
import time
import json
import hmac
import hashlib
import base64
import brotli
import requests


from dotenv import load_dotenv
load_dotenv()

# 配置信息（替换为您从控制台获取的实际信息）
VOLCANO_APP_ID = os.getenv("VOLCANO_APP_ID").strip()       # 火山引擎控制台「应用管理」获取AppID
VOLCANO_ACCESS_TOKEN = os.getenv("VOLCANO_ACCESS_TOKEN").strip() # 控制台获取Access Token
# V3不需要签名、Access Key/Secret Key，删掉了无用配置

# -------------------------- V3版本 TTS 请求封装 --------------------------
def tts_http_request(req: dict) -> dict:
    """替换原方法，适配V3版本接口，入参出参保持兼容"""
    # V3接口地址
    url = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
    method = "POST"

    # V3标准请求头 鉴权
    headers = {
        "Content-Type": "application/json",
        "X-Api-App-Id": VOLCANO_APP_ID,
        "X-Api-Access-Key": VOLCANO_ACCESS_TOKEN,
        "X-Api-Resource-Id": "seed-tts-2.0"  # 固定值，对应大模型语音合成服务
        
    }

    # 构造兼容原参数的请求体
    body = {
        "user": {
            "uid": "default"
        },
        "req_params": {
            "text": req.get("text", ""),
            "speaker": req.get("Voice_type", "saturn_zh_male_shuanglangshaonian_tob"),
            "audio_params": {
                "format": req.get("encoding", "mp3"),
                "sample_rate": req.get("sample_rate", 16000),
                "speech_rate": int((req.get("speed_ratio", 1.0) - 1) * 100),  # 语速转换为V3的[-50,100]格式
                "loudness_rate": int((req.get("volume_ratio", 1.0) - 1) * 100),  # 音量转换
                "pitch": int((req.get("pitch_ratio", 1.0) - 1) * 12)  # 音调转换
            }
        }
    }

    try:
        response = requests.post(url, headers=headers, json=body, stream=True, timeout=30)
        print(f"[TTS] HTTP 状态码: {response.status_code}")

        audio_data = b""
        # iter_lines 会自动处理压缩，返回解码后的文本行
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            print(f"[DEBUG] 行: {line[:200]}")  # 打印前200字符便于调试
            try:
                chunk = json.loads(line)
                code = chunk.get("code")
                if code == 0:
                    data_b64 = chunk.get("data")
                    if data_b64:
                        audio_data += base64.b64decode(data_b64)
                elif code == 20000000:
                    print("[TTS] 合成完成")
                    break
                else:
                    print(f"[TTS] 响应码 {code}: {chunk.get('message')}")
                    if code != 0:
                        raise Exception(f"API错误: {chunk.get('message')}")
            except json.JSONDecodeError as e:
                print(f"[TTS] JSON解析失败: {e}, 原始: {line[:100]}")
                continue

        if audio_data:
            return {"code": 0, "data": base64.b64encode(audio_data).decode("utf-8"), "message": "success"}
        else:
            return {"code": 55000000, "data": "", "message": "未收到音频数据，请检查 speaker 是否有效"}
    except Exception as e:
        return {"code": -1, "data": "", "message": str(e)}

"""
    # 发送请求，保持流式返回兼容原有逻辑
    response = requests.post(url, headers=headers, json=body, stream=True)
    print(f"[TTS] HTTP 状态码: {response.status_code}")
    print(f"[TTS] 响应头: {response.headers}")
    print(f"[TTS] 响应文本（前500字符）: {response.text[:500]}")
    
    # 返回格式和原方法保持一致，方便您原有业务处理
    if response.status_code == 200:
        # 收集音频数据
        audio_data = b""
        for chunk in response.iter_content(chunk_size=1024):
            if chunk:
                # 解析流式返回的JSON，提取音频数据
                try:
                    chunk_json = json.loads(chunk.decode("utf-8").strip())
                    if chunk_json.get("code") == 0 and chunk_json.get("data"):
                        audio_data += base64.b64decode(chunk_json["data"])
                    elif chunk_json.get("code") == 20000000:
                        # 合成结束标记
                        break
                except:
                    # 跳过非JSON格式的chunk
                    continue
        return {
            "code": 0,
            "data": base64.b64encode(audio_data).decode("utf-8"),
            "message": "success"
        }
    else:
        return {
            "code": response.status_code,
            "message": response.text
        }"""

# -------------------------- 你的原有业务逻辑几乎不变 --------------------------
def text_to_speech(text: str, output_dir: str = "./audio_cache") -> str:
    print(f"[TTS] 调用 text_to_speech，文本长度: {len(text)}")
    os.makedirs(output_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.mp3"
    filepath = os.path.join(output_dir, filename)

    # 仅把原来的TtsApi调用替换为上面的tts_http_request方法
    req = {
        "text": text,
        "Voice_type":  "saturn_zh_male_shuanglangshaonian_tob",
        "speed_ratio": 1.0,
        "volume_ratio": 1.0,
        "pitch_ratio": 1.0,
        "encoding": "mp3",
        "sample_rate": 16000,
    }
    resp = tts_http_request(req)
    # 打印完整响应，方便排查
    print(f"[TTS] 响应：code={resp.get('code')}, data长度={len(resp.get('data', ''))}")
    
    if resp.get("code") == 0 and resp.get("data"):
        audio_data = base64.b64decode(resp["data"])
        # 先打印音频数据长度，确认是否有效
        print(f"[TTS] 解码后音频长度：{len(audio_data)}字节")
        with open(filepath, "wb") as f:
            f.write(audio_data)
        # 验证文件是否生成成功
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            print(f"[TTS] 音频已保存：{filepath}，大小：{os.path.getsize(filepath)}字节")
            return filepath
        else:
            raise Exception(f"音频文件生成失败，路径：{filepath}")
    else:
        raise Exception(f"TTS failed: {resp}")
