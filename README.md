# monitor-brightness

极简的 Windows 显示器亮度调节器。一个滑块，调完关掉。

通过 **DDC/CI** 协议直接控制显示器硬件亮度，效果和按显示器上的物理按键完全一样——
不是改 gamma、不是叠半透明遮罩那种糊弄人的做法。

## 特点

- **真·硬件亮度**：走 DDC/CI（VCP 0x10），系统重启后亮度保持
- **零依赖**：只用 Python 标准库（`ctypes` + `tkinter`），不用 pip 装任何东西
- **不常驻**：打开调完叉掉，不驻留后台、不写注册表、不改系统设置
- **不卡顿**：滑块拖动时不会每个像素都发一次指令，松手后 120ms 才写入

## 用法

双击 `run.bat`，或者：

```bash
python brightness.py
```

拖动滑块调亮度，按 `Esc` 或点关闭按钮退出。

## 环境要求

- Windows（仅 Windows，其他系统会直接退出）
- Python 3，且**必须带 tkinter**

验证你的 Python 有没有 tkinter：

```bash
python -c "import tkinter; print('OK')"
```

如果报 `ModuleNotFoundError: No module named 'tkinter'`，说明这个 Python 没装 tk。
解决办法二选一：

1. 换一个带 tkinter 的 Python 运行（`run.bat` 会自动在 PATH 里挑一个带 tkinter 的）
2. 重新安装 Python，安装时勾选 **tcl/tk and IDLE** 组件

## 显示器要求

显示器必须**开启 DDC/CI**。绝大多数显示器默认开启，如果程序提示"没有响应 DDC/CI 亮度查询"：

1. 按显示器上的物理按键调出 OSD 菜单
2. 找到 `DDC/CI` 选项（通常在「设置」或「其他」分类下）
3. 设为「开 / 启用」

另外注意：

- **DP 线**基本都能用；**HDMI** 部分显示器/显卡组合不支持 DDC/CI 写入
- 经过某些 KVM、转接器、扩展坞时可能失效
- 笔记本内屏不在 DDC/CI 管辖范围内，本工具只针对外接显示器

## 实现原理

```
tkinter 滑块
    └─> SetMonitorBrightness (dxva2.dll)    写
    └─<  GetMonitorBrightness (dxva2.dll)   读
            └─> DDC/CI (VCP code 0x10)  ──> 显示器 MCU
```

`dxva2.dll` 里封装了 DDC/CI 的读写，底层走显卡驱动 → 显示线缆 → 显示器的 I2C 通道，
直接改显示器内部背光亮度寄存器。

亮度值在显示器里的量程不一定是 0–100（有的显示器是 0–255），
本程序内部按 `GetMonitorBrightness` 返回的 min/max 做归一化，对外统一成 0–100 的百分比。

## 打包成 exe（可选）

```bash
pip install pyinstaller
pyinstaller --onefile --noconsole --name monitor-brightness brightness.py
```

## License

MIT
