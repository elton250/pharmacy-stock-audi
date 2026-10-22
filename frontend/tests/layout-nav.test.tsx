import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const canMock = vi.fn()

vi.mock('../src/context/AuthContext', () => ({
  useAuth: () => ({
    user: { username: 'tester', full_name: 'Tester', roles: [] },
    can: (perm: string) => canMock(perm),
    logout: vi.fn(),
  }),
}))

import Layout from '../src/components/Layout'

function jsonResponse(status: number, body: unknown, withBody = true) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => (withBody ? body : {}),
  } as unknown as Response
}

const ENABLED = {
  enabled: true,
  engine: 'tesseract',
  engine_available: true,
  message: null,
  max_upload_bytes: 5242880,
  retention_days: 30,
}

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
  canMock.mockReset()
  canMock.mockImplementation(() => true)
})

function renderLayout() {
  return render(
    <MemoryRouter>
      <Routes>
        <Route path="/" element={<Layout />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('navigation: the vision entry follows the backend', () => {
  it('is hidden when the backend does not offer the feature (404)', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url) === '/api/vision/status') return jsonResponse(404, { detail: 'Not Found' })
      return jsonResponse(200, {})
    })

    renderLayout()

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(screen.queryByRole('link', { name: /vision scan/i })).not.toBeInTheDocument()
    // the rest of the navigation stays untouched
    expect(screen.getByRole('link', { name: /^medicines$/i })).toBeInTheDocument()
  })

  it('is hidden when the backend cannot be reached', async () => {
    fetchMock.mockRejectedValue(new Error('network down'))

    renderLayout()

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(screen.queryByRole('link', { name: /vision scan/i })).not.toBeInTheDocument()
  })

  it('is hidden when the backend reports the feature as disabled', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url) === '/api/vision/status') {
        return jsonResponse(200, { ...ENABLED, enabled: false })
      }
      return jsonResponse(200, {})
    })

    renderLayout()

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(screen.queryByRole('link', { name: /vision scan/i })).not.toBeInTheDocument()
  })

  it('is shown when the backend confirms that the feature is enabled', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url) === '/api/vision/status') return jsonResponse(200, ENABLED)
      return jsonResponse(200, {})
    })

    renderLayout()

    expect(await screen.findByRole('link', { name: /vision scan/i })).toBeInTheDocument()
  })

  it('is not requested at all for a user without the medicine:view permission', async () => {
    canMock.mockImplementation((perm: string) => perm !== 'medicine:view')

    renderLayout()

    await waitFor(() => expect(screen.getByRole('link', { name: /^dashboard$/i })).toBeInTheDocument())
    const visionCalls = fetchMock.mock.calls.filter(
      (call) => String(call[0]) === '/api/vision/status',
    )
    expect(visionCalls).toHaveLength(0)
    expect(screen.queryByRole('link', { name: /vision scan/i })).not.toBeInTheDocument()
  })
})
