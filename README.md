# BJUT Auto Login (Headless)

## 致谢 / Acknowledgements

本项目能够完成，离不开前人对 BJUT 校园网认证流程的公开整理与实现。特别感谢以下项目及其作者：

- **[key-zhzr/BJUT-Auto-Login](https://github.com/key-zhzr/BJUT-Auto-Login)**：本项目对 2026 年现行 BJUT Portal 的端点、请求参数、Type 3 加密认证流程，以及校园固定地址兼容策略的实现主要参考该项目。原项目采用 MIT License。
- **[sw1128/bjut_auth_linux](https://github.com/sw1128/bjut_auth_linux)**：较早的 BJUT Linux 校园网一键认证实现，为本项目理解旧版 Linux 无界面认证流程、Type 1 / Type 2 兼容方式提供了重要参考。

感谢上述作者将相关研究和实现公开分享，使本项目能够在此基础上进一步面向 **Headless Linux / systemd / 多网口服务器 / 无人值守自动恢复** 场景进行独立整理、实现和完善。

> 本项目不是上述项目的官方版本或附属项目。第三方许可与归属说明见 [`THIRD_PARTY_NOTICES`](./THIRD_PARTY_NOTICES)。

---

北京工业大学校园网无 UI 自动认证工具，面向 Linux 服务器、工作站、NAS 和其他无桌面环境。

项目目标很简单：**不运行 GUI，不依赖浏览器，在校园网认证失效后自动恢复联网。**

当前版本：`0.3.3`

> Type 3（BJUT 有线 `lgn`）已经在真实服务器环境完成“认证失效 → systemd timer 检测离线 → 自动重新认证 → `status=online`”闭环验证。
>
> Type 1 / Type 2 已按 2026 年现行 Portal 实现对齐，但仍建议在对应网络环境首次部署时执行一次人工验证。

---

## 功能

- Type 1：宿舍网 `10.21.221.98` ePortal
- Type 2：`bjut_wifi` / `wlgn.bjut.edu.cn`
- Type 3：有线 `lgn.bjut.edu.cn` 新版加密 ePortal
- `auto` 自动判断认证类型
- 自动识别校园网物理接口
- 支持双网口 / 多网口服务器
- default route 暂时消失时仍可继续识别候选校园接口
- 认证请求通过 `curl --interface` 绑定到选定接口
- 排除明显的 WireGuard / TUN / Tailscale / Docker 等虚拟接口
- Type 3 登录参数按现行 Portal 算法加密
- Portal DNS 解析、连接、TLS 或瞬时 5xx 异常时进行受控回退
- `lgn6.bjut.edu.cn` / `lgn.bjut.edu.cn` 固定地址回退仍保留原 HTTPS 主机名和 SNI
- Type 3 `getipv6` 正常请求保留系统原生地址族选择；`result != 1` 时继续尝试 `.2` / `.10` 两个校园端点
- `doctor` 部署前自检
- `status` 公网状态检测
- `ensure`：在线跳过，离线登录
- `ensure` 以最终公网状态为成功判据；Portal 5xx/超时但认证已生效时避免 systemd 假失败
- systemd timer 周期巡检和掉线恢复
- 配置文件安全检查
- GitHub Actions 单元测试

项目本身**不写独立日志文件**。systemd 部署时，stdout/stderr 由 `journald` 接管。

---

# 1. 依赖

```text
Linux
Python >= 3.8
curl
iproute2
systemd     # 仅自动重连需要
```

Ubuntu / Debian：

```bash
sudo apt update
sudo apt install -y python3 curl iproute2
```

---

# 2. 快速部署

```bash
git clone https://github.com/stainzhao/BJUT-Auto-Login-Headless-.git
cd BJUT-Auto-Login-Headless-
sudo ./install.sh
```

如果当前系统不是 systemd，只安装 CLI：

```bash
sudo ./install.sh --no-systemd
```

安装后：

```text
/usr/local/bin/bjut-auth
/etc/bjut-auto-login.conf
/etc/systemd/system/bjut-auto-login.service
/etc/systemd/system/bjut-auto-login.timer
```

`install.sh` 可重复执行用于升级。已有 `/etc/bjut-auto-login.conf` 不会被覆盖。

---

# 3. 配置

编辑：

```bash
sudo nano /etc/bjut-auto-login.conf
```

示例：

```ini
[BJUT]
username = 25000000
password = change_me

# auto / 1 / 2 / 3
type = auto

# 推荐先留空，让程序自动判断。
# 双网口环境若仍无法唯一判断，可显式写 enp7s0 / eno1 / wlan0 等。
interface =

# 默认关闭。
# 只有 Type 1 / Type 2 HTTPS 无法使用且确认校园网可信时才开启。
allow_http_fallback = false

# 用于确认是否真正访问公网。
connectivity_url = https://www.baidu.com/
```

配置权限必须收紧：

```bash
sudo chown root:root /etc/bjut-auto-login.conf
sudo chmod 600 /etc/bjut-auto-login.conf
```

不要把真实账号密码提交到 Git。

## 配置校验

程序会主动拒绝：

- 不存在的显式 `--config`
- 未知配置字段
- `type` 非 `auto/1/2/3`
- 拼错的布尔值，例如 `flase`
- 非 `http/https` 的 `connectivity_url`
- 带用户名或密码的 `connectivity_url`
- 示例密码 `change_me`
- 明显的虚拟接口

密码中的 `%` 会按字面量处理，不会被 `ConfigParser` 插值。

---

# 4. 自动选网卡逻辑

单网口机器通常无需配置 `interface`。

多网口服务器按以下信息综合判断：

1. 如果用户显式设置 `--interface` 或配置 `interface = ...`，始终优先。
2. 如果只有一个具有可用 IPv4 的非虚拟接口，直接使用。
3. 检查到 BJUT Portal 地址的实际路由及其源 IPv4。
4. default route 暂时消失时，使用现行 BJUT 校园 IPv4 地址族作为保守 fallback。
5. 多个候选仍无法区分时，对候选接口执行无凭据 Portal 探测。
6. 仍然歧义时**安全报错**，而不是随机选网卡。

这主要解决以下情况：

```text
enp7s0 -> BJUT 校园网
enp8s0 -> 管理网 / 内网 / 第二条外网
wg0    -> WireGuard
tailscale0 -> Tailscale
```

以及认证失效后主路由暂时消失的情况。

如果你已经明确知道校园网口，固定接口最确定：

```ini
interface = enp7s0
```

---

# 5. 部署前自检

运行：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf doctor
```

正常输出类似：

```text
BJUT Auto Login 0.3.3
python: 3.12.3
curl: OK (/usr/bin/curl)
ip: OK (/usr/sbin/ip)
config: /etc/bjut-auto-login.conf
config-security: OK
credentials: configured (config)
interfaces: enp7s0=172.19.26.49
interface: enp7s0 (wired)
ipv4: 172.19.26.49
internet: online
portal: type 3
```

注意：

- `internet: offline` 在尚未认证时**可以是正常的**。
- `portal: ERROR (...)` 会让 `doctor` 返回非零退出码。
- 凭据缺失或仍为示例值也会让 `doctor` 失败。
- 配置权限过宽会被视为部署问题。

检查退出码：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf doctor
echo $?
```

成功应为：

```text
0
```

---

# 6. 首次认证验证

运行：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf ensure
```

如果已经在线：

```text
online: interface=enp7s0, skip login
```

如果当前未认证，成功时类似：

```text
type=3 interface=enp7s0: Portal协议认证成功！
```

然后：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf status
```

成功：

```text
online
```

`ensure` 即使当前在线，也会先确认已经配置有效凭据，避免“部署时在线，但真正掉线后才发现密码仍是 `change_me`”这种假成功。

---

# 7. 启用自动重连

只有 `doctor` 和 `ensure` 验证通过后再启用：

```bash
sudo systemctl enable --now bjut-auto-login.timer
```

检查：

```bash
systemctl is-enabled bjut-auto-login.timer
systemctl is-active bjut-auto-login.timer
```

期望：

```text
enabled
active
```

查看详细状态：

```bash
systemctl status bjut-auto-login.timer --no-pager
```

正常：

```text
Active: active (waiting)
```

默认约每 60 秒检查一次。

---

# 8. systemd 状态说明

`bjut-auto-login.service` 是：

```ini
Type=oneshot
```

因此执行完后：

```text
Active: inactive (dead)
```

**是正常状态。**

正确判断是否启用自动认证，应看 timer：

```bash
systemctl status bjut-auto-login.timer
```

在线时典型日志：

```text
Starting bjut-auto-login.service...
online: interface=enp7s0, skip login
bjut-auto-login.service: Deactivated successfully.
Finished bjut-auto-login.service.
```

真实掉认证后成功恢复时：

```text
type=3 interface=enp7s0: Portal协议认证成功！
```

部分 Portal 节点可能已经完成认证，却在响应阶段返回 HTTP 5xx。`ensure` 会短暂复核最终公网状态；如果公网已经恢复，会保留 warning 但仍让 systemd 正常成功退出：

```text
warning: Portal 认证过程异常（HTTP 500...），但公网已恢复，视为认证成功
bjut-auto-login.service: Deactivated successfully.
Finished bjut-auto-login.service.
```

---

# 9. 日志

查看最近日志：

```bash
journalctl -u bjut-auto-login.service -n 50 --no-pager
```

实时查看：

```bash
journalctl -fu bjut-auto-login.service
```

查看 journal 总磁盘占用：

```bash
journalctl --disk-usage
```

本项目没有自己的日志文件，也不会创建持续增长的 `.log` 文件。

---

# 10. 瞬时网络与 DNS 错误

服务器开机、DHCP 更新、IPv6 尚未完全就绪、校园 DNS 暂时异常或 Portal 短时异常时，可能遇到：

```text
curl: (6) Could not resolve host: lgn6.bjut.edu.cn
HTTP 500
HTTP 502
HTTP 503
HTTP 504
```

当前版本首先按正常 DNS 访问 Portal。Type 3 的 `getipv6` 地址发现会保留系统原生地址族选择；若正常请求失败或返回 `result != 1`，会继续尝试受控固定地址。其他 BJUT Portal 域名仅在 DNS、连接、TLS、超时或瞬时 5xx 错误时使用固定地址回退：

```text
lgn6.bjut.edu.cn:443 -> 172.30.201.2 / 172.30.201.10
lgn.bjut.edu.cn:802  -> 172.30.201.2 / 172.30.201.10
wlgn.bjut.edu.cn:443 -> 10.21.251.3
```

实现使用 `curl --resolve`，因此 URL 中仍然是 `lgn6.bjut.edu.cn` / `lgn.bjut.edu.cn`，HTTPS 主机名、SNI 和证书校验不会因为回退到固定 IPv4 而被替换成裸 IP。

固定地址回退只允许上述白名单 Portal；不会对普通互联网域名使用。

如果本轮所有有限尝试仍失败，systemd timer 会在下一轮继续检查，不会在单次进程里无限循环。

永久配置错误不会被无限掩盖，例如：

```text
配置文件不存在
密码仍是 change_me
存在多个无法区分的候选网卡
Portal 无法确认
认证网关明确拒绝账号密码
```

---

# 11. 常用命令

版本：

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

公网状态：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf status
```

主动登录：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf login
```

自动逻辑：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf ensure
```

固定接口：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf --interface enp7s0 doctor
```

固定 Type 3：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf --type 3 login
```

---

# 12. 更新

```bash
cd BJUT-Auto-Login-Headless-
git pull --ff-only
sudo ./install.sh
```

如果本地目录仍叫旧名称，例如：

```text
~/auto_login
```

目录名不影响：

```bash
cd ~/auto_login
git pull --ff-only
sudo ./install.sh
```

已经运行的 timer 会在安装后重新加载新的 unit 定义。

---

# 13. 卸载

保留账号配置：

```bash
sudo ./uninstall.sh
```

连配置一起删除：

```bash
sudo ./uninstall.sh --purge
```

非 systemd 环境也可以正常卸载。

---

# 14. AI Agent / Codex 自动部署协议

如果 Agent 负责在新服务器部署本项目，按以下顺序执行。

## A. 前置条件

Agent 必须确认：

- Linux
- root / sudo 可用
- 用户已提供 BJUT 凭据，或凭据已安全存在目标机
- 不把密码写进 Git / PR / Issue / 最终汇报
- 不通过 `-p/--password` 长期保存密码
- 如果当前 SSH 依赖校园网，不主动执行断网、改 MAC、删路由等危险测试

## B. 获取代码

新部署：

```bash
git clone https://github.com/stainzhao/BJUT-Auto-Login-Headless-.git
cd BJUT-Auto-Login-Headless-
```

已有部署：

```bash
git pull --ff-only
```

## C. 安装

systemd Linux：

```bash
sudo ./install.sh
```

非 systemd：

```bash
sudo ./install.sh --no-systemd
```

## D. 写入配置

目标：

```text
/etc/bjut-auto-login.conf
owner: root:root
mode: 600
```

默认保持：

```ini
type = auto
interface =
allow_http_fallback = false
```

Agent 不应仅根据接口名猜 `type`。

如果自动判断唯一失败，Agent 可以根据 `doctor`、`ip -4 addr` 和 `ip -4 route get 172.30.201.2` 的结果确定校园接口，再写入 `interface = ...`。

Agent 不应因为 `lgn6.bjut.edu.cn` 系统 DNS 解析失败而立即修改 `/etc/hosts`；当前版本会在程序内部对已知 BJUT Portal 做固定地址回退。

## E. doctor

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf doctor
```

Agent 只记录非敏感结果：

```text
version
Python
curl/ip
config security
credentials configured / error
candidate interfaces
selected interface
IPv4
internet
portal type
```

必须检查退出码。

## F. ensure

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf ensure
```

然后：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf status
```

必须得到：

```text
online
```

## G. timer

仅 systemd：

```bash
sudo systemctl enable --now bjut-auto-login.timer
systemctl is-enabled bjut-auto-login.timer
systemctl is-active bjut-auto-login.timer
```

必须得到：

```text
enabled
active
```

## H. 最终验收

只有以下条件全部满足，Agent 才能报告部署成功：

```text
[ ] /usr/local/bin/bjut-auth 存在
[ ] /etc/bjut-auto-login.conf 存在
[ ] 配置权限为 600
[ ] doctor 退出码 = 0
[ ] credentials = configured
[ ] 已唯一确定校园网接口
[ ] 已识别 Portal 类型
[ ] ensure 退出码 = 0
[ ] status = online
[ ] timer = enabled        # systemd 部署
[ ] timer = active         # systemd 部署
```

如果部署时机器本来就在线，只能说明“自动巡检链路已部署”。

只有日志确实出现过：

```text
type=<...> interface=<...>: ...认证成功...
```

并随后 `status=online`，才能声称已经完成真实掉认证恢复验证。

Agent 最终汇报示例：

```text
BJUT Auto Login 部署完成
- version: 0.3.3
- interface: enp7s0
- IPv4: 172.x.x.x
- portal: type 3
- internet: online
- timer: enabled / active
- last ensure: success
```

严禁输出：

```text
password
Cookie
Token
完整认证请求 URL
```

---

# 15. 双网口故障排查

查看所有 IPv4：

```bash
ip -4 -o addr show scope global
```

查看到 Type 3 Portal 的实际路由：

```bash
ip -4 route get 172.30.201.2
```

例如：

```text
172.30.201.2 via ... dev enp7s0 src 172.19.26.49
```

这里：

```text
dev enp7s0
src 172.19.26.49
```

通常就是认证所应使用的接口和源 IPv4。

如果自动模式仍无法唯一判断：

```ini
interface = enp7s0
```

然后：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf doctor
```

不要为了验证自动登录而远程执行：

```bash
ip link set enp7s0 down
```

否则可能直接断掉 SSH。

---

# 16. 安全设计

- Type 3 使用 HTTPS。
- Type 1 / Type 2 默认优先 HTTPS。
- HTTP fallback 默认关闭。
- Portal 请求不读取系统 HTTP/HTTPS proxy。
- Portal 请求绑定选定接口。
- 除 Type 3 `getipv6` 的正常发现请求保留系统原生地址族外，其余 Portal 请求优先使用 IPv4，减少 AAAA / IPv6 路由对物理接口选择的干扰。
- 固定地址回退仅允许内置 BJUT Portal 白名单，并继续使用原 HTTPS 主机名/SNI。
- 含凭据的完整 URL 通过 `curl --config -` 从 stdin 传给 curl，不出现在 curl argv。
- curl 错误信息会脱敏。
- 非 2xx Portal 响应只记录 HTTP 状态，不输出响应体，降低敏感信息回显风险。
- systemd 配置使用 `UMask=0077`、`NoNewPrivileges=true`、`ProtectSystem=strict` 等限制。
- 配置文件建议仅 root 可读。

---

# 17. 协议实现说明

Type 3 当前流程：

```text
确定物理接口和源 IPv4
    ↓
正常 DNS 请求 lgn6.bjut.edu.cn
失败时仅对受信 Portal 使用 172.30.201.2 / 172.30.201.10 回退
    ↓
GET https://lgn6.bjut.edu.cn/drcom/getipv6
    ↓
取得 Portal 观测到的 IPv6
    ↓
用户名 / 密码 / IPv4 / IPv6 等字段
UTF-16 code unit XOR 0x16
    ↓
hex 编码
    ↓
GET https://lgn.bjut.edu.cn:802/eportal/portal/login
    ↓
解析 JSONP result
```

Type 1 / Type 2 的参数顺序和重复 `lang=zh-cn` / `lang=zh` 已按当前参考实现对齐。

---

# 18. 开发与测试

项目没有第三方 Python 包依赖。

运行：

```bash
python3 -m py_compile bjut_auth.py
python3 -m unittest discover -s tests -v
bash -n install.sh uninstall.sh
```

测试覆盖：

- Type 3 XOR 加密
- Unicode UTF-16 code unit 加密
- JSONP 解析
- Type 1 / Type 2 请求参数
- 重复 `lang`
- 虚拟接口排除
- Fake-IP 排除
- route `dev/src` 解析
- 单网口无 default route
- 双网口 Portal 路由选择
- 双网口 default route 缺失 fallback
- 多地址接口的路由源 IPv4
- HTTP 5xx 瞬时重试
- Portal DNS 失败固定地址回退
- 固定地址 `.2 -> .10` 顺序回退
- 非 BJUT 域名禁止固定解析
- 非瞬时 4xx 不回退
- `%` 密码
- 显式缺失配置
- 未知配置字段
- 错误布尔值
- connectivity URL 校验
- 示例凭据检测
- `doctor` Portal 失败退出码
- `doctor` 配置凭据不会被临时环境变量掩盖

GitHub Actions 会在多个 Python 版本上执行测试。

---

# 19. 许可与第三方说明

项目致谢和主要参考来源已置于 README 最前方。

`key-zhzr/BJUT-Auto-Login` 采用 MIT License；其相关许可与归属声明保留在：

```text
THIRD_PARTY_NOTICES
```

`sw1128/bjut_auth_linux` 作为早期 BJUT Linux 认证实现参考。本项目未将其声明为代码来源或许可来源。

本项目同样采用 MIT License，具体以仓库中的 `LICENSE` 为准。
