#!/bin/bash
# Altair API 余额查询服务 - 一键域名绑定与 Nginx 自动化配置脚本
# 用法：sudo ./setup_domain.sh

# 强制以 root 权限运行
if [ "$EUID" -ne 0 ]; then
    echo "❌ 请使用 sudo 运行此脚本！例如：sudo ./setup_domain.sh"
    exit 1
fi

echo "=================================================="
echo "   🌌 Altair API 域名绑定与 Nginx 一键自动化配置   "
echo "=================================================="
echo ""

# 1. 让用户输入域名
read -p "👉 请输入你要绑定的域名 (例如: api.yourdomain.com): " USER_DOMAIN

if [ -z "$USER_DOMAIN" ]; then
    echo "❌ 域名不能为空，程序退出。"
    exit 1
fi

# 2. 检测并安装 Nginx
echo "📦 正在检测并安装 Nginx..."
if command -v apt-get &> /dev/null; then
    # Ubuntu / Debian
    apt-get update -y
    apt-get install nginx -y
elif command -v yum &> /dev/null; then
    # CentOS / RHEL
    yum install epel-release -y
    yum install nginx -y
else
    echo "❌ 未能识别你的 Linux 发行版，请手动安装 Nginx。"
    exit 1
fi

# 3. 清理默认冲突配置
echo "🧹 正在清理 Nginx 默认冲突配置..."
if [ -f "/etc/nginx/sites-enabled/default" ]; then
    rm -f /etc/nginx/sites-enabled/default
fi
if [ -f "/etc/nginx/sites-available/default" ]; then
    echo "" > /etc/nginx/sites-available/default
fi

# 4. 写入专用反向代理配置
echo "✍️ 正在配置域名反向代理转发..."
cat << EOF > /etc/nginx/conf.d/altairapi.conf
server {
    listen 80;
    server_name $USER_DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:29180;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        # 避免连接超时
        proxy_connect_timeout 60s;
        proxy_read_timeout 60s;
    }
}
EOF

# 5. 放行 80 端口（系统防火墙）
echo "🛡️ 正在开放服务器 80 端口..."
if command -v ufw &> /dev/null; then
    ufw allow 80/tcp >/dev/null 2>&1
fi
if command -v iptables &> /dev/null; then
    iptables -I INPUT -p tcp --dport 80 -j ACCEPT >/dev/null 2>&1
fi

# 6. 验证并重启 Nginx
echo "🔄 正在启动并重启 Nginx 服务..."
nginx -t
if [ $? -eq 0 ]; then
    systemctl restart nginx
    systemctl enable nginx
    echo ""
    echo "=================================================="
    echo "🎉【第一阶段：服务器端配置】已圆满成功！"
    echo "=================================================="
    echo "域名: http://$USER_DOMAIN"
    echo "程序已在后台监听: 29180 端口，并通过 Nginx (80端口) 转发"
    echo ""
    echo "⚠️【第二阶段：去雨云和 Cloudflare 做最后设置】"
    echo "1. 登录 [雨云控制台] ➔ [防火墙/安全组] ➔ 开放 80 端口"
    echo "   (注意：不需要在雨云放行 29180 端口了！只开 80 即可，更安全)"
    echo ""
    echo "2. 登录 [Cloudflare 控制台] ➔ 解析你的域名:"
    echo "   - 类型 (Type): A"
    echo "   - 名称 (Name): @ (或者填二级域名如 api)"
    echo "   - IP 地址 (IPv4): 填写你服务器的真实 IP"
    echo "   - ⚠️ 代理状态: 必须点亮「黄色小云朵」(已代理/Proxied)"
    echo ""
    echo "3. 在 Cloudflare [SSL/TLS] 菜单下:"
    echo "   - 将加密模式设置为「Flexible」(灵活模式) ➔ 自动获得 HTTPS 绿色安全锁！"
    echo ""
    echo "配置完成后，即可直接通过 https://$USER_DOMAIN 访问你的网站！"
    echo "=================================================="
else
    echo "❌ Nginx 配置验证失败，请检查报错日志。"
fi
