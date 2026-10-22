import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import CameraCapture from '../src/components/CameraCapture'
import type { VisionCandidate } from '../src/lib/types'

const CANDIDATE: VisionCandidate = {
  medicine_id: 42,
  name: 'Paracetamol 500mg',
  generic_name: 'Paracetamol',
  strength: '500mg',
  barcode: '8901234567890',
  match_type: 'barcode_exact',
  score: 100,
}

const CAPTURE = {
  capture_id: 'abc123',
  status: 'pending',
  ocr_text: '8901234567890',
  ocr_confidence: 93.5,
  engine: 'tesseract',
  engine_available: true,
  candidates: [CANDIDATE],
  matched_medicine_id: null,
  created_at: '2026-10-22T12:00:00Z',
  expires_at: '2026-11-21T12:00:00Z',
  confirmed_at: null,
  image_deleted: false,
}

function jsonResponse(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response
}

function fakeStream() {
  const stop = vi.fn()
  return { stream: { getTracks: () => [{ stop }] } as unknown as MediaStream, stop }
}

let fetchMock: ReturnType<typeof vi.fn>
let getUserMedia: ReturnType<typeof vi.fn>

beforeEach(() => {
  fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
  getUserMedia = vi.fn()
  Object.defineProperty(navigator, 'mediaDevices', {
    value: { getUserMedia },
    configurable: true,
    writable: true,
  })
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    drawImage: vi.fn(),
  } as unknown as CanvasRenderingContext2D)
  vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation(function (
    this: HTMLCanvasElement,
    callback: BlobCallback,
  ) {
    callback(new Blob(['frame'], { type: 'image/jpeg' }))
  })
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined as unknown as void)
})

function urlsOf(): string[] {
  return fetchMock.mock.calls.map((call) => String(call[0]))
}

describe('CameraCapture', () => {
  it('asks for the rear camera and shows the preview', async () => {
    const { stream } = fakeStream()
    getUserMedia.mockResolvedValue(stream)
    render(<CameraCapture />)

    await userEvent.click(screen.getByRole('button', { name: /start camera/i }))

    expect(getUserMedia).toHaveBeenCalledWith({ video: { facingMode: 'environment' } })
    expect(await screen.findByRole('button', { name: /capture frame/i })).toBeInTheDocument()
  })

  it('uploads the frame and shows the proposed candidates', async () => {
    const { stream } = fakeStream()
    getUserMedia.mockResolvedValue(stream)
    fetchMock.mockResolvedValue(jsonResponse(201, CAPTURE))

    render(<CameraCapture />)
    await userEvent.click(screen.getByRole('button', { name: /start camera/i }))
    await userEvent.click(screen.getByRole('button', { name: /capture frame/i }))

    await waitFor(() => expect(screen.getByText('Paracetamol 500mg')).toBeInTheDocument())
    expect(screen.getAllByText('8901234567890').length).toBeGreaterThan(0)
    expect(urlsOf()).toContain('/api/vision/captures')

    const call = fetchMock.mock.calls.find((c) => String(c[0]) === '/api/vision/captures')
    expect(call).toBeDefined()
    const body = (call![1] as RequestInit).body as FormData
    expect(body).toBeInstanceOf(FormData)
    expect(body.get('file')).toBeInstanceOf(Blob)
  })

  it('asks the human to confirm a candidate and reports the choice', async () => {
    const { stream } = fakeStream()
    getUserMedia.mockResolvedValue(stream)
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url) === '/api/vision/captures') return jsonResponse(201, CAPTURE)
      if (String(url).includes('/confirm')) return jsonResponse(200, { ...CAPTURE, status: 'confirmed' })
      return jsonResponse(200, {})
    })
    const picked = vi.fn()
    render(<CameraCapture onPicked={picked} />)

    await userEvent.click(screen.getByRole('button', { name: /start camera/i }))
    await userEvent.click(screen.getByRole('button', { name: /capture frame/i }))
    await screen.findByText('Paracetamol 500mg')

    await userEvent.click(screen.getByRole('button', { name: /^confirm$/i }))

    await waitFor(() => expect(urlsOf().some((u) => u === '/api/vision/captures/abc123/confirm')).toBe(true))
    const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/confirm'))
    expect(call).toBeDefined()
    expect(JSON.parse(String((call![1] as RequestInit).body))).toEqual({
      decision: 'confirm',
      medicine_id: 42,
    })
    await waitFor(() => expect(picked).toHaveBeenCalledWith(CANDIDATE, 'capture'))
  })

  it('keeps the manual entry usable when the OCR engine is unavailable', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url) === '/api/vision/captures') {
        return jsonResponse(503, {
          detail: 'The local OCR engine is not available on this server. Use the manual search instead.',
          code: 'ocr_unavailable',
        })
      }
      if (String(url) === '/api/vision/scan-text') return jsonResponse(200, [CANDIDATE])
      return jsonResponse(200, {})
    })
    const picked = vi.fn()
    render(<CameraCapture onPicked={picked} />)

    const { stream } = fakeStream()
    getUserMedia.mockResolvedValue(stream)
    await userEvent.click(screen.getByRole('button', { name: /start camera/i }))
    await userEvent.click(screen.getByRole('button', { name: /capture frame/i }))

    expect(await screen.findByText(/not available on this server/i)).toBeInTheDocument()

    await userEvent.type(screen.getByPlaceholderText(/e\.g\. 8901234567890 or Paracetamol/i), 'Paracetamol')
    await userEvent.click(screen.getByRole('button', { name: /^search$/i }))

    await waitFor(() => expect(screen.getByText('Paracetamol 500mg')).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: /use this medicine/i }))
    expect(picked).toHaveBeenCalledWith(CANDIDATE, 'manual')
  })

  it('never writes stock or creates a medicine', async () => {
    const { stream } = fakeStream()
    getUserMedia.mockResolvedValue(stream)
    fetchMock.mockResolvedValue(jsonResponse(201, CAPTURE))

    render(<CameraCapture />)
    await userEvent.click(screen.getByRole('button', { name: /start camera/i }))
    await userEvent.click(screen.getByRole('button', { name: /capture frame/i }))
    await screen.findByText('Paracetamol 500mg')
    await userEvent.click(screen.getByRole('button', { name: /^confirm$/i }))

    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(1))
    const allowed = [/^\/api\/vision\/status$/, /^\/api\/vision\/captures/, /^\/api\/vision\/scan-text$/]
    for (const url of urlsOf()) {
      expect(allowed.some((pattern) => pattern.test(url))).toBe(true)
    }
    // no inventory transaction and no medicine creation may be triggered
    expect(urlsOf().some((u) => u.includes('/api/inventory'))).toBe(false)
    expect(urlsOf().some((u) => u.includes('/api/medicines') && !u.includes('/api/medicines?'))).toBe(false)
  })

  it('stops the camera when the component is unmounted', async () => {
    const { stream, stop } = fakeStream()
    getUserMedia.mockResolvedValue(stream)
    const { unmount } = render(<CameraCapture />)

    await userEvent.click(screen.getByRole('button', { name: /start camera/i }))
    await screen.findByRole('button', { name: /capture frame/i })
    unmount()

    expect(stop).toHaveBeenCalled()
  })
})
