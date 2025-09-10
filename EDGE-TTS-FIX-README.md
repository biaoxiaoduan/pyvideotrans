# Edge-TTS网络问题解决方案

## 问题描述

Edge-TTS出现"Invalid response status"错误，通常是由于网络连接问题导致的。

## 错误信息分析

```
[Edge-TTS]配音 [2/30] 第 2/3 次尝试失败: 500, message='Invalid response status', 
url='wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1?Ocp-Apim-Subscription-Key=...'
```

这个错误表明：
1. Edge-TTS无法连接到Microsoft的TTS服务
2. 可能是网络连接问题或代理设置问题
3. 服务可能暂时不可用

## 解决方案

### 方案1: 配置代理（推荐）

如果你使用代理，请编辑 `edgetts.txt` 文件：

```bash
# 编辑代理配置文件
nano edgetts.txt
```

添加你的代理信息：
```
127.0.0.1:7890
```

或者：
```
your-proxy-host:port
```

### 方案2: 检查网络连接

测试Edge-TTS服务连接：

```bash
# 测试网络连接
curl -I https://api.msedgeservices.com

# 测试Edge-TTS服务
python3 -c "
import asyncio
from edge_tts import Communicate

async def test():
    try:
        communicate = Communicate('测试', 'zh-CN-XiaoxiaoNeural')
        await communicate.save('test.mp3')
        print('Edge-TTS连接正常')
    except Exception as e:
        print(f'Edge-TTS连接失败: {e}')

asyncio.run(test())
"
```

### 方案3: 使用其他TTS引擎

如果Edge-TTS持续有问题，可以修改代码使用其他TTS引擎：

#### 3.1 使用Azure TTS

修改 `api.py` 中的TTS配置：

```python
tts_item = {
    "line": subtitle.get('line', i + 1),
    "text": subtitle['text'],
    "role": "zh-CN-XiaoxiaoNeural",
    "start_time": start_time,
    "end_time": end_time,
    "startraw": subtitle.get('startraw', ''),
    "endraw": subtitle.get('endraw', ''),
    "rate": "+0%",
    "volume": "+0%",
    "pitch": "+0Hz",
    "tts_type": 1,  # 改为Azure TTS
    "filename": config.TEMP_DIR + f"/dubbing_cache/{filename_md5}.wav"
}
```

#### 3.2 使用本地TTS

```python
tts_item = {
    # ... 其他配置 ...
    "tts_type": 2,  # 使用其他本地TTS引擎
    # ... 其他配置 ...
}
```

### 方案4: 增加重试和延时

修改Edge-TTS的重试配置：

```python
# 在 videotrans/tts/_edgetts.py 中
RETRY_NUMS = 5  # 增加重试次数
RETRY_DELAY = 10  # 增加重试延时
```

### 方案5: 使用备用TTS服务

创建一个备用的TTS生成函数：

```python
def generate_tts_with_fallback(text, voice, output_path):
    """使用备用TTS服务生成音频"""
    try:
        # 尝试Edge-TTS
        return generate_edge_tts(text, voice, output_path)
    except Exception as e:
        print(f"Edge-TTS失败: {e}")
        try:
            # 尝试Azure TTS
            return generate_azure_tts(text, voice, output_path)
        except Exception as e2:
            print(f"Azure TTS也失败: {e2}")
            # 使用本地TTS或返回错误
            return False
```

## 临时解决方案

### 1. 跳过TTS生成

如果TTS持续失败，可以暂时跳过TTS生成，直接使用原音频：

```python
# 在视频合成任务中
if not queue_tts:
    print("跳过TTS生成，使用原音频")
    # 直接使用原音频进行视频合成
    return
```

### 2. 使用预生成的音频

如果有预生成的TTS音频文件，可以直接使用：

```python
# 使用预生成的音频文件
pre_generated_audio = "path/to/pre_generated_audio.wav"
if Path(pre_generated_audio).exists():
    # 直接使用预生成的音频
    pass
```

## 网络环境检查

### 1. 检查防火墙设置

确保防火墙没有阻止Edge-TTS的连接：

```bash
# 检查防火墙状态
sudo ufw status

# 如果需要，允许HTTPS连接
sudo ufw allow out 443
```

### 2. 检查DNS设置

```bash
# 测试DNS解析
nslookup api.msedgeservices.com

# 如果DNS有问题，可以尝试使用公共DNS
echo "nameserver 8.8.8.8" | sudo tee /etc/resolv.conf
```

### 3. 检查代理设置

```bash
# 检查系统代理设置
echo $http_proxy
echo $https_proxy

# 如果需要，设置代理
export http_proxy=http://127.0.0.1:7890
export https_proxy=http://127.0.0.1:7890
```

## 测试脚本

创建一个测试脚本来诊断Edge-TTS问题：

```python
#!/usr/bin/env python3
import asyncio
import aiohttp
from edge_tts import Communicate

async def test_edge_tts():
    """测试Edge-TTS连接"""
    try:
        # 测试网络连接
        async with aiohttp.ClientSession() as session:
            async with session.get('https://api.msedgeservices.com') as response:
                print(f"网络连接状态: {response.status}")
        
        # 测试TTS生成
        communicate = Communicate('测试文本', 'zh-CN-XiaoxiaoNeural')
        await communicate.save('test_output.mp3')
        print("Edge-TTS测试成功")
        
    except Exception as e:
        print(f"Edge-TTS测试失败: {e}")

if __name__ == "__main__":
    asyncio.run(test_edge_tts())
```

## 长期解决方案

1. **使用本地TTS引擎**: 考虑部署本地TTS服务，避免网络依赖
2. **多TTS引擎支持**: 实现多个TTS引擎的自动切换
3. **缓存机制**: 缓存已生成的TTS音频，避免重复生成
4. **离线模式**: 提供完全离线的TTS解决方案

## 联系支持

如果问题持续存在，可以：
1. 检查Edge-TTS的官方文档和状态页面
2. 联系网络管理员检查网络配置
3. 考虑使用其他TTS服务提供商
