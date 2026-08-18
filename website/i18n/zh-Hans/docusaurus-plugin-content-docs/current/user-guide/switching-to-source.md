---
sidebar_position: 8
title: 在桌面应用与源码安装之间切换
description: 从桌面应用迁移到源码检出，或反向迁移，数据不丢失。
---

# 在桌面应用与源码安装之间切换

Hermes 以两种打包形态运行同一个智能体：

- **桌面应用** — 智能体运行在应用密封资源内部。应用通过自身的更新器
  更新自己（连同智能体）。
- **源码安装** — 由安装脚本（Windows 上为 Hermes Setup）创建、
  `hermes update` 管理的 git 检出。

你的数据不在任何一种形态内部。会话、配置、记忆、技能和 API 密钥都在
`~/.hermes`（Windows 上是 `%USERPROFILE%\.hermes`），它位于所有应用容器
之外，本页每个步骤都不会影响它。卸载一种形态、安装另一种，改变的只是
*运行哪份代码*，不会改变 Hermes 记得什么。

## 桌面应用 → 源码安装

1. **退出 Hermes Desktop。**

2. **卸载应用，保留数据。** 应用自身的卸载界面（设置 → 关于）在打包安装
   上只会移除用户数据 — 移除应用本身由操作系统负责：

   - **Windows：** 设置 → 应用 → 安装的应用 → Hermes → 卸载。
     NSIS 卸载器只移除应用；`%USERPROFILE%\.hermes` 不受影响。
   - **Windows（Microsoft Store 安装）：** 从 Store 或设置 → 应用卸载。
     Store 容器不包含 `~/.hermes`。
   - **macOS：** 退出应用，把 Hermes.app 从「应用程序」拖到废纸篓。
     （`~/.hermes` 在你的主目录，不在 .app 内。）
   - **Linux：** 删除 AppImage 文件（或应用目录）。

3. **从源码安装。**

   - **Linux / macOS：**

     ```bash
     curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
     ```

   - **Windows：** 从[官网](https://hermes-agent.nousresearch.com/)下载并
     运行 Hermes Setup，或使用网站上的 PowerShell 一行命令。两者驱动同一
     套安装引擎。

   安装器在 `~/.hermes/hermes-agent` 创建检出，并写入自更新标记
   （此后 `hermes update` 管理它），你已有的数据原地可用。

## 源码安装 → 桌面应用

四步：

1. 运行 `hermes uninstall`（保留 `~/.hermes`，除非加 `--data`）。
2. 从[下载页](https://hermes-agent.nousresearch.com/)下载对应系统的桌面应用。
3. 安装并启动。
4. 完成 — 应用会在 `~/.hermes` 找到你的数据。

## 我现在运行的是哪种？

```bash
hermes update --install-id   # 打印本安装的 id 和路径
hermes version               # 打印安装方式
```

密封的桌面树会拒绝 `hermes update` 并指向本页；源码检出就地更新。
更新通道（`hermes update --set-channel main|stable|nightly`）按安装记录，
同一台机器上的桌面应用和源码检出各自独立跟踪自己的通道。
