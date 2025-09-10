#!/usr/bin/env python3
"""
ElevenLabs API密钥配置脚本
用于配置pyvideotrans项目的ElevenLabs API密钥
"""

import json
import os
from pathlib import Path

def configure_elevenlabs_key():
    """配置ElevenLabs API密钥"""
    
    # 获取项目根目录
    project_root = Path(__file__).parent
    params_file = project_root / "videotrans" / "params.json"
    
    print("🔑 ElevenLabs API密钥配置工具")
    print("=" * 50)
    
    # 检查params.json文件是否存在
    if not params_file.exists():
        print("❌ 错误：找不到params.json文件")
        print(f"   预期路径：{params_file}")
        return False
    
    # 读取当前配置
    try:
        with open(params_file, 'r', encoding='utf-8') as f:
            params = json.load(f)
    except Exception as e:
        print(f"❌ 错误：无法读取配置文件 - {e}")
        return False
    
    # 显示当前配置状态
    current_key = params.get('elevenlabstts_key', '')
    if current_key:
        print(f"✅ 当前已配置API密钥：{current_key[:8]}...{current_key[-4:]}")
    else:
        print("❌ 当前未配置API密钥")
    
    print("\n📋 获取ElevenLabs API密钥的步骤：")
    print("1. 访问 https://elevenlabs.io/app/home")
    print("2. 登录你的账户")
    print("3. 点击左下角的个人头像")
    print("4. 选择 'API Keys'")
    print("5. 点击 'Create API Key'")
    print("6. 复制生成的API密钥")
    
    # 获取用户输入
    print("\n" + "=" * 50)
    new_key = input("请输入你的ElevenLabs API密钥（或按Enter跳过）: ").strip()
    
    if not new_key:
        print("⏭️  跳过配置")
        return True
    
    # 验证API密钥格式（基本检查）
    if len(new_key) < 20:
        print("⚠️  警告：API密钥似乎太短，请确认是否正确")
        confirm = input("是否继续？(y/N): ").strip().lower()
        if confirm != 'y':
            print("❌ 配置已取消")
            return False
    
    # 更新配置
    params['elevenlabstts_key'] = new_key
    
    # 保存配置
    try:
        with open(params_file, 'w', encoding='utf-8') as f:
            json.dump(params, f, ensure_ascii=False, indent=4)
        print("✅ API密钥配置成功！")
        print(f"   已保存到：{params_file}")
        return True
    except Exception as e:
        print(f"❌ 错误：无法保存配置 - {e}")
        return False

def test_elevenlabs_connection():
    """测试ElevenLabs连接"""
    print("\n🧪 测试ElevenLabs连接...")
    
    try:
        from elevenlabs import ElevenLabs
        
        # 读取配置
        project_root = Path(__file__).parent
        params_file = project_root / "videotrans" / "params.json"
        
        with open(params_file, 'r', encoding='utf-8') as f:
            params = json.load(f)
        
        api_key = params.get('elevenlabstts_key', '')
        if not api_key:
            print("❌ 未找到API密钥")
            return False
        
        # 创建客户端
        client = ElevenLabs(api_key=api_key)
        
        # 测试连接（获取用户信息）
        user = client.user.get()
        print(f"✅ 连接成功！")
        print(f"   用户：{user.first_name} {user.last_name}")
        print(f"   订阅：{user.subscription.tier}")
        print(f"   字符限制：{user.subscription.character_limit}")
        print(f"   已用字符：{user.subscription.character_count}")
        
        return True
        
    except Exception as e:
        print(f"❌ 连接测试失败：{e}")
        return False

if __name__ == "__main__":
    print("🚀 启动ElevenLabs配置工具...\n")
    
    # 配置API密钥
    if configure_elevenlabs_key():
        # 测试连接
        test_elevenlabs_connection()
    
    print("\n✨ 配置完成！")
    print("现在你可以使用语音克隆功能了。")
