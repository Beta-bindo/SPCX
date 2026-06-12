# XAU助手

BA (Binance) 与 MT5 跨平台黄金点差监控桌面应用。

## 功能

- 深色交易终端风格 UI（PySide6 + QSS）
- 实时点差监控（数字面板，无走势图）
- BA 订单簿展示
- 持仓与盈亏面板
- 演示模式（无需 API / MT5 即可体验）
- 实盘模式：Binance API + MT5 终端

## 本地开发运行

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt          # macOS / Linux 开发
pip install -r requirements-windows.txt  # Windows 打包（含 MT5）
python main.py
```

## Windows 打包成 exe

> **重要**：你当前在 **Mac** 上，PyInstaller **不能** 在 macOS 上直接生成 Windows 的 `.exe`。  
> 任选其一：**Windows 电脑本地打包** 或 **GitHub 云端自动打包**。

### 方式 A：在 Windows 电脑上打包（推荐）

1. 把整个 `BS` 文件夹拷到 Windows（U 盘、网盘均可）
2. 安装 [Python 3.10+](https://www.python.org/downloads/)（勾选 Add to PATH）
3. 在项目目录打开 **cmd**，运行：

```bat
build.bat
```

4. 完成后得到：

| 路径 | 说明 |
|------|------|
| `dist\XAUAssistant\XAUAssistant.exe` | 主程序 |
| `dist\XAUAssistant\` 整个文件夹 | 需整夹拷贝分发，用户无需装 Python |

5. **可选**安装包：先装 [Inno Setup 6](https://jrsoftware.org/isinfo.php)，再运行 `build_installer.bat`  
   输出：`installer_output\XAUAssistant_Setup_1.0.0.exe`（用户双击安装）

### 方式 B：没有 Windows？用 GitHub 自动打包

1. 把项目推到 GitHub 仓库
2. 打开仓库 **Actions** → 选择 **Build Windows EXE** → **Run workflow**
3. 完成后在 **Artifacts** 下载 `XAUAssistant-Windows.zip`，解压即含 `XAUAssistant.exe`

## 关于「所有依赖都在程序里」

| 内容 | 是否内置 |
|------|----------|
| Python 运行时 | ✅ PyInstaller 已打包 |
| PySide6 | ✅ 已打包 |
| python-binance | ✅ 已打包 |
| MetaTrader5 Python 库 | ✅ 已打包 |
| **MetaTrader 5 终端程序** | ❌ **无法内置** |

### MT5 特别说明

MT5 是经纪商提供的独立交易终端，受授权与安全限制，**不能** 打进 exe 里。

实盘使用 MT5 时，用户电脑仍需：

1. 安装 [MetaTrader 5](https://www.metatrader5.com/)
2. 登录经纪商账户（如 Exness）
3. 在本软件中填写 MT5 账户、密码、服务器

若未安装 MT5，程序会自动回退到 **演示模式**。

### Binance 实盘

1. 连接模式选「实盘 · BA + MT5」或「仅 BA」
2. 填写 API Key / Secret
3. 如需代理，勾选并填写 `127.0.0.1:7890`

## 项目结构

```
BS/
├── main.py                 # 入口
├── app/
│   ├── main_window.py      # 主窗口
│   ├── styles/dark.qss     # 深色主题
│   ├── widgets/            # UI 组件
│   ├── core/               # 配置与引擎
│   └── connectors/         # BA / MT5 连接器
├── build.spec              # PyInstaller 配置
├── build.bat               # Windows 打包脚本
├── build_installer.bat     # 安装包脚本
└── installer/setup.iss     # Inno Setup 脚本
```

## 配置存储

用户配置保存在：`%USERPROFILE%\.xau_assistant\config.json`
