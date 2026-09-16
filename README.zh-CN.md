[![English](https://img.shields.io/badge/English-555555?style=flat)](README.md) [![简体中文](https://img.shields.io/badge/简体中文-555555?style=flat)](README.zh-CN.md)

# zram-doctor

只读的 Linux 诊断 CLI，用于比较 `zram-generator.conf` 与实际运行的 zram 设备。修改配置后，可以用它确认变更是否真正生效；仅执行 `systemctl daemon-reload` 不会重新配置已经运行的设备。

## 检查内容

- 配置中声明、但系统中不存在的设备。
- 压缩算法、字面量大小和挂载点是否一致。
- 预期用于 swap、却未出现在 `swapon` 中的设备。
- 正在运行、但没有对应配置节的设备。

工具通过 `systemd-analyze cat-config` 获取合并后的配置，再读取 `zramctl` 和 `swapon` 的运行状态。支持文本和 JSON 报告。

## 安装与运行

需要 Linux、Python 3.9+，以及 util-linux 提供的 `zramctl`、`swapon`。建议安装 `systemd-analyze`，以正确合并配置 drop-in。Python 运行时不依赖第三方包。

```bash
git clone https://github.com/zhuhroscar-tech/zram-doctor.git
cd zram-doctor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
zram-doctor
zram-doctor --json
```

也可从 [Releases](https://github.com/zhuhroscar-tech/zram-doctor/releases) 下载独立 `.pyz`，运行前请核对对应版本的校验和。

退出码：`0` 表示没有 warning/failure 项，`1` 表示存在警告，`2` 表示配置中的设备缺失。**还应检查 JSON 中的 `tool_error`**：部分设备查询不完整的情况仍可能返回 `0`，不能只看退出码判断系统正常。

## 安全与限制

工具不会重启 unit、写入 sysfs 或修改配置，也不会发起网络请求或遥测。一般检查通常不需要 root。

大小比较只支持字面量；`min(ram / 2, 4096)` 这类表达式会标记为无法验证。若 `systemd-analyze cat-config` 不可用，备用方式只读取第一个可用的主配置文件，不合并 drop-in。`swapon` 缺失或查询失败时，swap 检查也可能不完整。

报告中的重启建议需要由你决定是否执行。重建正在使用的 zram 设备可能影响 swap 或已挂载的文件系统；操作前请确认内存余量和相关负载。

## 预览与开发

[输出截图](docs/images/example-output.png) · [演示视频](docs/demo.mp4)

```bash
python -m pip install -e ".[dev]"
python -m pytest -v
```

[实现](src/zram_doctor/core.py) · [CI](.github/workflows/ci.yml) · [MIT 许可证](LICENSE)
