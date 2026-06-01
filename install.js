module.exports = {
  run: [{
    method: "shell.run",
    params: {
      message: "uv cache clean"
    }
  }, {
    method: "shell.run",
    params: {
      message: "uv sync"
    }
  }, {
    method: "script.start",
    params: {
      uri: "torch.js",
      params: {
        path: ".",
        venv: ".venv",
        flashattention: true
      }
    }
  }, {
    method: "notify",
    params: {
      html: "安装完成！点击“启动”即可打开应用。"
    }
  }]
}
