# ugos-app-template

绿联云 UGOS Pro 应用开发模板：**Go 后端 + 静态前端 + upk 打包校验**。

从零到可安装的 `.upk`，一条 `make pack` 完成编译、校验、打包、验收。

## 环境要求

| 依赖 | 说明 |
|---|---|
| [ugcli](https://developer.ugnas.com/doc/tools/ugcli.html) | 官方打包工具，需在 PATH 中，`ugcli --version` 可验证 |
| Go 1.21+ | 模板固定用 `GOTOOLCHAIN=go1.21.13` 编译，兼容性优于最新版 |
| Python 3.8+ | 仅用于 `tools/verify_upk.py`，无第三方依赖 |

## 目录结构

```
.
├── project.yaml                    # 应用配置（app_id / 端口 / 打开方式 / 权限）
├── Makefile                        # 编译、打包、校验一条龙
├── backend/
│   ├── main.go                     # Go HTTP 后端
│   └── go.mod
├── rootfs_common/
│   ├── icon.png                    # 应用图标（256x256 PNG）
│   └── www/index.html              # 前端静态页，由系统 Web 服务器托管
├── rootfs_amd64/bin/               # amd64 后端二进制（编译产物，不入库）
├── rootfs_arm64/bin/               # arm64 后端二进制（编译产物，不入库）
├── tools/verify_upk.py             # upk 逐层校验工具
└── build_dir/                      # 打包产物（不入库）
```

## 快速开始

```bash
# 编译两个架构 + 校验配置 + 清空 build_dir + 打包 + 验收
make pack BUILD=1

# 产物
ls build_dir/pkgs/upk/*.upk
```

`BUILD` 必须递增，同一 `version` 下不能重复（`0.1.0` + `BUILD=1` → `0.1.0.0001`）。

其他目标：

```bash
make build      # 只编译 linux amd64/arm64
make check      # 只跑 ugcli check
make verify     # 只校验已有 upk
make dev        # 本地起后端调试（默认 21010）
make clean      # 清理产物
```

## 安装到设备

1. 浏览器打开 NAS 管理页 → 应用中心 → 手动安装
2. **点「选择文件」从本地磁盘选取**，不要拖拽（拖拽易导致二进制被文本化损坏）
3. 安装前核对文件大小与 MD5：

```bash
md5 build_dir/pkgs/upk/amd64_nasync_com.example.demoapp_0.1.0.0001.upk
```

首次安装自建应用需要开发者授权：把设备序列号、MAC、管理员账号报给绿联申请，授权文件改名为 `ugdev.sig`，放到管理员账号「个人文件夹」根目录。

## 配置说明（project.yaml）

| 字段 | 本模板取值 | 说明 |
|---|---|---|
| `app_id` | `com.example.demoapp` | 发布后不可修改 |
| `version` | `0.1.0` | `x.y.z`，打包时追加 build 号 |
| `support_arch` | `amd64` `arm64` | Intel 机型用 amd64，ARM 机型用 arm64 |
| `product_series` | `nasync` | 绿联 NAS；HomeAgent 另打包 |
| `port` | `21010` | 系统以此端口可连作为启动成功判据 |
| `start_cmd` | `bin/demoapp_serv --port=21010` | 必须指向 **ELF 可执行文件** |
| `proxy_path` | `api` | 网关把 `/api/` 转发到后端 |
| `open_type` | `inner` | 桌面窗口，支持 JSSDK；`tab` 为浏览器标签页 |
| `permissions` | `NETWORK.ACCESS_INTERNET` | 声明网络权限，避免沙箱限制 |
| `depend_fw_version` | `1.13.0.0000` | 需与设备实际固件匹配 |

## 运行时规则（容易踩坑，务必先读）

这些是 UGOS Pro 的硬规则，违反任意一条都会表现为「装上但一直转圈」：

1. **启动成功的唯一判据是端口可连**。程序必须第一件事就 `net.Listen`，不要在任何可能失败的前置逻辑之后才监听。
2. **工作目录是 `data` 目录**，不是安装目录。定位静态文件要用 `UGAPP_INSTALL_DIR`，不能用相对路径。
3. **安装目录只读**。可写的只有 `data` / `log` / `cache` / `shared`。
4. **文件系统视图受限**：只能访问安装目录和 `/lib` `/sys` `/dev`。因此 `start_cmd` 指向 `#!/bin/sh` 脚本大概率失败——**必须直接指向 ELF 二进制**。
5. **stdout/stderr 会被系统收集**到 `/var/packages/{appid}/log/{appid}.log`，排查启动问题先看这里。
6. **进程以独立的非 root 用户运行**，收到 `SIGTERM` 需在 10 秒内退出，否则 `SIGKILL`。

环境变量：`UGAPP_INSTALL_DIR`、`UGAPP_DATA_DIR`、`UGAPP_LOG_DIR`、`UGAPP_CACHE_DIR`、`UGAPP_SHARED_DIR`。

## 打包产物校验

`ugcli pack` 成功不代表包可用。`make pack` 会自动跑 `tools/verify_upk.py`，逐层检查：

- 容器结构：`sig` / `pub` / `ico` / `ugb` / `obj` 段是否齐全、长度是否偏移（畸形包最典型特征）
- 载荷：`ugb → tar.gz → 内层 .ugb → xz → tar` 能否解开
- 包内自洽：`.check-app` 记录的每个文件 MD5 是否与内容一致
- 运行时：`execStart` 目标存在且带可执行位，proxy 目标与探测端口一致
- 对账：与同名 `product.json` 比对 `hash` / `size` / `version` / `arch` / `series`

单独使用：

```bash
python3 tools/verify_upk.py
python3 tools/verify_upk.py path/to/app.upk
```

### 两个必须知道的坑

- **不要用任何编辑器/预览工具打开 `.upk`**。二进制会被按文本处理并自动保存，体积膨胀（2.6MB → 4.8MB），设备必然报「无效的套件包」。校验脚本能通过 `product.json` 的 `hash` 对账发现这种损坏。
- **打包前必须清空 `build_dir`**。残留的中间产物可能产出体积异常的畸形包（2.6MB → 4.7MB），`make pack` 已内置这一步。

## 参考文档

- [ugcli 使用指南](https://developer.ugnas.com/doc/tools/ugcli.html)
- [project.yaml 配置](https://developer.ugnas.com/doc/tools/project-yaml.html)
- [运行环境](https://developer.ugnas.com/doc/backend/application/runtime-environment.html)
- [应用端口](https://developer.ugnas.com/doc/backend/application/app-port.html)
- [启动与停止](https://developer.ugnas.com/doc/backend/application/start-stop.html)
- [打开方式](https://developer.ugnas.com/doc/backend/application/open-type.html)
