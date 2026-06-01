const fs = require('fs')
const path = require('path')

module.exports = {
  version: "5.0",
  title: "Voc Studio",
  description: "一个将书籍或小说文本转换为有声书的工具：先用 LLM 生成带说话人和语音指令的标注脚本，再通过 TTS 生成语音行，最后合成为 MP3 有声书。",
  icon: "icon.png",
  menu: async (kernel, info) => {
    // Check running states
    let running = {
      install: info.running("install.js"),
      start: info.running("start.js"),
      reset: info.running("reset.js"),
      update: info.running("update.js")
    }

    // Check file existence states
    let installed = info.exists(".venv")

    // Handle running states first
    if (running.install) {
      return [{
        default: true,
        icon: "fa-solid fa-plug",
        text: "正在安装",
        href: "install.js"
      }]
    }

    if (running.start) {
      let local = info.local("start.js")
      if (local && local.url) {
        return [{
          default: true,
          icon: "fa-solid fa-rocket",
          text: "打开 Web 界面",
          href: local.url,
        }, {
          icon: "fa-solid fa-terminal",
          text: "终端",
          href: "start.js",
        }]
      } else {
        return [{
          default: true,
          icon: "fa-solid fa-terminal",
          text: "正在启动",
          href: "start.js",
        }]
      }
    }

    if (running.reset) {
      return [{
        default: true,
        icon: "fa-solid fa-rotate-left",
        text: "正在重置",
        href: "reset.js"
      }]
    }

    if (running.update) {
      return [{
        default: true,
        icon: "fa-solid fa-arrows-rotate",
        text: "正在更新",
        href: "update.js"
      }]
    }

    // STATE: NOT_INSTALLED - auto-run install
    if (!installed) {
      return [{
        default: true,
        icon: "fa-solid fa-plug",
        text: "安装",
        href: "install.js"
      }]
    }

    // STATE: INSTALLED
    return [{
      default: true,
      icon: "fa-solid fa-power-off",
      text: "启动",
      href: "start.js"
    }, {
      icon: "fa-solid fa-folder-open",
      text: "打开语音行目录",
      href: "voicelines"
    }, {
      icon: "fa-solid fa-arrows-rotate",
      text: "更新",
      href: "update.js"
    }, {
      icon: "fa-solid fa-plug",
      text: "重新安装",
      href: "install.js"
    }, {
      icon: "fa-solid fa-rotate-left",
      text: "重置",
      href: "reset.js"
    }]
  }
}
