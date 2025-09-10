# 视频翻译配音工具 Docker 部署指南

## 快速开始

### 方法一：使用启动脚本（推荐）

```bash
# 给脚本执行权限
chmod +x docker-run.sh

# 运行启动脚本
./docker-run.sh
```

### 方法二：使用 docker-compose

```bash
# 创建数据目录
mkdir -p data logs models tmp

# 构建并启动服务
docker-compose up -d
```

### 方法三：使用 Docker 命令

```bash
# 构建镜像
docker build -t biaovideotrans-api .

# 运行容器
docker run -d \
  --name biaovideotrans-api \
  -p 9011:9011 \
  -v $(pwd)/data:/app/apidata \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/models:/app/models \
  -v $(pwd)/tmp:/app/tmp \
  biaovideotrans-api
```

## 访问服务

- **API地址**: http://localhost:9011
- **文档地址**: https://pyvideotrans.com/api-cn
- **Web界面**: http://localhost:9011/viewer

## 目录说明

- `data/` - 存储API生成的文件和任务数据
- `logs/` - 存储应用日志
- `models/` - 存储AI模型文件
- `tmp/` - 临时文件目录

## 常用命令

```bash
# 查看服务状态
docker-compose ps

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down

# 重启服务
docker-compose restart

# 更新镜像
docker-compose pull
docker-compose up -d
```

## 配置说明

### 环境变量

可以通过环境变量配置服务：

```yaml
environment:
  - PYTHONUNBUFFERED=1
  - TZ=Asia/Shanghai
  - HOST=0.0.0.0  # 监听所有接口
  - PORT=9011     # 端口号
```

### 端口配置

默认端口是9011，可以通过修改docker-compose.yml中的端口映射来更改：

```yaml
ports:
  - "8080:9011"  # 将外部端口改为8080
```

### GPU支持

如果需要GPU加速，取消docker-compose.yml中的GPU配置注释：

```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: 1
          capabilities: [gpu]
```

## 故障排除

### 1. 服务无法启动

```bash
# 查看详细日志
docker-compose logs biaovideotrans-api

# 检查端口是否被占用
netstat -tlnp | grep 9011
```

### 2. 权限问题

```bash
# 设置目录权限
chmod 755 data logs models tmp
```

### 3. 内存不足

如果遇到内存不足的问题，可以：

1. 增加Docker的内存限制
2. 使用更小的模型
3. 调整并发处理数量

### 4. 模型下载

首次运行可能需要下载AI模型，这可能需要一些时间。模型会存储在`models/`目录中。

## 生产环境部署建议

1. **使用反向代理**: 建议使用Nginx作为反向代理
2. **数据备份**: 定期备份`data/`目录
3. **监控**: 设置日志监控和健康检查
4. **资源限制**: 设置适当的内存和CPU限制

## 更新服务

```bash
# 停止服务
docker-compose down

# 拉取最新代码
git pull

# 重新构建并启动
docker-compose up -d --build
```

## 支持

如果遇到问题，请查看：
- [项目文档](https://pyvideotrans.com/api-cn)
- [GitHub Issues](https://github.com/jianchang512/pyvideotrans/issues)
