import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '../src/lib/api'

vi.mock('../src/context/AuthContext', () => ({
  useAuth: () => ({ can: () => true }),
}))

function jsonResponse(status: number, body: unknown, withBody = true) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => (withBody ? body : {}),
  } as unknown as Response
}

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
})

describe('api client error handling', () => {
  it('keeps the backend message of a 503 that carries a detail', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(503, { detail: 'The local OCR engine is not available.', code: 'ocr_unavailable' }),
    )
    const error = (await api.post('/api/vision/captures').catch((err) => err)) as ApiError
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(503)
    expect(error.code).toBe('ocr_unavailable')
    expect(error.detail).toBe('The local OCR engine is not available.')
    expect(error.message).toBe('The local OCR engine is not available.')
  })

  it('treats a bodiless 502 as "backend unreachable"', async () => {
    fetchMock.mockResolvedValue(jsonResponse(502, {}, false))
    const error = (await api.get('/api/health').catch((err) => err)) as ApiError
    expect(error.code).toBe('backend_unavailable')
  })

  it('uploads a file without setting the content type', async () => {
    fetchMock.mockResolvedValue(jsonResponse(201, { capture_id: 'x' }))
    const form = new FormData()
    form.append('file', new Blob(['x'], { type: 'image/jpeg' }), 'capture.jpg')
    await api.postFile('/api/vision/captures', form)
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.body).toBe(form)
    expect((init.headers as Record<string, string>)['Content-Type']).toBeUndefined()
  })
})

describe('VisionScan page', () => {
  it('warns when the OCR engine is unavailable but keeps the manual entry', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url) === '/api/vision/status') {
        return jsonResponse(200, {
          enabled: true,
          engine: 'tesseract',
          engine_available: false,
          message: 'The local OCR engine is not available. Search manually instead.',
          max_upload_bytes: 5242880,
          retention_days: 30,
        })
      }
      return jsonResponse(200, {})
    })

    render(
      <MemoryRouter>
        <VisionScanLazy />
      </MemoryRouter>,
    )

    await waitFor(() =>
      expect(screen.getByText(/The local OCR engine is not available/i)).toBeInTheDocument(),
    )
    expect(screen.getByPlaceholderText(/e\.g\. 8901234567890 or Paracetamol/i)).toBeInTheDocument()
  })

  it('explains that the feature is missing when the backend does not offer it', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url) === '/api/vision/status') {
        return jsonResponse(404, { detail: 'Not Found' })
      }
      return jsonResponse(200, {})
    })

    render(
      <MemoryRouter>
        <VisionScanLazy />
      </MemoryRouter>,
    )

    await waitFor(() =>
      expect(screen.getByText(/not available on this server/i)).toBeInTheDocument(),
    )
    // no camera and no manual entry: the feature is not offered at all
    expect(screen.queryByRole('button', { name: /start camera/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^search$/i })).not.toBeInTheDocument()
    expect(screen.queryByText(/Not Found/i)).not.toBeInTheDocument()
    // the raw backend error text is not shown to the user
    expect(screen.getByRole('link', { name: /scan page/i })).toBeInTheDocument()
  })

  it('hides the feature when the backend reports it as disabled', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url) === '/api/vision/status') {
        return jsonResponse(200, {
          enabled: false,
          engine: 'tesseract',
          engine_available: true,
          message: 'Vision-assisted identification is disabled',
          max_upload_bytes: 5242880,
          retention_days: 30,
        })
      }
      return jsonResponse(200, {})
    })

    render(
      <MemoryRouter>
        <VisionScanLazy />
      </MemoryRouter>,
    )

    await waitFor(() =>
      expect(screen.getByText(/not available on this server/i)).toBeInTheDocument(),
    )
    expect(screen.queryByRole('button', { name: /start camera/i })).not.toBeInTheDocument()
  })

  it('does not offer the camera before the backend has answered', () => {
    let release: (value: Response) => void = () => {}
    fetchMock.mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          release = resolve
        }),
    )

    render(
      <MemoryRouter>
        <VisionScanLazy />
      </MemoryRouter>,
    )

    expect(screen.queryByRole('button', { name: /start camera/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^search$/i })).not.toBeInTheDocument()
    release(jsonResponse(200, {}))
  })

  it('shows the camera and the manual entry when the engine is available', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url) === '/api/vision/status') {
        return jsonResponse(200, {
          enabled: true,
          engine: 'tesseract',
          engine_available: true,
          message: null,
          max_upload_bytes: 5242880,
          retention_days: 30,
        })
      }
      return jsonResponse(200, {})
    })

    render(
      <MemoryRouter>
        <VisionScanLazy />
      </MemoryRouter>,
    )

    expect(await screen.findByRole('button', { name: /start camera/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^search$/i })).toBeInTheDocument()
    expect(screen.queryByText(/The local OCR engine is not available/i)).not.toBeInTheDocument()
  })
})

// imported lazily so the AuthContext mock is applied before the page renders
import VisionScanLazy from '../src/pages/VisionScan'
