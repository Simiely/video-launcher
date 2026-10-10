# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['视频方案启动器.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='VideoLauncher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX 压缩会把 PyInstaller 单文件 exe 改得"像自解压壳"，是杀软误报的主要诱因之一
    # （UPX 压缩体被安全软件判为可疑 → 进程被拦/被杀，表现就是"打开闪一下就关"）。
    # 关掉它：体积略增，但换来"不被误杀"和更稳的引导流程。
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
