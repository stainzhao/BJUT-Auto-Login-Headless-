# BJUT Auto Login (Headless)

北京工业大学（BJUT）校园网 Linux 无界面自动认证工具，适用于服务器、工作站、NAS 等 Headless 环境。

目标：无需 GUI 或浏览器，在校园网认证失效后自动检测并恢复联网。

当前版本：`0.4.0`

## 功能

- 支持 Type 1：宿舍网 ePortal
- 支持 Type 2：bjut_wifi / wlgn.bjut.edu.cn
- 支持 Type 3：有线 lgn.bjut.edu.cn
- 自动识别校园网接口
- 支持断线自动恢复
- systemd timer + NetworkManager 事件触发
- 支持手动注销与重新认证（relogin）
- 支持定时重新认证（可选）
- 配置文件权限保护

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
/etc/bjut-auto-login.conf
```

## 配置

编辑：

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
```

权限：

```bash
sudo chmod 600 /etc/bjut-auto-login.conf
```

## 首次测试

检查环境：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf doctor
```

测试认证：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf ensure
```

查看状态：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf status
```

## 自动恢复

启用断线自动恢复：

```bash
sudo systemctl enable --now bjut-auto-login.timer
```

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

也可以通过 systemd：

```bash
sudo systemctl start bjut-auto-relogin.service
```

## 定时重新认证（可选）

默认安装但不启用。

启用：

```bash
sudo systemctl enable --now bjut-auto-relogin.timer
```

默认每天 04:00 执行，用于刷新校园网认证会话。

关闭：

```bash
sudo systemctl disable --now bjut-auto-relogin.timer
```

查看状态：

```bash
systemctl status bjut-auto-relogin.timer
```

## 更新已有安装

更新代码：

```bash
git pull
sudo ./install.sh
sudo systemctl daemon-reload
```

已有 `/etc/bjut-auto-login.conf` 不会覆盖。

更新后建议测试：

```bash
sudo bjut-auth --config /etc/bjut-auto-login.conf doctor
sudo bjut-relogin --config /etc/bjut-auto-login.conf relogin
```

## 常用命令

|功能|命令|
|-|-|
|环境检查|`bjut-auth doctor`|
|立即认证|`bjut-auth ensure`|
|查看状态|`bjut-auth status`|
|注销|`bjut-relogin logout`|
|重新认证|`bjut-relogin relogin`|
|查看自动恢复日志|`journalctl -u bjut-auto-login.service`|
|查看重新认证日志|`journalctl -u bjut-auto-relogin.service`|

## 致谢与许可

本项目的 Portal 协议实现和兼容策略参考了以下开源项目：

- https://github.com/WuSiYu/BJUT-Auto-Login
- https://github.com/key-zhzr/BJUT-Auto-Login

感谢相关开源项目作者对北京工业大学校园网认证协议研究与实现提供的参考。

本项目仅用于个人学习与科研环境中的校园网自动认证，不包含任何账号信息，也不提供绕过认证或违规访问网络的功能。
