# BJUT Auto Login (Headless)

面向北京工业大学（BJUT）校园网的 Linux 无界面自动认证工具，适用于服务器、工作站、NAS 以及其他 Headless Linux 环境。

项目目标：**无需 GUI 或浏览器，在校园网认证失效后自动检测并恢复联网。**

当前版本：`0.4.0`

> Type 3（BJUT 有线 `lgn`）已完成实机自动重连流程验证。Type 1 / Type 2 已按当前 Portal 协议实现，建议首次部署时在对应网络环境执行一次人工验证。

---

## 功能特性

- 支持 Type 1：宿舍网 ePortal
- 支持 Type 2：`bjut_wifi` / `wlgn.bjut.edu.cn`
- 支持 Type 3：有线 `lgn.bjut.edu.cn` 加密 ePortal
- 支持 `auto` 自动判断认证类型
- 自动识别校园网物理接口
- 支持双网口 / 多网口服务器
- default route 暂时消失时仍可识别候选校园接口
- Portal 请求通过 `curl --interface` 绑定到选定物理接口
- 排除 WireGuard / TUN / Tailscale / Docker 等常见虚拟接口
- Type 3 登录参数按当前 Portal 算法加密
- Portal DNS、连接、TLS 或瞬时 HTTP 5xx 异常时进行受控回退
- `doctor` 部署前自检
- `status` 公网状态检测
- `ensure` 在线跳过、离线自动认证
- Portal 返回异常但公网已实际恢复时，以最终公网状态作为 `ensure` 成功判据
- systemd timer 周期巡检和自动掉线恢复
- NetworkManager 网络变化事件即时触发检测
- 2 秒事件防抖，无额外常驻轮询进程
- 60 秒周期 timer 作为 Portal 静默掉线兜底
- 可自定义公网连通性检测地址
- 可选 `connectivity_resolve_ip` 固定健康检查域名到指定 IP
- 配置文件权限检查与敏感信息保护
- GitHub Actions 多版本测试

项目本身**不会创建独立日志文件**。systemd 部署时 stdout / stderr 由 `journald` 接管。

---

## 1. 系统要求

```text
Linux
Python >= 3.8
curl
iproute2
systemd          # 自动重连需要
NetworkManager   # 可选，用于网络变化事件触发
```

Ubuntu / Debian：

```bash
sudo apt update
sudo apt install -y python3 curl iproute2
```

---

## 2. 安装

```bash
git clone https://github.com/stainzhao/BJUT-Auto-Login-Headless-.git
cd BJUT-Auto-Login-Headless-
sudo ./install.sh
```

如果系统不使用 systemd，只安装 CLI：

```bash
sudo ./install.sh --no-systemd
```

默认安装位置：

```text
/usr/local/bin/bjut-auth
/etc/bjut-auto-login.conf
/etc/systemd/system/bjut-auto-login.service
/etc/systemd/system/bjut-auto-login.timer
```

如果系统运行 NetworkManager，还会安装网络事件 dispatcher 和对应的防抖 timer。

`install.sh` 可以重复执行用于升级。已有 `/etc/bjut-auto-login.conf` 不会被覆盖。

---

## 3. 配置

编辑配置文件：

```bash
sudo nano /etc/bjut-auto-login.conf
```

通用示例：

```ini
[BJUT]
username = <campus_username>
password = <campus_password>

# auto / 1 / 2 / 3
type = auto

# 推荐先留空，由程序自动判断。
# 自动判断失败时再填写实际校园网物理接口，例如 eno1 / enp3s0 / wlan0。
interface =

# Type 1 / Type 2 HTTPS 失败时是否允许使用 HTTP。
# 默认关闭，仅在可信校园网络且确有需要时启用。
allow_http_fallback = false

# 用于判断公网是否真正可用。
connectivity_url = http://connect.rom.miui.com/generate_204

# 可选。用于把 connectivity_url 中的域名固定到指定 IP。
# 留空时使用系统 DNS。
connectivity_resolve_ip =
```

配置权限必须限制为仅 root 可读写：

```bash
sudo chown root:root /etc/bjut-auto-login.conf
sudo chmod 600 /etc/bjut-auto-login.conf
```

**不要把真实校园网账号或密码提交到 Git、Issue、PR 或日志。**

### 配置校验

程序会主动拒绝以下常见错误：

- 显式指定但不存在的配置文件
- 未知配置字段
- 非 `auto / 1 / 2 / 3` 的认证类型
- 无效布尔值
- 非 HTTP / HTTPS 的 `connectivity_url`
- `connectivity_url` 中包含用户名或密码
- 示例占位密码
- 明显的虚拟接口

密码中的 `%` 按字面量处理，不会被 `ConfigParser` 插值。

---

## 4. 公网健康检查

默认配置使用轻量 204 探测地址：

```ini
connectivity_url = http://connect.rom.miui.com/generate_204
```

正常联网时该地址返回 `204 No Content`，不需要下载网页正文。

也可以使用其他稳定的 HTTP / HTTPS 2xx 地址，例如自建健康检查服务：

```ini
connectivity_url = https://check.example.com/204
connectivity_resolve_ip = 192.0.2.10
```

其中 `192.0.2.10` 为文档示例地址，请替换为实际服务器地址。

填写 `connectivity_resolve_ip` 后，程序使用 curl `--resolve` 直接连接指定 IP，同时保留 URL 主机名、TLS SNI 和证书校验。

一个最简单的 Nginx 204 端点可以写成：

```nginx
location = /204 {
    access_log off;
    return 204;
}
```

---

## 5. 自动选择网卡

单网口设备通常无需设置 `interface`。

多网口环境会按以下顺序综合判断：

1. 显式设置的 `--interface` 或配置文件 `interface`
2. 唯一可用的非虚拟 IPv4 接口
3. BJUT Portal 地址的实际路由及源 IPv4
4. BJUT 校园 IPv4 地址特征
5. 无凭据 Portal 探测
6. 如果仍然存在多个无法区分的候选接口，则安全报错

示例拓扑：

```text
enp3s0      -> BJUT 校园网
enp4s0      -> 管理网 / 内网
wg0         -> WireGuard
tailscale0  -> Tailscale
```

如果已明确知道校园网接口，可以直接固定：

```ini
interface = enp3s0
```

查看 Type 3 Portal 的实际路由：

```bash
ip -4 route get 172.30.201.2
```

示例：

```text
172.30.201.2 via <gateway> dev enp3s0 src <campus_ipv4>
```

其中 `dev` 对应出口接口，`src` 对应实际源 IPv4。

---

## 6. 部署前自检

运行：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf doctor
```

正常输出形式类似：

```text
BJUT Auto Login 0.4.0
python: 3.x.x
curl: OK (/usr/bin/curl)
ip: OK (/usr/sbin/ip)
config: /etc/bjut-auto-login.conf
config-security: OK
credentials: configured (config)
interfaces: enp3s0=<campus_ipv4>
interface: enp3s0 (wired)
ipv4: <campus_ipv4>
internet: online
portal: type 3
```

说明：

- 尚未认证时出现 `internet: offline` 可以是正常现象。
- `portal: ERROR (...)` 会使 `doctor` 返回非零退出码。
- 凭据缺失或仍为示例值会使检查失败。
- 配置文件权限过宽会被视为安全问题。

查看退出码：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf doctor
echo $?
```

成功时应为：

```text
0
```

---

## 7. 首次认证验证

运行：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf ensure
```

已经在线时：

```text
online: interface=<interface>, skip login
```

未认证并成功登录时：

```text
type=<type> interface=<interface>: Portal协议认证成功！
```

随后检查公网：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf status
```

正常结果：

```text
online
```

`ensure` 即使在当前已经在线时，也会先确认自动重连所需凭据有效，避免真正掉线后才发现配置不完整。

---

## 8. 自动重连机制

### 8.1 周期兜底

验证 `doctor` 和 `ensure` 后启用：

```bash
sudo systemctl enable --now bjut-auto-login.timer
```

检查：

```bash
systemctl is-enabled bjut-auto-login.timer
systemctl is-active bjut-auto-login.timer
```

正常应为：

```text
enabled
active
```

当前 timer 核心调度：

```ini
OnActiveSec=20s
OnUnitInactiveSec=60s
```

含义：

- timer 首次启用或重启后，约 20 秒执行第一次检查；
- 每次 `bjut-auto-login.service` 执行结束后，约 60 秒执行下一次兜底检查；
- `AccuracySec` / `RandomizedDelaySec` 会带来少量正常调度抖动。

查看状态：

```bash
systemctl status bjut-auto-login.timer --no-pager
```

正常应显示：

```text
Active: active (waiting)
```

并存在未来的 `Trigger` 时间。

### 8.2 网络变化事件即时检测

在 NetworkManager 系统上，安装器会安装 dispatcher。

以下类型的网络事件会触发检查：

```text
up
dhcp4-change
dhcp6-change
connectivity-change
reapply
```

连续事件先经过约 2 秒防抖，然后触发与周期巡检相同的：

```text
bjut-auto-login.service
```

明显的 WireGuard、TUN、Tailscale、Docker 等虚拟接口事件会被忽略。

事件触发仅在 `bjut-auto-login.timer` 已启用时生效，因此关闭周期 timer 也等价于关闭自动登录机制。

### 8.3 为什么仍保留 60 秒 timer

Portal 认证会话失效时，可能出现：

```text
物理网卡仍然 UP
IPv4 / IPv6 未变化
路由未变化
DNS 未变化
但公网已经不可访问
```

这种“静默掉线”通常不会产生系统网络变化事件，因此需要周期 timer 作为兜底。

最终逻辑为：

```text
网络变化事件
    ↓
约 2 秒防抖
    ↓
立即 ensure

        +

约 60 秒周期兜底
    ↓
ensure
    ↓
公网正常 -> 跳过登录
公网异常 -> Portal 认证 -> 再次确认公网
```

没有 NetworkManager 的系统仍可正常使用 systemd timer，不受影响。

---

## 9. systemd 状态说明

`bjut-auto-login.service` 使用：

```ini
Type=oneshot
```

所以每次执行完成后：

```text
Active: inactive (dead)
```

**这是正常状态。**

自动认证是否启用，应查看：

```bash
systemctl status bjut-auto-login.timer
```

在线时日志类似：

```text
Starting bjut-auto-login.service...
online: interface=<interface>, skip login
bjut-auto-login.service: Deactivated successfully.
Finished bjut-auto-login.service.
```

掉线恢复成功时类似：

```text
type=<type> interface=<interface>: Portal协议认证成功！
```

部分 Portal 节点可能已经完成认证，但响应阶段返回 HTTP 5xx 或超时。`ensure` 会短暂复核最终公网状态；如果公网已经恢复，会保留 warning，但 systemd 仍按成功处理：

```text
warning: Portal 认证过程异常（HTTP 500...），但公网已恢复，视为认证成功
```

---

## 10. 日志

查看最近日志：

```bash
journalctl -u bjut-auto-login.service -n 50 --no-pager
```

实时查看：

```bash
journalctl -fu bjut-auto-login.service
```

查看 journal 磁盘占用：

```bash
journalctl --disk-usage
```

本项目不会创建持续增长的独立 `.log` 文件。

---

## 11. Portal、DNS 与瞬时网络错误

开机、DHCP 更新、IPv6 尚未完全就绪、校园 DNS 暂时异常或 Portal 短时异常时，可能看到：

```text
curl: (6) Could not resolve host: lgn6.bjut.edu.cn
HTTP 500
HTTP 502
HTTP 503
HTTP 504
```

程序首先按正常 DNS 访问 Portal。

Type 3 `getipv6` 地址发现保留系统原生地址族选择；正常请求失败或返回无效结果时，会继续尝试受控固定地址。

已知 Portal 固定地址回退：

```text
lgn6.bjut.edu.cn:443 -> 172.30.201.2 / 172.30.201.10
lgn.bjut.edu.cn:802  -> 172.30.201.2 / 172.30.201.10
wlgn.bjut.edu.cn:443 -> 10.21.251.3
```

实现使用 `curl --resolve`，因此 HTTPS 主机名、SNI 和证书校验仍保持原域名。

固定地址回退仅用于内置 BJUT Portal 白名单，不会应用到普通互联网域名。

单次执行采用有限重试，不会在一个进程中无限循环。若瞬时故障仍未恢复，systemd timer 会在下一轮继续检查。

永久配置错误不会被无限掩盖，例如：

```text
配置文件不存在
凭据仍为示例值
存在多个无法区分的候选网卡
Portal 无法确认
认证网关明确拒绝账号或密码
```

---

## 12. 常用命令

查看版本：

```bash
bjut-auth --version
```

诊断：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf doctor
```

检测 Portal：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf detect
```

检查公网状态：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf status
```

执行一次登录：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf login
```

执行自动逻辑：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf ensure
```

指定接口：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf --interface enp3s0 doctor
```

指定 Type 3：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf --type 3 login
```

---

## 13. 更新

```bash
cd BJUT-Auto-Login-Headless-
git pull --ff-only
sudo ./install.sh
```

如果 timer 已经运行，安装器会重新加载 systemd unit，并使新的调度规则生效。

更新后建议确认：

```bash
bjut-auth --version
systemctl status bjut-auto-login.timer --no-pager
```

---

## 14. 卸载

保留账号配置：

```bash
sudo ./uninstall.sh
```

同时删除配置：

```bash
sudo ./uninstall.sh --purge
```

非 systemd 环境也可以正常卸载。

---

## 15. 双网口与远程服务器排障

查看全局 IPv4：

```bash
ip -4 -o addr show scope global
```

查看 Type 3 Portal 路由：

```bash
ip -4 route get 172.30.201.2
```

如果自动识别仍无法唯一确定校园接口，可以显式配置：

```ini
interface = enp3s0
```

然后重新运行：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf doctor
```

如果当前 SSH 会话依赖校园网，**不要为了测试自动重连而远程关闭正在使用的物理网卡**，例如：

```bash
sudo ip link set <campus_interface> down
```

这可能直接中断远程连接。

---

## 16. 安全设计

- Type 3 使用 HTTPS。
- Type 1 / Type 2 默认优先 HTTPS。
- HTTP fallback 默认关闭。
- Portal 请求不读取系统 HTTP / HTTPS proxy。
- Portal 请求绑定选定物理接口。
- 除 Type 3 `getipv6` 正常发现请求保留系统原生地址族外，其余 Portal 请求优先 IPv4。
- 固定地址回退仅允许内置 BJUT Portal 白名单，并继续使用原 HTTPS 主机名与 SNI。
- 含凭据的完整 URL 通过 `curl --config -` 从 stdin 传给 curl，不出现在 curl argv。
- curl 错误信息会进行敏感字段脱敏。
- 非 2xx Portal 响应只记录 HTTP 状态，不输出响应体。
- systemd service 使用 `UMask=0077`、`NoNewPrivileges=true`、`ProtectSystem=strict` 等限制。
- 配置文件建议只允许 root 读取。

---

## 17. Type 3 协议流程

当前流程：

```text
确定物理接口与源 IPv4
    ↓
访问 lgn6.bjut.edu.cn
    ↓
取得 Portal 观测到的 IPv6
    ↓
准备用户名 / 密码 / IPv4 / IPv6 等字段
    ↓
UTF-16 code unit XOR 0x16
    ↓
hex 编码
    ↓
GET https://lgn.bjut.edu.cn:802/eportal/portal/login
    ↓
解析 JSONP result
    ↓
再次检查公网状态
```

正常 DNS 或连接异常时，只对受信 BJUT Portal 使用预设固定地址进行回退。

---

## 18. 开发与测试

项目没有第三方 Python 包依赖。

本地检查：

```bash
python3 -m py_compile bjut_auth.py
python3 -m unittest discover -s tests -v
bash -n install.sh uninstall.sh NetworkManager/dispatcher.d/90-bjut-auto-login
```

测试覆盖包括：

- Type 1 / Type 2 / Type 3 协议参数
- Type 3 XOR 与 Unicode UTF-16 code unit 加密
- JSONP 解析
- 虚拟接口与 Fake-IP 排除
- route `dev/src` 解析
- 单网口 / 双网口 / 多地址接口选择
- default route 缺失 fallback
- HTTP 5xx 有限重试
- Portal DNS 失败固定地址回退
- 固定地址顺序回退
- 非 BJUT 域名禁止固定解析
- 非瞬时 HTTP 4xx 不回退
- 配置与凭据校验
- 公网健康检查与固定 IP 解析
- Portal 异常但公网已经恢复时的最终状态确认
- systemd timer 调度规则
- NetworkManager dispatcher 与事件防抖 timer

GitHub Actions 会在多个 Python 版本上自动执行测试。

---

## 19. 致谢与许可

本项目的 Portal 协议实现和兼容策略参考了以下开源项目：

- [key-zhzr/BJUT-Auto-Login](https://github.com/key-zhzr/BJUT-Auto-Login) — 当前 BJUT Portal 协议、Type 3 加密流程及兼容策略的重要参考，采用 MIT License。
- [sw1128/bjut_auth_linux](https://github.com/sw1128/bjut_auth_linux) — 较早的 BJUT Linux 校园网认证实现，为 Linux 无界面认证流程提供参考。

本项目不是上述项目的官方版本或附属项目。第三方归属与许可说明见 [`THIRD_PARTY_NOTICES`](./THIRD_PARTY_NOTICES)。

本项目采用 MIT License，具体以仓库中的 [`LICENSE`](./LICENSE) 为准。
