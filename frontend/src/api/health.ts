export async function checkApiHealth(): Promise<void> {
  const response = await fetch('/api/v1/health', {
    signal: AbortSignal.timeout(5000),
    cache: 'no-store',
  })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  const data: unknown = await response.json()
  if (
    typeof data !== 'object' || data === null ||
    !('status' in data) || data.status !== 'ok' ||
    !('service' in data) || data.service !== 'campusflow-api'
  ) {
    throw new Error('Unexpected health response')
  }
}
