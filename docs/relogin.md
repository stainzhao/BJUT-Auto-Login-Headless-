# 定时注销与重新认证

该功能作为现有 `ensure` 自动重连的补充：`ensure` 只在公网离线时认证；`relogin` 在当前在线时执行“注销 → 等待 → 重新认证”，如果触发时已经离线，则跳过注销并直接尝试恢复登录。

## 手动触发

```bash
sudo bjut-relogin --config /etc/bjut-auto-login.conf logout
sudo bjut-relogin --config /etc/bjut-auto-login.conf relogin
```

也可以通过 systemd 手动执行一次完整重新认证：

```bash
sudo systemctl start bjut-auto-relogin.service
sudo journalctl -u bjut-auto-relogin.service -n 50 --no-pager
```

> `logout` 只负责注销。如果 `bjut-auto-login.timer` 仍在运行，它可能在下一次检查时再次登录；需要刷新会话时应优先使用 `relogin`。

## 定时重新认证

安装脚本只安装 `bjut-auto-relogin.timer`，**不会自动启用**。默认计划为每天 04:00，另加最多 5 分钟随机延迟。

确认手动 `relogin` 正常后再执行：

```bash
sudo systemctl enable --now bjut-auto-relogin.timer
systemctl list-timers bjut-auto-relogin.timer
```

修改时间建议使用 systemd drop-in，而不是直接改 `/etc/systemd/system` 中安装的文件：

```bash
sudo systemctl edit bjut-auto-relogin.timer
```

例如改为每天 03:30：

```ini
[Timer]
OnCalendar=
OnCalendar=*-*-* 03:30:00
RandomizedDelaySec=5min
```

然后执行：

```bash
sudo systemctl daemon-reload
sudo systemctl restart bjut-auto-relogin.timer
```

## 并发保护

`relogin` 使用 `/run/lock/bjut-auto-login.lock`。现有 `bjut-auto-login.service` 也通过同一个 `flock` 锁运行，并在 `ProtectSystem=strict` 下显式允许写入 `/run/lock`，因此在“注销 → 等待 → 登录”期间，60 秒 `ensure` 不会抢先发起认证。

## 退出码

- `0`：操作成功；
- `1`：配置、网卡、Portal 请求或互斥锁错误；
- `2`：Portal 明确返回失败；
- `3`：注销后仍在线，或重新登录后仍无法访问公网。
