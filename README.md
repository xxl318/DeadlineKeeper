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
需 Python 及 Tkinter，建议 Windows 环境。

```sh
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python deadlinekeeper.py
```

Windows 数据保存在 `%APPDATA%/DeadlineKeeper`；本仓库不包含真实任务或个人配置。

## 构建
运行 `Build_Windows_EXE.bat`，输出 `dist/DeadlineKeeper.exe`；检测到 Inno Setup 后可生成安装包。构建脚本会清理项目内已有构建产物，请仅在项目目录运行。

## 工程说明
主程序包含 TaskStore、ReminderEngine 和 Tkinter 界面组件；任务存储使用可重入锁，JSON 保存采用临时文件替换。此版本为单文件应用，后续可拆分存储、业务逻辑与界面模块。

## 验证范围
本次发布准备对源码进行语法检查，并在隔离临时目录验证日期解析、月末顺延、任务增改删与数据重载。未将此次检查表述为完整 GUI 或安装包端到端测试。
