# Demucs人声分离问题修复说明

## 问题描述

用户反馈生成的背景音乐文件仍然包含人声，说明Demucs人声分离没有正确工作。

## 问题分析

1. **文件路径不匹配**: 视频合成任务期望 `background_music.wav`，但Demucs生成的是 `background.wav`
2. **分离效果不佳**: Demucs可能没有正确分离人声和背景音乐
3. **调试信息不足**: 无法确定分离过程的具体问题

## 修复方案

### 1. 修复文件路径匹配问题

**修改前**:
```python
bgm_path = cache_dir / "background_music.wav"
success = separate_voice_background_demucs(str(audio_path), str(cache_dir))
if success and bgm_path.exists():  # 这里会失败，因为Demucs生成的是background.wav
```

**修改后**:
```python
bgm_path = cache_dir / "background_music.wav"
success = separate_voice_background_demucs(str(audio_path), str(cache_dir))

if success:
    # Demucs生成的文件名是background.wav和vocal.wav
    demucs_bgm_path = cache_dir / "background.wav"
    demucs_vocal_path = cache_dir / "vocal.wav"
    
    if demucs_bgm_path.exists() and demucs_vocal_path.exists():
        # 复制到我们期望的文件名
        shutil.copy2(demucs_bgm_path, bgm_path)
        shutil.copy2(demucs_vocal_path, vocal_path)
```

### 2. 增强Demucs分离函数

**主要改进**:
- 添加详细的调试信息
- 检查多种可能的输出目录结构
- 增加超时处理
- 显示文件大小信息
- 列出目录内容用于调试

**关键改进**:
```python
# 检查多种可能的输出结构
possible_output_dirs = [
    output_path / "htdemucs" / audio_name,
    output_path / "htdemucs",
    output_path / audio_name,
    output_path
]

# 详细的调试信息
print(f"Demucs返回码: {result.returncode}")
if result.stdout:
    print(f"Demucs输出: {result.stdout}")
if result.stderr:
    print(f"Demucs错误: {result.stderr}")
```

### 3. 添加测试工具

创建了 `test_demucs.py` 测试脚本，用于：
- 验证Demucs是否正确安装
- 测试人声分离功能
- 检查输出文件结构

## 使用方法

### 1. 测试Demucs安装

```bash
python3 test_demucs.py
```

### 2. 检查分离效果

运行视频合成任务后，检查以下文件：
- `background_music.wav` - 应该只包含背景音乐
- `original_vocal.wav` - 应该只包含人声

### 3. 调试信息

查看控制台输出，关注：
- Demucs命令执行结果
- 文件大小对比
- 错误信息

## 预期结果

### 正确的分离效果

1. **背景音乐文件** (`background_music.wav`):
   - 应该只包含背景音乐
   - 不应该有人声
   - 文件大小应该小于原音频

2. **人声文件** (`original_vocal.wav`):
   - 应该只包含人声
   - 不应该有背景音乐
   - 文件大小应该小于原音频

### 文件大小参考

- 原音频: 例如 5000 KB
- 背景音乐: 例如 3000 KB (60%左右)
- 人声: 例如 2000 KB (40%左右)

## 故障排除

### 1. Demucs未安装

**错误信息**: "无法找到Demucs，请安装: pip install demucs"

**解决方案**:
```bash
pip install demucs
```

### 2. 分离效果不佳

**可能原因**:
- 音频质量太差
- 人声和背景音乐混合度太高
- Demucs模型不适合该音频类型

**解决方案**:
- 尝试不同的音频文件
- 使用更高质量的原始音频
- 考虑使用其他分离工具

### 3. 输出文件未找到

**可能原因**:
- Demucs输出目录结构变化
- 权限问题
- 磁盘空间不足

**解决方案**:
- 检查控制台输出的目录内容
- 确认输出目录权限
- 检查磁盘空间

## 验证方法

### 1. 听觉验证

下载并播放分离后的文件：
- `background_music.wav` 应该只有背景音乐
- `original_vocal.wav` 应该只有人声

### 2. 文件大小验证

比较文件大小：
- 背景音乐 + 人声 ≈ 原音频大小
- 如果差异很大，可能分离有问题

### 3. 频谱分析

使用音频编辑软件查看频谱：
- 背景音乐应该主要包含低频和中频
- 人声应该主要包含中频和高频

## 进一步优化

### 1. 使用更好的分离模型

可以考虑使用其他分离工具：
- Spleeter (TensorFlow)
- LALAL.AI API
- 其他AI分离服务

### 2. 参数调优

调整Demucs参数：
```python
demucs_args = [
    *demucs_cmd,
    '--two-stems', 'vocals',
    '--model', 'htdemucs',  # 指定模型
    '--device', 'cuda',     # 使用GPU加速
    '--out', str(output_path),
    str(audio_path)
]
```

### 3. 后处理优化

对分离后的音频进行后处理：
- 降噪处理
- 音量平衡
- 频率补偿

通过这些修复，应该能够正确分离人声和背景音乐，生成高质量的合成视频。
