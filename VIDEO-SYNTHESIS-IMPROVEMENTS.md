# 视频合成功能改进说明

## 问题分析

用户反馈音频混合后文件只有600多KB，说明音频混合过程可能存在问题。我们进行了以下改进：

## 主要改进

### 1. 单独保存背景音乐文件
- **改进前**: 直接使用Demucs分离后的文件路径
- **改进后**: 明确保存背景音乐文件到 `background_music.wav`
- **好处**: 便于调试和验证分离效果

### 2. 增强音频混合逻辑
- **改进前**: 简单的amix混合，可能导致音频质量问题
- **改进后**: 
  - 背景音乐音量降低到30% (`volume=0.3`)
  - TTS音频保持100%音量 (`volume=1.0`)
  - 使用PCM格式确保音频质量
  - 添加详细的文件大小检查

### 3. 详细的调试信息
- **文件大小监控**: 每个步骤都显示文件大小
- **FFmpeg命令输出**: 显示完整的FFmpeg命令和错误信息
- **中间文件保存**: 保存所有中间处理文件到任务目录

### 4. 中间文件保存
现在会保存以下中间文件到任务目录：
- `background_music.wav` - 背景音乐文件
- `tts_audio.wav` - TTS生成的音频
- `mixed_audio.wav` - 混合后的音频
- `synthesized_video_*.mp4` - 最终合成视频

## 技术细节

### 音频混合参数
```bash
ffmpeg -i background.wav -i tts.wav \
  -filter_complex '[0:a]volume=0.3[bgm];[1:a]volume=1.0[tts];[bgm][tts]amix=inputs=2:duration=longest:dropout_transition=0[mixed]' \
  -map '[mixed]' -c:a pcm_s16le -ar 44100 output.wav
```

### 视频合成参数
```bash
ffmpeg -i video.mp4 -i mixed_audio.wav \
  -c:v copy -c:a aac -b:a 128k \
  -map 0:v:0 -map 1:a:0 -shortest output.mp4
```

## 调试功能

### 1. 文件大小检查
每个处理步骤都会显示文件大小，帮助识别问题：
```
背景音乐文件大小: 2048.5 KB
TTS音频文件大小: 1024.3 KB
混合音频文件大小: 3072.8 KB
```

### 2. FFmpeg错误捕获
详细的错误信息帮助定位问题：
```
FFmpeg执行失败: Command 'ffmpeg ...' returned non-zero exit status 1
错误输出: [ffmpeg stderr output]
```

### 3. 中间文件访问
所有中间文件都保存在任务目录中，可以直接下载和检查：
- 访问 `/apidata/{task_id}/background_music.wav`
- 访问 `/apidata/{task_id}/tts_audio.wav`
- 访问 `/apidata/{task_id}/mixed_audio.wav`

## 使用方法

### 1. 启动视频合成
在字幕查看页面点击"合成视频"按钮

### 2. 监控处理过程
查看控制台输出，关注文件大小变化

### 3. 检查中间文件
如果最终结果有问题，可以下载中间文件进行分析

### 4. 调试问题
- 如果背景音乐文件很小：Demucs分离可能失败
- 如果TTS音频文件很小：TTS生成可能有问题
- 如果混合音频文件很小：音频混合过程可能失败

## 测试脚本

使用 `test_video_synthesis.py` 脚本进行自动化测试：

```bash
python3 test_video_synthesis.py
```

## 常见问题解决

### 1. 音频文件过小
- 检查原始视频是否有音频轨道
- 确认TTS生成是否成功
- 验证FFmpeg是否正确安装

### 2. 混合音频质量差
- 调整音量比例（当前背景音乐30%，TTS 100%）
- 检查音频采样率是否一致
- 确认音频格式兼容性

### 3. 视频合成失败
- 检查视频和音频时长是否匹配
- 确认输出目录有写入权限
- 验证FFmpeg版本是否支持使用的参数

## 性能优化建议

1. **批量处理**: 对于大量字幕，考虑分批处理
2. **缓存机制**: 重复使用已生成的TTS音频
3. **并行处理**: 可以并行处理多个视频合成任务
4. **存储管理**: 定期清理临时文件

## 监控指标

- 背景音乐文件大小（应该与原音频相近）
- TTS音频文件大小（根据字幕数量和时长）
- 混合音频文件大小（应该大于TTS音频）
- 最终视频文件大小（应该包含完整视频内容）

通过这些改进，我们可以更好地诊断和解决音频混合问题，确保生成高质量的合成视频。
