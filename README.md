# BJUT Auto Login (Headless)

北京工业大学校园网无 UI 自动认证工具，面向 Linux 服务器、NAS 和无桌面环境。

认证协议参考并重新实现自 [`key-zhzr/BJUT-Auto-Login`](https://github.com/key-zhzr/BJUT-Auto-Login) 的现行 BJUT Portal 流程；本项目不依赖 Tauri/Rust GUI，仅依赖 `python3`、`curl` 和 Linux `ip` 命令。

## 支持网络

- Type 1：宿舍网 `10.21.221.98` ePortal
- Type 2：`bjut_wifi` / `wlgn.bjut.edu.cn`
- Type 3：有线 `lgn.bjut.edu.cn` 新版加密 ePortal（`jsVersion=4.2.2`）
- `auto`：根据物理网卡类型和网关响应自动判断

Type 3 登录会先从 `lgn6.bjut.edu.cn/drcom/getipv6` 获取网关观测到的 IPv6，再对登录参数按当前 Portal 算法进行 UTF-16 code unit XOR `0x16` 后十六进制编码，最后提交至 `https://lgn.bjut.edu.cn:802/eportal/portal/login`。

## 快速使用

```bash
git clone https://github.com/stainzhao/auto_login.git
cd auto_login
cp config.example.ini config.ini
chmod 600 config.ini
nano config.ini
python3 bjut_auth.py --config ./config.ini login
```

查看状态和检测认证类型：

```bash
python3 bjut_auth.py --config ./config.ini status
python3 bjut_auth.py --config ./config.ini detect
```

指定物理网卡或认证类型：

```bash
python3 bjut_auth.py --config ./config.ini --interface eno1 --type 3 login
```

脚本会拒绝明显的 `wg*`、`tun*`、`tailscale*` 等虚拟接口，认证请求由 `curl --interface` 绑定到物理网卡，降低 WireGuard/TUN 抢走校园网认证流量的风险。

## systemd 自动认证

```bash
sudo ./install.sh
sudo nano /etc/bjut-auto-login.conf
sudo bjut-auth --config /etc/bjut-auto-login.conf login
sudo systemctl enable --now bjut-auto-login.timer
```

默认每 60 秒检查一次公网连通性；已经在线时不会重复认证，离线时才执行一次登录。

查看日志：

```bash
systemctl status bjut-auto-login.timer
journalctl -u bjut-auto-login.service -n 50 --no-pager
```

## HTTP 回退说明

默认不会通过 HTTP 明文发送校园网密码。部分宿舍认证入口实测仍可能只接受 `http://10.21.221.98:801`；确认当前网络可信后，可以在配置中设置：

```ini
allow_http_fallback = true
```

## 配置文件安全

账号密码用于无人值守认证时需要保存在本机配置文件中。建议：

```bash
sudo chown root:root /etc/bjut-auto-login.conf
sudo chmod 600 /etc/bjut-auto-login.conf
```

不要把真实账号密码提交到 Git。

## 致谢与许可

现行 BJUT Portal 端点、参数与认证流程参考 [`key-zhzr/BJUT-Auto-Login`](https://github.com/key-zhzr/BJUT-Auto-Login)，原项目采用 MIT License。相关许可声明见 [`THIRD_PARTY_NOTICES`](THIRD_PARTY_NOTICES)。

本项目同样以 MIT License 发布。
