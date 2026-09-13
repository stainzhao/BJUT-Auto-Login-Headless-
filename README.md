# BJUT Auto Login (Headless)

北京工业大学（BJUT）校园网 Linux 无界面自动认证工具，适用于服务器、工作站、NAS 等 Headless 环境。

目标：无需 GUI 或浏览器，在校园网认证失效后自动检测并恢复联网。

当前版本：`0.4.0`

## 功能

- 支持 Type 1：宿舍网 ePortal
- 支持 Type 2：bjut_wifi / wlgn.bjut.edu.cn
- 支持 Type 3：有线 lgn.bjut.edu.cn
- 自动识别校园网接口
- 支持 IPv4 断线自动恢复
- Type 3 IPv6 健康监控与自动重新认证（独立开关，默认关闭）
- systemd timer + NetworkManager 事件触发
- 支持手动注销与重新认证（relogin）
- 支持定时重新认证（可选）
- 配置文件权限保护

## 登录 / 注销 / 重新认证技术路线图

```text
                    网络事件 / 约 60 秒定时检查
                               │
                               ▼
                      bjut-auth ensure
                               │
                 ┌─────────────┴─────────────┐
                 │                           │
                 ▼                           ▼
              IPv4 在线                   IPv4 离线
                 │                           │
                 │                     Portal 登录认证
                 │                           │
                 └─────────────┬─────────────┘
                               ▼
                      IPv6Watch.enabled ?
                 ┌─────────────┴─────────────┐
                 │                           │
              false                       true
                 │                           │
                 │                 Type 3 IPv6 健康检查
                 │              （其他类型直接结束本轮）
                 │                           │
                 │              ┌────────────┴────────────┐
                 │              │                         │
                 │           IPv6 正常                 IPv6 异常
                 │              │                         │
                 │              │                    连续失败计数
                 │              │                         │
                 │              │              未达到阈值 → 结束
                 │              │                         │
                 │              │              达到阈值（默认 2）
                 │              │                         │
                 │              │                         ▼
                 │              │                   bjut-relogin
                 │              │                         │
                 │              │                      logout
                 │              │                         │
                 │              │                      等待释放
                 │              │                         │
                 │              │                       login
                 │              │                         │
                 │              │                      公网确认
                 │              │                         │
                 └──────────────┴──────────────► 完成 ◄──┘

手动 relogin / bjut-auto-relogin.timer ─────► bjut-relogin
```

Type 3 IPv6 监控开启后同时确认：

1. 物理网卡仍存在 `2001:da8:216::/48` 的 BJUT 全局 IPv6；
2. Portal `getipv6` 返回的 IPv6 与本机地址一致。

## 安装

```bash
git clone https://github.com/stainzhao/BJUT-Auto-Login-Headless-.git
cd BJUT-Auto-Login-Headless-
sudo ./install.sh
```

无 systemd 环境：

```bash
sudo ./install.sh --no-systemd
```

安装位置：

```text
/usr/local/bin/bjut-auth
/usr/local/bin/bjut-relogin
/usr/local/bin/bjut-ipv6-watch
/etc/bjut-auto-login.conf
```

## 配置

```bash
sudo nano /etc/bjut-auto-login.conf
```

示例：

```ini
[BJUT]
username = your_username
password = your_password
type = auto
interface =
allow_http_fallback = false

[IPv6Watch]
# Type 3 IPv6 自动恢复独立开关，默认关闭
enabled = false
# 开启后，连续异常多少次才触发 relogin
failures = 2
# 两次自动恢复尝试之间的最短间隔（秒）
cooldown_seconds = 300
```

需要监控 Type 3 IPv6 时改为：

```ini
[IPv6Watch]
enabled = true
```

如果校园 IPv6 本身不可用、维护中或经常波动，保持 `enabled = false` 即可，IPv4 自动登录不会受影响。

配置文件权限：

```bash
sudo chmod 600 /etc/bjut-auto-login.conf
```

## 首次测试

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf doctor
sudo bjut-auth --config /etc/bjut-auto-login.conf ensure
sudo bjut-auth --config /etc/bjut-auto-login.conf status
```

Type 3 IPv6 监控开启后可单独检查：

```bash
sudo bjut-ipv6-watch --config /etc/bjut-auto-login.conf
```

正常时会看到类似：

```text
ipv6-watch: healthy: interface=enp7s0, ipv6=2001:da8:216:...
```

关闭时会看到：

```text
ipv6-watch: disabled
```

## 自动恢复

启用 IPv4 断线自动恢复：

```bash
sudo systemctl enable --now bjut-auto-login.timer
```

现有 timer 约每 60 秒执行一次 `ensure`。若 `[IPv6Watch] enabled = true`，每次 IPv4 检查/恢复后还会进行 Type 3 IPv6 健康检查；连续异常达到阈值后自动调用 `bjut-relogin`。

查看日志：

```bash
journalctl -u bjut-auto-login.service -f
```

## 手动注销与重新登录

注销：

```bash
sudo bjut-relogin --config /etc/bjut-auto-login.conf logout
```

重新登录：

```bash
sudo bjut-relogin --config /etc/bjut-auto-login.conf relogin
```

systemd 手动触发：

```bash
sudo systemctl start bjut-auto-relogin.service
```

## 定时重新认证（可选）

默认安装但不启用。

启用：

```bash
sudo systemctl enable --now bjut-auto-relogin.timer
```

默认每天 04:00 执行。

修改时间：

```bash
sudo systemctl edit bjut-auto-relogin.timer
```

例如每天 03:30：

```ini
[Timer]
OnCalendar=
OnCalendar=*-*-* 03:30:00
RandomizedDelaySec=5min
```

应用：

```bash
sudo systemctl daemon-reload
sudo systemctl restart bjut-auto-relogin.timer
```

关闭：

```bash
sudo systemctl disable --now bjut-auto-relogin.timer
```

## 更新已有安装

```bash
git pull
sudo ./install.sh
sudo systemctl daemon-reload
```

已有 `/etc/bjut-auto-login.conf` 不会覆盖。旧配置没有 `[IPv6Watch]` 时，IPv6 自动恢复保持关闭；需要时手动加入：

```ini
[IPv6Watch]
enabled = true
failures = 2
cooldown_seconds = 300
```

更新后建议测试：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf doctor
sudo bjut-ipv6-watch --config /etc/bjut-auto-login.conf
```

## 常用命令

|功能|命令|
|-|-|
|环境检查|`bjut-auth doctor`|
|立即认证|`bjut-auth ensure`|
|查看状态|`bjut-auth status`|
|IPv6 健康检查|`bjut-ipv6-watch`|
|注销|`bjut-relogin logout`|
|重新认证|`bjut-relogin relogin`|

## 致谢与许可

本项目的 Portal 协议实现和兼容策略参考：

- https://github.com/WuSiYu/BJUT-Auto-Login
- https://github.com/key-zhzr/BJUT-Auto-Login

感谢相关开源项目作者提供的协议研究与实现参考。

本项目仅用于个人学习与科研环境中的校园网自动认证，不包含任何账号信息，也不提供绕过认证或违规访问网络的功能。
