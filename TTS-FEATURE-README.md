# TTS音频生成功能说明

## 功能概述

在`/view/taskid`页面上新增了"生成TTS音频"按钮，可以根据SRT字幕内容生成人声音频文件。

## 功能特点

- 🎵 **智能TTS生成**: 使用EdgeTTS引擎生成高质量中文语音
- ⏰ **时间精确对齐**: 根据SRT时间轴精确生成音频片段
- 🔗 **自动音频连接**: 将多个音频片段按时间顺序连接成完整音频
- 🎛️ **实时进度显示**: 显示生成进度和状态
- 📱 **响应式界面**: 支持移动端和桌面端访问

## 使用方法

### 1. 访问字幕查看页面

访问 `http://localhost:9011/view/{task_id}` 页面，其中 `{task_id}` 是您的任务ID。

### 2. 点击生成TTS音频按钮

在页面右上角找到绿色的"生成TTS音频"按钮，点击开始生成。

### 3. 等待生成完成

- 按钮会显示"生成中..."状态
- 系统会在后台处理字幕内容
- 生成完成后会弹出新窗口显示结果

### 4. 查看和下载结果

在结果页面可以：
- 在线播放生成的音频
- 下载音频文件
- 查看生成统计信息

## 技术实现

### 前端实现

```javascript
// 按钮点击事件
async function onGenerateTTS() {
    // 1. 验证字幕数据
    // 2. 发送TTS生成请求
    // 3. 显示进度状态
    // 4. 跳转到结果页面
}
```

### 后端实现

#### API接口
- `POST /viewer_api/{task_id}/generate_tts` - 启动TTS生成任务
- `GET /tts_result/{task_id}` - 查看TTS生成结果

#### 核心功能
1. **字幕解析**: 解析SRT字幕文件，提取文本和时间信息
2. **TTS生成**: 使用EdgeTTS为每个字幕片段生成音频
3. **音频连接**: 使用FFmpeg将音频片段按时间顺序连接
4. **静音填充**: 在音频片段间添加适当的静音间隔

### 音频处理流程

```
字幕数据 → TTS引擎 → 音频片段 → 时间对齐 → 音频连接 → 最终音频
```

## 配置选项

### TTS引擎设置

默认使用EdgeTTS中文女声，可以在代码中修改：

```python
tts_item = {
    "role": "zh-CN-XiaoxiaoNeural",  # 可修改为其他音色
    "tts_type": 0,  # EdgeTTS
    "rate": "+0%",   # 语速
    "volume": "+0%", # 音量
    "pitch": "+0Hz"  # 音调
}
```

### 支持的音色

EdgeTTS支持多种中文音色：
- `zh-CN-XiaoxiaoNeural` - 晓晓（女声，推荐）
- `zh-CN-YunxiNeural` - 云希（男声）
- `zh-CN-YunyangNeural` - 云扬（男声）
- `zh-CN-XiaochenNeural` - 晓辰（女声）

## 文件结构

```
apidata/
├── {task_id}/                    # 原始任务目录
│   ├── video.mp4                 # 视频文件
│   └── subtitles.srt            # 字幕文件
└── tts_{task_id}_{timestamp}/    # TTS任务目录
    ├── tts_audio_{timestamp}.wav # 生成的音频文件
    └── result.json              # 任务结果信息
```

## 错误处理

### 常见错误及解决方案

1. **"没有字幕数据"**
   - 检查SRT文件是否存在
   - 确认字幕文件格式正确

2. **"TTS生成启动失败"**
   - 检查网络连接
   - 确认EdgeTTS服务可用

3. **"音频连接失败"**
   - 检查FFmpeg是否正确安装
   - 确认有足够的磁盘空间

### 日志查看

查看API日志了解详细错误信息：

```bash
# 查看实时日志
tail -f logs/$(date +%Y%m%d).log

# 查看特定任务日志
grep "tts_" logs/$(date +%Y%m%d).log
```

## 性能优化

### 处理大量字幕

- 字幕数量过多时，建议分批处理
- 可以调整并发处理数量
- 监控内存使用情况

### 音频质量

- 默认生成WAV格式，质量较高
- 可以修改为MP3格式减少文件大小
- 调整采样率和比特率

## 扩展功能

### 多语言支持

可以扩展支持其他语言：

```python
# 根据字幕语言自动选择TTS引擎
if language == "en":
    tts_item["role"] = "en-US-AriaNeural"
    tts_item["language"] = "en"
```

### 多角色配音

支持为不同说话人分配不同音色：

```python
# 根据说话人信息选择音色
speaker_roles = {
    "主持人": "zh-CN-YunxiNeural",
    "嘉宾": "zh-CN-XiaoxiaoNeural"
}
```

## 测试

运行测试脚本验证功能：

```bash
python3 test_tts_feature.py
```

## 注意事项

1. **网络要求**: EdgeTTS需要网络连接
2. **存储空间**: 确保有足够空间存储音频文件
3. **处理时间**: 大量字幕可能需要较长时间处理
4. **浏览器兼容**: 建议使用现代浏览器

## 更新日志

- **v1.0.0**: 初始版本，支持基础TTS生成功能
- 支持EdgeTTS中文语音合成
- 支持音频时间对齐和连接
- 提供Web界面和API接口
