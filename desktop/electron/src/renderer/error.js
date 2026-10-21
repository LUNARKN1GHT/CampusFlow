// 错误页展示失败阶段、原因、日志路径与最近日志；重试只通过 preload 暴露的接口发起。
;(function () {
  var stageElement = document.querySelector('[data-testid="error-stage"]')
  var messageElement = document.querySelector('[data-testid="error-message"]')
  var pathElement = document.querySelector('[data-testid="error-log-path"]')
  var tailElement = document.querySelector('[data-testid="error-log-tail"]')
  var statusElement = document.querySelector('[data-testid="error-status"]')
  var retryButton = document.querySelector('[data-testid="retry"]')
  var openLogsButton = document.querySelector('[data-testid="open-logs"]')
  var api = window.campusflow

  var stageLabels = {
    postgres: 'PostgreSQL',
    migrate: '数据库迁移',
    api: 'API 服务',
    worker: '后台 Worker',
  }

  function render(status) {
    if (!status || !status.failure) return
    var failure = status.failure
    if (stageElement) {
      stageElement.textContent = '阶段：' + (stageLabels[failure.stage] || failure.stage)
    }
    if (messageElement) messageElement.textContent = '原因：' + failure.message
    if (pathElement) pathElement.textContent = failure.logPath || '未知'
    if (tailElement) tailElement.textContent = (failure.logTail || []).join('\n')
  }

  if (api) {
    api.onStatus(render)
    api.getStatus().then(render).catch(function () {
      if (statusElement) statusElement.textContent = '无法读取失败详情，请直接查看日志目录。'
    })

    if (retryButton) {
      retryButton.addEventListener('click', function () {
        retryButton.disabled = true
        if (statusElement) statusElement.textContent = '正在重新启动…'
        api.retry().catch(function () {
          retryButton.disabled = false
          if (statusElement) statusElement.textContent = '重试没有启动，请查看日志后再次尝试。'
        })
      })
    }

    if (openLogsButton) {
      openLogsButton.addEventListener('click', function () {
        api.openLogs().catch(function () {
          if (statusElement) statusElement.textContent = '无法打开日志目录，请按上方路径手动查看。'
        })
      })
    }
  } else if (statusElement) {
    statusElement.textContent = '预加载接口不可用，请查看日志目录。'
  }
})()
