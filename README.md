# Altair-api-TokenTrace

一键查询多种 AI 大模型 API 的余额与用量。

## 支持的服务商

| 服务商 | Base URL 示例 | 自动识别 |
|---|---|---|
| OpenAI | api.openai.com | ✅ |
| DeepSeek | api.deepseek.com | ✅ |
| Anthropic Claude | api.anthropic.com | ✅ |
| 硅基流动 SiliconFlow | api.siliconflow.cn | ✅ |
| OpenRouter | openrouter.ai | ✅ |
| NewAPI/OneAPI 中转站 | 自定义 | 自动探测 |

## 快速部署

### 1. 克隆代码到服务器

登录你的 Linux 服务器终端，直接运行 `git clone` 一键拉取项目：

```bash
cd /opt
git clone https://github.com/altair-9527/Altair-api-TokenTrace.git
```

> 💡 如果你的仓库是 Private (私有) 的，克隆时会提示输入你的 GitHub 用户名和 Token (或密码) 进行验证。

### 2. 前台启动（调试用）

```bash
cd /opt/Altair-api-TokenTrace
chmod +x start.sh
./start.sh
```

### 3. 后台启动（生产推荐）

```bash
cd /opt/Altair-api-TokenTrace
chmod +x run_bg.sh
./run_bg.sh          # 启动
./run_bg.sh status   # 查看状态
./run_bg.sh stop     # 停止
```

### 4. 访问

浏览器打开 `http://<服务器IP>:29180` 即可使用。

## 外网访问配置

如果服务器有防火墙，需要开放 29180 端口：

```bash
# Ubuntu/Debian
sudo ufw allow 29180

# CentOS/RHEL
sudo firewall-cmd --permanent --add-port=29180/tcp
sudo firewall-cmd --reload
```

如果使用云服务器（阿里云/腾讯云/AWS），还需在安全组中放行 29180 端口。

## 自定义端口

修改启动脚本中的 `--port 29180` 为你想要的端口即可。

## 目录结构

```
Altair-api-TokenTrace/
├── start.sh          # 前台启动脚本
├── run_bg.sh         # 后台启动/停止脚本
├── requirements.txt  # Python 依赖
├── static/           # 前端构建产物
│   ├── index.html
│   └── assets/
└── app/              # 后端 Python
    ├── __init__.py
    ├── main.py       # FastAPI 入口（含前端托管）
    ├── models.py     # 数据模型
    └── adapters.py   # 各服务商适配器
```
