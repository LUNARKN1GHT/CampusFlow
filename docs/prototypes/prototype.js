document.querySelectorAll('[data-state-switcher]').forEach((switcher) => {
  const targetName = switcher.getAttribute('data-state-switcher')
  const panels = document.querySelectorAll(`[data-state-panel="${targetName}"]`)
  switcher.querySelectorAll('button[data-state]').forEach((button) => {
    button.addEventListener('click', () => {
      const state = button.getAttribute('data-state')
      switcher.querySelectorAll('button[data-state]').forEach((peer) => {
        peer.setAttribute('aria-pressed', String(peer === button))
      })
      panels.forEach((panel) => {
        const values = panel.getAttribute('data-value').split(',')
        panel.classList.toggle('is-visible', values.includes(state))
      })
    })
  })
})
