# BJUT Auto Login (Headless)

北京工业大学校园网无 UI 自动认证工具，面向 Linux 服务器、NAS 和无桌面环境。无需 Tauri/Rust GUI，仅依赖 `python3`、`curl` 和 Linux `ip`。

认证协议参考并重新实现自 [`key-zhzr/BJUT-Auto-Login`](https://github.com/key-zhzr/BJUT-Auto-Login) 的现行 BJUT Portal 流程。

## 支持网络

- **Type 1**：宿舍网 `10.21.221.98` ePortal
- **Type 2**：`bjut_wifi` / `wlgn.bjut.edu.cn`
- **Type 3**：有线 `lgn.bjut.edu.cn` 新版加密 ePortal（`jsVersion=4.2.2`）
- **auto**：根据物理网卡和 Portal 响应自动判断

Type 3 会先从 `lgn6.bjut.edu.cn/drcom/getipv6` 获取网关观测到的 IPv6，再按当前 Portal 算法对登录参数执行 UTF-16 code unit XOR `0x16` + hex 编码，最后提交到 `https://lgn.bjut.edu.cn:802/eportal/portal/login`。

## 快速使用

```bash
git clone https://github.com/stainzhao/auto_login.git
cd auto_login
cp config.example.ini config.ini
chmod 600 config.ini
nano config.ini
python3 bjut_auth.py --config ./config.ini doctor
python3 bjut_auth.py --config ./config.ini login
```

常用命令：

```bash
python3 bjut_auth.py --config ./config.ini status
python3 bjut_auth.py --config ./config.ini detect
python3 bjut_auth.py --config ./config.ini ensure
python3 bjut_auth.py --config ./config.ini doctor
```

指定物理网卡或认证类型：

```bash
python3 bjut_auth.py --config ./config.ini --interface eno1 --type 3 login
```

> `-p/--password` 会让密码出现在当前 Python 进程命令行中，不建议在多用户服务器使用。优先使用权限为 `600` 的配置文件或 `BJUT_PASSWORD` 环境变量。

## systemd 自动认证

推荐直接通过 Bash 调用安装脚本；即使文件执行位因下载方式丢失也能正常安装：

```bash
sudo bash install.sh
sudo nano /etc/bjut-auto-login.conf
sudo bjut-auth --config /etc/bjut-auto-login.conf doctor
sudo bjut-auth --config /etc/bjut-auto-login.conf login
sudo systemctl enable --now bjut-auto-login.timer
```

默认每约 60 秒检查一次公网连通性；在线时跳过认证，离线时执行一次登录。

查看状态与日志：

```bash
systemctl status bjut-auto-login.timer
systemctl status bjut-auto-login.service
journalctl -u bjut-auto-login.service -n 50 --no-pager
```

更新：

```bash
cd auto_login
git pull
sudo bash install.sh
```

卸载（默认保留账号配置）：

```bash
sudo bash uninstall.sh
# 连配置一起删除：
sudo bash uninstall.sh --purge
```

## 配置

```ini
[BJUT]
username = 25000000
password = change_me
type = auto
interface =
allow_http_fallback = false
connectivity_url = https://www.baidu.com/
```

建议在服务器上明确填写校园网物理网卡，例如 `eno1` / `enp3s0`。脚本会拒绝 `wg*`、`tun*`、`tailscale*`、`docker*` 等明显虚拟接口，并使用 `curl --interface` 绑定认证流量，降低 WireGuard/TUN 抢路由的风险。

配置文件如果包含密码，请设置：

```bash
sudo chown root:root /etc/bjut-auto-login.conf
sudo chmod 600 /etc/bjut-auto-login.conf
```

脚本还会对权限过宽的密码配置给出警告；配置解析已关闭 `%` 插值，因此密码中含 `%` 不会被误解析。

## 安全说明

- Type 3 使用 HTTPS。
- Type 1/2 默认优先 HTTPS；只有明确设置 `allow_http_fallback = true` 才允许回退 HTTP。
- 带认证信息的完整 Portal URL 通过 `curl --config -` 从标准输入传给 curl，不放在 curl 的进程参数中。
- 不要把真实 `config.ini` 提交到 Git；仓库已通过 `.gitignore` 忽略。

## 故障排查

先运行：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf doctor
```

它会检查 Python/curl/ip、配置文件权限、物理网卡、IPv4、互联网状态和 Portal 类型。若仍失败，再查看：

```bash
ip route show table main default
ip -4 addr
journalctl -u bjut-auto-login.service -n 100 --no-pager
```

如果自动识别失败，可在配置中临时固定 `type = 1`、`2` 或 `3`，并明确填写 `interface`。

## 开发与测试

项目无第三方 Python 依赖：

```bash
python3 -m py_compile bjut_auth.py
python3 -m unittest discover -s tests -v
bash -n install.sh uninstall.sh
```

GitHub Actions 会在 Python 3.8 / 3.10 / 3.12 上运行上述测试。

## 致谢与许可

现行 BJUT Portal 端点、参数与认证流程参考 [`key-zhzr/BJUT-Auto-Login`](https://github.com/key-zhzr/BJUT-Auto-Login)，原项目采用 MIT License。相关许可声明见 [`THIRD_PARTY_NOTICES`](THIRD_PARTY_NOTICES)。

本项目同样以 MIT License 发布。
