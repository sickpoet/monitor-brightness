# -*- coding: utf-8 -*-
"""一键打包 monitor-brightness 为单文件 exe。

做四件事：
1. 从 brightness.py 读 __version__，作为唯一的版本号来源
2. 生成 icon.ico（调用 make_icon.py，商品化图标不入库）
3. 生成 PyInstaller 需要的版本资源文件，让 exe 右键属性里有版本号
4. 调 PyInstaller 打包成 --onefile --windowed

必须用带 tkinter 的 Python 运行本脚本（打包的是 GUI 程序）。
脚本会自检，缺 tkinter 直接报错而不是打出一个跑不起来的 exe。

UPX 默认关闭。只省约 1.5MB（11MB -> 9.2MB），但加壳会拉高国内杀软的误报率。
确实想要时加 --upx，目录优先看 --upx-dir，其次环境变量 UPX_DIR，最后找 PATH。
注意 PyInstaller 只在「命令行参数」里认 upx-dir，写在 spec 里无效。

用法：
    python build_exe.py
    python build_exe.py --onedir
    python build_exe.py --upx --upx-dir "C:\\path\\to\\upx"
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENTRY = os.path.join(HERE, "brightness.py")
ICON = os.path.join(HERE, "icon.ico")
APP_NAME = "monitor-brightness"
BUILD_DIR = os.path.join(HERE, "build")
DIST_DIR = os.path.join(HERE, "dist")


def die(msg):
    sys.stderr.write("[错误] %s\n" % msg)
    raise SystemExit(1)


def read_version():
    """从入口文件读 __version__，保证版本号只有一个来源。"""
    with open(ENTRY, "r", encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', src, re.M)
    if not m:
        die("在 brightness.py 中找不到 __version__")
    return m.group(1)


def check_env():
    """确认当前解释器能打包出可用的 GUI 程序。"""
    try:
        import tkinter  # noqa: F401
    except ImportError:
        die(
            "当前 Python 没有 tkinter，打出来的 exe 无法运行。\n"
            "请换用带 tkinter 的解释器，例如：\n"
            '  "C:\\Program Files\\Python312\\python.exe" build_exe.py'
        )
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        die("当前 Python 没有 PyInstaller：pip install pyinstaller")


def make_version_file(version):
    """生成 PyInstaller 版本资源文件，返回路径。"""
    parts = version.split(".")
    while len(parts) < 4:
        parts.append("0")
    quad = ", ".join(str(int(p)) for p in parts[:4])

    content = """# -*- coding: utf-8 -*-
# 由 build_exe.py 自动生成，请勿手工编辑。
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(%s),
    prodvers=(%s),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '080404b0',
          [StringStruct('CompanyName', 'sickpoet'),
           StringStruct('FileDescription', '显示器亮度调节器'),
           StringStruct('FileVersion', '%s'),
           StringStruct('InternalName', '%s'),
           StringStruct('LegalCopyright', 'MIT License'),
           StringStruct('OriginalFilename', '%s.exe'),
           StringStruct('ProductName', '%s'),
           StringStruct('ProductVersion', '%s')])
      ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
""" % (quad, quad, version, APP_NAME, APP_NAME, APP_NAME, version)

    path = os.path.join(HERE, "version_info.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def find_upx(explicit=None):
    """定位 UPX 目录，找不到返回 None。"""
    if explicit:
        if os.path.isfile(os.path.join(explicit, "upx.exe")):
            return explicit
        die("--upx-dir 下没有 upx.exe：%s" % explicit)

    env_dir = os.environ.get("UPX_DIR")
    if env_dir and os.path.isfile(os.path.join(env_dir, "upx.exe")):
        return env_dir

    found = shutil.which("upx")
    if found:
        return os.path.dirname(found)

    return None


def clean_previous():
    """清掉上次的 build/dist，避免旧文件混进新产物。

    不用 PyInstaller 的 --clean：那个会批量删缓存，容易触发安全拦截。
    """
    for path in (BUILD_DIR, DIST_DIR):
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="打包 monitor-brightness 为单文件 exe")
    parser.add_argument("--upx", action="store_true",
                        help="启用 UPX 压缩（体积小约 15%%，但加壳容易被杀软误报，默认关闭）")
    parser.add_argument("--upx-dir", default=None,
                        help="UPX 所在目录，指定即隐含启用 UPX")
    parser.add_argument("--onedir", action="store_true",
                        help="打成目录版而非单文件。启动与退出快得多，代价是要整文件夹一起分发")
    parser.add_argument("--keep-console", action="store_true", help="保留控制台，用于排查启动问题")
    args = parser.parse_args()

    check_env()
    version = read_version()
    print("[1/4] 版本号: %s" % version)

    # 图标：始终重新生成，保证和代码里的配色同步
    try:
        import make_icon
        make_icon.build(ICON)
        print("[2/4] 图标已生成: %s" % ICON)
    except ImportError:
        if os.path.isfile(ICON):
            print("[2/4] 未装 Pillow，沿用已有图标: %s" % ICON)
        else:
            print("[2/4] 未装 Pillow 且无图标，使用 PyInstaller 默认图标")

    version_file = make_version_file(version)
    print("[3/4] 版本资源: %s" % version_file)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir" if args.onedir else "--onefile",
        "--name", APP_NAME,
        "--distpath", DIST_DIR,
        "--workpath", BUILD_DIR,
        "--specpath", BUILD_DIR,
        "--exclude-module", "PIL",
        "--exclude-module", "numpy",
        "--exclude-module", "pytest",
        "--exclude-module", "setuptools",
        "--exclude-module", "pip",
    ]
    if not args.keep_console:
        cmd.append("--windowed")
    if os.path.isfile(ICON):
        cmd += ["--icon", ICON]
    if os.path.isfile(version_file):
        cmd += ["--version-file", version_file]

    # UPX 默认关闭：只省约 1.5MB，却可能让国内杀软报毒，不划算
    want_upx = args.upx or bool(args.upx_dir)
    upx_dir = find_upx(args.upx_dir) if want_upx else None
    if upx_dir:
        cmd += ["--upx-dir", upx_dir]
        print("      UPX: %s" % upx_dir)
    elif want_upx:
        print("      UPX: 未找到 upx.exe，本次不压缩")
    else:
        print("      UPX: 未启用（默认关闭）")

    cmd.append(ENTRY)

    clean_previous()
    print("[4/4] 开始打包 ...")
    proc = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout or "")
        sys.stderr.write(proc.stderr or "")
        die("PyInstaller 打包失败，返回码 %d" % proc.returncode)

    if args.onedir:
        exe = os.path.join(DIST_DIR, APP_NAME, APP_NAME + ".exe")
    else:
        exe = os.path.join(DIST_DIR, APP_NAME + ".exe")
    if not os.path.isfile(exe):
        die("打包结束但找不到产物：%s" % exe)

    size_mb = os.path.getsize(exe) / 1048576.0
    print("\n完成: %s  (%.1f MB)" % (exe, size_mb))
    if args.onedir:
        total = 0
        for root, _dirs, files in os.walk(os.path.dirname(exe)):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        print("      目录版合计 %.1f MB（需整个文件夹一起分发）" % (total / 1048576.0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
