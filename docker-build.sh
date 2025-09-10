#!/bin/bash

# Docker构建脚本 - 支持多种镜像源

echo "=== 视频翻译配音工具 Docker 构建脚本 ==="

# 检查Docker是否安装
if ! command -v docker &> /dev/null; then
    echo "错误: Docker 未安装，请先安装 Docker"
    exit 1
fi

# 默认参数
IMAGE_NAME="biaovideotrans-api"
TAG="latest"
DOCKERFILE="Dockerfile"

# 解析命令行参数
while [[ $# -gt 0 ]]; do
    case $1 in
        -n|--name)
            IMAGE_NAME="$2"
            shift 2
            ;;
        -t|--tag)
            TAG="$2"
            shift 2
            ;;
        -f|--file)
            DOCKERFILE="$2"
            shift 2
            ;;
        --cn)
            DOCKERFILE="Dockerfile.cn"
            shift
            ;;
        --test)
            DOCKERFILE="Dockerfile.test"
            shift
            ;;
        -h|--help)
            echo "用法: $0 [选项]"
            echo "选项:"
            echo "  -n, --name NAME     镜像名称 (默认: biaovideotrans-api)"
            echo "  -t, --tag TAG       镜像标签 (默认: latest)"
            echo "  -f, --file FILE     Dockerfile文件 (默认: Dockerfile)"
            echo "  --cn                使用国内镜像源 (Dockerfile.cn)"
            echo "  --test              使用测试版本 (Dockerfile.test)"
            echo "  -h, --help          显示帮助信息"
            exit 0
            ;;
        *)
            echo "未知参数: $1"
            echo "使用 -h 或 --help 查看帮助"
            exit 1
            ;;
    esac
done

echo "构建配置:"
echo "  镜像名称: $IMAGE_NAME"
echo "  镜像标签: $TAG"
echo "  Dockerfile: $DOCKERFILE"
echo ""

# 检查Dockerfile是否存在
if [ ! -f "$DOCKERFILE" ]; then
    echo "错误: Dockerfile '$DOCKERFILE' 不存在"
    exit 1
fi

# 构建镜像
echo "开始构建Docker镜像..."
echo "命令: docker build -t $IMAGE_NAME:$TAG -f $DOCKERFILE ."
echo ""

if docker build -t "$IMAGE_NAME:$TAG" -f "$DOCKERFILE" .; then
    echo ""
    echo "✅ 镜像构建成功！"
    echo "镜像名称: $IMAGE_NAME:$TAG"
    echo ""
    echo "运行容器:"
    echo "  docker run -d -p 9011:9011 --name biaovideotrans-api $IMAGE_NAME:$TAG"
    echo ""
    echo "或者使用docker-compose:"
    echo "  docker-compose up -d"
else
    echo ""
    echo "❌ 镜像构建失败！"
    echo ""
    echo "可能的解决方案:"
    echo "1. 检查网络连接"
    echo "2. 尝试使用国内镜像源: $0 --cn"
    echo "3. 尝试使用测试版本: $0 --test"
    echo "4. 检查Dockerfile语法"
    exit 1
fi
