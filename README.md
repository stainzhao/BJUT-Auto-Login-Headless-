# BJUT Auto Login (Headless)

北京工业大学校园网无 UI 自动认证工具，适用于 Linux 服务器、工作站、NAS 和其他无桌面环境。

本项目将现行 BJUT Portal 登录流程实现为轻量 CLI，不依赖 Tauri/Rust GUI。认证请求会绑定到校园网物理网卡，尽量避免 WireGuard、Tailscale、TUN/TAP、Docker 等虚拟网络抢走认证流量。

> 当前版本：`0.2.0`
>
> 已在 BJUT 有线 Type 3 环境完成真实验证：认证失效后，systemd timer 检测到离线并自动重新认证，随后公网连接恢复。

## 支持功能

- **Type 1**：宿舍网 `10.21.221.98` ePortal
- **Type 2**：`bjut_wifi` / `wlgn.bjut.edu.cn`
- **Type 3**：有线 `lgn.bjut.edu.cn` 新版加密 ePortal
- **auto**：自动检测认证类型
- 自动识别物理网卡
- `curl --interface` 绑定认证请求到物理接口
- 拒绝明显的 `wg*`、`tun*`、`tailscale*`、`docker*` 等虚拟接口
- `doctor` 环境和 Portal 自检
- `status` 公网状态检查
- `ensure`：在线跳过，离线自动登录
- systemd timer 周期巡检和掉线恢复
- GitHub Actions 单元测试

## 依赖

```text
Linux
Python >= 3.8
curl
iproute2
systemd   # 仅自动重连需要
```

Ubuntu / Debian：

```bash
sudo apt update
sudo apt install -y python3 curl iproute2
```

---

# 人工快速部署

## 1. 克隆

```bash
git clone https://github.com/stainzhao/BJUT-Auto-Login-Headless-.git
cd BJUT-Auto-Login-Headless-
```

## 2. 安装

```bash
sudo ./install.sh
```

安装后主要文件：

```text
/usr/local/bin/bjut-auth
/etc/bjut-auto-login.conf
/etc/systemd/system/bjut-auto-login.service
/etc/systemd/system/bjut-auto-login.timer
```

`install.sh` 可重复运行用于升级；如果 `/etc/bjut-auto-login.conf` 已存在，不会主动覆盖已有账号配置。

## 3. 配置账号

```bash
sudo nano /etc/bjut-auto-login.conf
```

示例：

```ini
[BJUT]
username = 25000000
password = change_me

# auto / 1 / 2 / 3
# 推荐保持 auto
type = auto

# 留空时自动寻找物理网卡
# 也可以显式填写 eno1 / enp7s0 / wlan0 等
interface =

# 默认 false
# 只有明确需要且确认当前校园网可信时才开启
allow_http_fallback = false

connectivity_url = https://www.baidu.com/
```

设置权限：

```bash
sudo chown root:root /etc/bjut-auto-login.conf
sudo chmod 600 /etc/bjut-auto-login.conf
```

不要把真实账号密码提交到 Git。

## 4. 自检

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf doctor
```

正常示例：

```text
BJUT Auto Login 0.2.0
python: 3.12.3
curl: OK (/usr/bin/curl)
ip: OK (/usr/sbin/ip)
config: /etc/bjut-auto-login.conf
interface: enp7s0 (wired)
ipv4: 172.x.x.x
internet: online
portal: type 3
```

重点确认：

```text
curl: OK
ip: OK
interface: <物理接口>
ipv4: <校园网 IPv4>
portal: type 1 / 2 / 3
```

`internet` 在未认证时可以是 `offline`，这本身不代表程序异常。

## 5. 验证认证

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf ensure
```

已经在线时：

```text
online: interface=enp7s0, skip login
```

未认证时，成功结果类似：

```text
type=3 interface=enp7s0: Portal协议认证成功！
```

然后执行：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf status
```

成功应输出：

```text
online
```

## 6. 启用自动重连

```bash
sudo systemctl enable --now bjut-auto-login.timer
```

检查：

```bash
systemctl status bjut-auto-login.timer
```

正常状态包含：

```text
Loaded: loaded (...; enabled; ...)
Active: active (waiting)
Triggers: bjut-auto-login.service
```

查看日志：

```bash
journalctl -u bjut-auto-login.service -n 50 --no-pager
```

实时日志：

```bash
journalctl -fu bjut-auto-login.service
```

---

# AI Agent / Codex 自动部署协议

本节用于让 Agent 在新 Linux 主机上自动部署本项目。

如果你是负责部署本项目的 Agent，**严格按以下顺序执行，不要自行猜测网卡、IP、Portal 类型，也不要为了测试而主动切断远程服务器网络。**

## Agent 前置条件

部署前确认：

1. 主机是 Linux。
2. 有 root 或 `sudo` 权限。
3. 用户已经明确提供 BJUT 用户名和密码，或凭据已安全存在目标机。
4. 不得把真实密码写入 Git、PR、Issue、日志或最终汇报。
5. 如果 SSH/远程桌面依赖当前校园网连接，不得执行 `ip link set ... down`、修改 MAC、删除路由等可能中断远程访问的测试。

## Step A：获取代码

新部署：

```bash
git clone https://github.com/stainzhao/BJUT-Auto-Login-Headless-.git
cd BJUT-Auto-Login-Headless-
```

已有部署：

```bash
cd BJUT-Auto-Login-Headless-
git pull --ff-only
```

如果本地目录仍叫旧名称，例如 `auto_login`，目录名不影响程序运行：

```bash
cd auto_login
git pull --ff-only
```

## Step B：检查并安装依赖

```bash
command -v python3
command -v curl
command -v ip
```

Ubuntu / Debian 缺少依赖时：

```bash
sudo apt update
sudo apt install -y python3 curl iproute2
```

## Step C：安装

```bash
sudo ./install.sh
```

如果旧 checkout 丢失可执行位：

```bash
chmod +x install.sh
sudo ./install.sh
```

或者：

```bash
sudo bash install.sh
```

Agent 不应修改安装路径；默认使用：

```text
/usr/local/bin/bjut-auth
/etc/bjut-auto-login.conf
```

## Step D：写入配置

目标配置格式：

```ini
[BJUT]
username = <USERNAME>
password = <PASSWORD>
type = auto
interface =
allow_http_fallback = false
connectivity_url = https://www.baidu.com/
```

写入后必须执行：

```bash
sudo chown root:root /etc/bjut-auto-login.conf
sudo chmod 600 /etc/bjut-auto-login.conf
```

除非用户明确要求，否则 Agent 应保持：

```ini
type = auto
interface =
allow_http_fallback = false
```

**不要仅根据接口名字猜测 Type 1/2/3。** 使用 `doctor` 或 `detect` 获取实际结果。

不要通过 `-p/--password` 传无人值守密码，因为命令行参数可能被本机其他用户看到。systemd 部署应使用权限为 `600` 的配置文件。

## Step E：运行 doctor

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf doctor
```

Agent 应读取并记录以下非敏感结果：

```text
Python 版本
curl 状态
ip 状态
config 路径
interface
IPv4
internet 状态
portal type
```

如 `doctor` 无法识别物理网卡、没有 IPv4、无法确定 Portal，停止部署并报告错误，不要继续猜测配置。

## Step F：执行 ensure

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf ensure
```

以下两种结果均可接受。

主机已经在线：

```text
online: interface=<iface>, skip login
```

主机未认证且登录成功：

```text
type=<1|2|3> interface=<iface>: ...认证成功...
```

然后必须执行：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf status
```

验收要求：

```text
online
```

且退出码为 `0`。

## Step G：启用 timer

只有 `ensure` 和 `status` 验证成功后才执行：

```bash
sudo systemctl enable --now bjut-auto-login.timer
```

验收：

```bash
systemctl is-enabled bjut-auto-login.timer
systemctl is-active bjut-auto-login.timer
```

期望输出：

```text
enabled
active
```

进一步检查：

```bash
systemctl status bjut-auto-login.timer --no-pager
```

应包含：

```text
Active: active (waiting)
```

## Step H：确认 timer 实际执行

```bash
journalctl -u bjut-auto-login.service -n 30 --no-pager
```

在线情况下通常看到：

```text
online: interface=<iface>, skip login
```

如果之后真实发生掉认证并成功恢复，会看到类似：

```text
type=3 interface=<iface>: Portal协议认证成功！
```

再执行：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf status
```

应返回：

```text
online
```

## Agent 部署完成判据

只有以下条件全部满足，Agent 才应报告 **部署成功**：

```text
[ ] /usr/local/bin/bjut-auth 存在
[ ] /etc/bjut-auto-login.conf 存在
[ ] 配置文件权限为 600
[ ] doctor 成功识别物理网卡
[ ] doctor 成功识别 Portal 类型
[ ] ensure 成功
[ ] status 返回 online
[ ] bjut-auto-login.timer = enabled
[ ] bjut-auto-login.timer = active
```

如果当前主机本来就在线，Agent 可以确认自动巡检链路已经部署，但**不能声称已经完成“真实掉认证后自动恢复验证”**，除非 systemd 日志中确实出现过一次认证成功记录。

## Agent 最终汇报建议格式

部署成功后只汇报非敏感信息，例如：

```text
BJUT Auto Login 部署完成
- version: 0.2.0
- interface: enp7s0
- IPv4: 172.x.x.x
- portal: type 3
- internet: online
- timer: enabled / active
- last ensure: success
```

不要输出：

```text
password
完整认证 URL 中的凭据
Cookie
Token
```

---

# 命令参考

环境诊断：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf doctor
```

检测认证类型：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf detect
```

检查公网：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf status
```

主动执行登录：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf login
```

在线跳过、离线登录：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf ensure
```

指定接口：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf --interface enp7s0 doctor
```

指定认证类型：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf --type 3 login
```

---

# systemd 状态说明

`bjut-auto-login.service` 使用 `Type=oneshot`。

因此一次任务执行完成后：

```text
Active: inactive (dead)
```

**属于正常状态，不代表服务故障。**

正确判断自动认证是否启用，应检查：

```bash
systemctl status bjut-auto-login.timer
```

正常状态：

```text
Active: active (waiting)
```

service 最近一次执行应为：

```text
status=0/SUCCESS
```

典型日志：

```text
Starting bjut-auto-login.service...
online: interface=enp7s0, skip login
bjut-auto-login.service: Deactivated successfully.
Finished bjut-auto-login.service.
```

这表示 timer → service → `ensure` 整条链路正常。

---

# Type 3 实际验证状态

BJUT 有线 Type 3 已完成如下真实闭环测试：

```text
已认证在线
    ↓
systemd timer 周期检查
    ↓
校园网认证失效
    ↓
下一轮 ensure 检测离线
    ↓
type=3 Portal 协议自动认证成功
    ↓
status = online
```

实际成功日志形式：

```text
type=3 interface=enp7s0: Portal协议认证成功！
```

因此 Type 3 不仅完成了协议级测试，也完成了 systemd 自动恢复测试。

---

# 安全说明

- Type 3 使用 HTTPS。
- Type 1 / Type 2 默认优先 HTTPS。
- 只有明确设置 `allow_http_fallback = true` 才允许 Type 1 / Type 2 回退到 HTTP。
- 带认证信息的 Portal URL 通过 `curl --config -` 从标准输入传给 curl，不直接暴露在 curl 的进程参数中。
- 密码配置文件建议始终保持 `0600`。
- 不要把真实 `config.ini`、密码、Cookie、Token 提交到 Git。

---

# WireGuard / Tailscale / VPN 环境

脚本会拒绝明显的虚拟网卡，例如：

```text
wg0
tun0
tailscale0
docker0
```

认证请求通过：

```text
curl --interface <physical-interface>
```

绑定到校园网物理接口，并禁用环境代理参与认证请求。

如果安装 WireGuard/Tailscale 后出现认证异常，优先运行：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf doctor
```

确认 `interface` 是真正连接 BJUT 校园网的物理接口。

---

# 故障排查

## `sudo: ./install.sh: 找不到命令` / 无法执行

更新仓库并检查权限：

```bash
git pull --ff-only
ls -l install.sh
```

正常应类似：

```text
-rwxr-xr-x
```

旧 checkout 可执行：

```bash
chmod +x install.sh
sudo ./install.sh
```

或者：

```bash
sudo bash install.sh
```

## 无法自动识别网卡

```bash
ip route show table main default
ip -br addr
```

确认实际物理接口后：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf --interface enp7s0 doctor
```

## 自动认证失败

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf doctor
systemctl status bjut-auto-login.service --no-pager
journalctl -u bjut-auto-login.service -n 100 --no-pager
```

如果 `doctor` 能识别 Portal，但 `login` 返回认证网关错误，请保留完整的非敏感错误信息用于排查。

---

# 更新

```bash
cd BJUT-Auto-Login-Headless-
git pull --ff-only
sudo ./install.sh
```

更新后建议：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf doctor
sudo bjut-auth --config /etc/bjut-auto-login.conf ensure
sudo systemctl restart bjut-auto-login.timer
```

---

# 卸载

保留配置：

```bash
sudo ./uninstall.sh
```

同时删除配置：

```bash
sudo ./uninstall.sh --purge
```

---

# Type 3 协议概要

新版 BJUT 有线认证大致流程：

```text
lgn6.bjut.edu.cn/drcom/getipv6
        ↓
获取网关观测 IPv6
        ↓
获取校园网物理接口 IPv4
        ↓
登录参数 UTF-16 code unit XOR 0x16
        ↓
十六进制编码
        ↓
lgn.bjut.edu.cn:802/eportal/portal/login
        ↓
解析 JSONP result
```

---

# 开发与测试

项目无第三方 Python 包依赖：

```bash
python3 -m py_compile bjut_auth.py
python3 -m unittest discover -s tests -v
bash -n install.sh uninstall.sh
```

GitHub Actions 会在 Python 3.8 / 3.10 / 3.12 上运行测试。

---

# 致谢与许可

现行 BJUT Portal 端点、参数与认证流程参考并重新实现自 [`key-zhzr/BJUT-Auto-Login`](https://github.com/key-zhzr/BJUT-Auto-Login)，原项目采用 MIT License。相关许可声明见 [`THIRD_PARTY_NOTICES`](THIRD_PARTY_NOTICES)。

本项目采用 MIT License。