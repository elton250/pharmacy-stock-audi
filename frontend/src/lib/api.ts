// Typed API client with cookie sessions + CSRF double-submit.

export class ApiError extends Error {
  status: number
  code: string
  detail: string
  constructor(status: number, detail: string, code: string) {
    super(detail)
    this.status = status
    this.code = code
    this.detail = detail
  }
}

function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'))
  return match ? decodeURIComponent(match[1]) : null
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (method !== 'GET') {
    const csrf = readCookie('pharmacy_csrf')
    if (csrf) headers['X-CSRF-Token'] = csrf
  }
  let res: Response
  try {
    res = await fetch(path, {
      method,
      headers,
      credentials: 'include',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch {
    // Network level failure (backend down, DNS, connection refused, offline).
    throw new ApiError(
      0,
      'The backend is not reachable. Start it with: scripts/start-dev.sh (see scripts/check-health.sh for details).',
      'backend_unavailable',
    )
  }
  if (res.status === 204) return undefined as T
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    // A 502/503/504 *without* a body comes from the proxy (the backend is
    // down). A 503 that carries a detail is a deliberate backend answer
    // (e.g. the OCR engine is unavailable) and must keep its own message.
    if ((res.status === 502 || res.status === 503 || res.status === 504) && !data.detail) {
      throw new ApiError(
        res.status,
        'The backend is not reachable. Start it with: scripts/start-dev.sh (see scripts/check-health.sh for details).',
        'backend_unavailable',
      )
    }
    throw new ApiError(res.status, data.detail || res.statusText, data.code || 'error')
  }
  return data as T
}

async function requestForm<T>(method: string, path: string, form: FormData): Promise<T> {
  // The browser sets the multipart boundary itself — Content-Type must not be set here.
  const headers: Record<string, string> = {}
  if (method !== 'GET') {
    const csrf = readCookie('pharmacy_csrf')
    if (csrf) headers['X-CSRF-Token'] = csrf
  }
  let res: Response
  try {
    res = await fetch(path, {
      method,
      headers,
      credentials: 'include',
      body: form,
    })
  } catch {
    throw new ApiError(
      0,
      'The backend is not reachable. Start it with: scripts/start-dev.sh (see scripts/check-health.sh for details).',
      'backend_unavailable',
    )
  }
  if (res.status === 204) return undefined as T
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    // A 502/503/504 *without* a body comes from the proxy (the backend is
    // down). A 503 that carries a detail is a deliberate backend answer
    // (e.g. the OCR engine is unavailable) and must keep its own message.
    if ((res.status === 502 || res.status === 503 || res.status === 504) && !data.detail) {
      throw new ApiError(
        res.status,
        'The backend is not reachable. Start it with: scripts/start-dev.sh (see scripts/check-health.sh for details).',
        'backend_unavailable',
      )
    }
    throw new ApiError(res.status, data.detail || res.statusText, data.code || 'error')
  }
  return data as T
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
  postFile: <T>(path: string, form: FormData) => requestForm<T>('POST', path, form),
  patch: <T>(path: string, body?: unknown) => request<T>('PATCH', path, body),
  put: <T>(path: string, body?: unknown) => request<T>('PUT', path, body),
  del: <T>(path: string) => request<T>('DELETE', path),
}

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}
