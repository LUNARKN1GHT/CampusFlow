import { contextBridge, ipcRenderer } from 'electron'

import type { SupervisorStatus } from './types'

/**
 * 只暴露三个最小方法：读取状态、重试启动、打开日志目录。
 * 不暴露 ipcRenderer、require、process 或任何 Node 原语。
 */
const api = {
  getStatus: (): Promise<SupervisorStatus> => ipcRenderer.invoke('campusflow:get-status'),
  retry: (): Promise<void> => ipcRenderer.invoke('campusflow:retry'),
  openLogs: (): Promise<string> => ipcRenderer.invoke('campusflow:open-logs'),
  /** 仅用于外部 OAuth：主进程会再次校验 HTTPS 来源白名单。 */
  openExternal: (url: string): Promise<boolean> =>
    ipcRenderer.invoke('campusflow:open-external', url),
  onStatus: (callback: (status: SupervisorStatus) => void): (() => void) => {
    const listener = (_event: Electron.IpcRendererEvent, status: SupervisorStatus): void => {
      callback(status)
    }
    ipcRenderer.on('campusflow:status', listener)
    return () => ipcRenderer.removeListener('campusflow:status', listener)
  },
}

contextBridge.exposeInMainWorld('campusflow', api)
