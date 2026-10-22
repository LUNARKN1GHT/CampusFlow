// 启动页只负责展示主进程推送的状态，不直接访问 Node 或网络。
;(function () {
  var statusElement = document.querySelector('[data-testid="splash-status"]')
  var api = window.campusflow

  function render(status) {
    if (!status || !statusElement) return
    if (status.phase === 'failed') {
      statusElement.textContent = '启动失败，正在显示详细信息…'
      return
    }
    statusElement.textContent = status.message || '正在启动…'
  }

  if (api) {
    api.onStatus(render)
    api.getStatus().then(render).catch(function () {
      if (statusElement) statusElement.textContent = '无法读取启动状态，请查看日志。'
    })
  }
})()
