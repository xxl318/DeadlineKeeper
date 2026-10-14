# DeadlineKeeper

基于 Python / Tkinter 的本地桌面任务与截止日期管理工具，包含提醒、归档和桌宠激励功能。

## 功能
- 任务新增、编辑、删除及子任务完成状态联动。
- 灵活日期输入、周期任务截止日期顺延与后台提醒。
- 本地 JSON 持久化、原子写入及损坏文件备份。
- 已完成任务归档、CSV / Excel 导出。
- 桌宠、积分、每日打卡与装扮交互。
- Windows 高 DPI 适配及 PyInstaller / Inno Setup 打包脚本。

## 运行
需 Python 3.10 或更高版本及 Tkinter，建议 Windows 环境。

```sh
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python deadlinekeeper.py
```

Windows 数据保存在 `%APPDATA%/DeadlineKeeper`；本仓库不包含真实任务或个人配置。

## 构建
双击 `Build_Windows_EXE.bat`，成功后直接运行 `dist/DeadlineKeeper.exe`，无需每次重新打包。

- 脚本自动切换至自身所在目录，先验证 `python` 能否运行，再尝试 `py -3`，避免选中损坏的 Windows 启动别名。
- 使用项目内独立的 `.venv` 安装依赖，包含桌宠图片所需的 Pillow。
- 包含完整 assets 资源；不再预先删除 build、dist 或 Output 目录，同名构建文件仍可能被覆盖。
- 安装 Inno Setup 6 后可生成 `Output/DeadlineKeeper-Setup.exe`；未安装时跳过安装包，免安装 EXE 仍可使用。
- `Build_Windows_EXE.bat --probe` 仅检查解释器；`--no-pause` 用于自动构建。

### 常见提示
- `Requirement already satisfied` 表示依赖已安装，不是错误。
- pip 更新提示不影响构建，无需为此升级。
- 出现 `The system cannot execute the specified program` 时，请使用更新后的脚本；原脚本仅检查启动器是否存在，没有验证其能否执行。
- 若提示已有虚拟环境无效，将项目内 `.venv` 改名备份后重试，不要移动或删除应用数据目录。
- 打包失败时查看项目目录的 `build.log`。

### 构建验证（2026-10-14）
Windows / Python 3.14.5 下完成虚拟环境创建、依赖安装及 PyInstaller 构建，退出码为 0，生成 `dist/DeadlineKeeper.exe`。未执行完整 GUI 测试；本机未安装 Inno Setup，安装包生成步骤未验证。

## 工程说明
主程序包含 TaskStore、ReminderEngine 和 Tkinter 界面组件；任务存储使用可重入锁，JSON 保存采用临时文件替换。此版本为单文件应用，后续可拆分存储、业务逻辑与界面模块。

## 验证范围
本次发布准备对源码进行语法检查，并在隔离临时目录验证日期解析、月末顺延、任务增改删与数据重载。未将此次检查表述为完整 GUI 或安装包端到端测试。
