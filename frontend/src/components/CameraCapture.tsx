import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { VisionCandidate, VisionCapture } from '../lib/types'
import { Alert, Field } from './ui'

interface Props {
  /** Called when a human picked a medicine (camera capture or manual entry). */
  onPicked?: (candidate: VisionCandidate, source: 'capture' | 'manual') => void
}

const MATCH_LABEL: Record<VisionCandidate['match_type'], string> = {
  barcode_exact: 'barcode match',
  barcode_partial: 'partial barcode match',
  name: 'name match',
}

/**
 * Camera capture with local OCR.
 *
 * The frame is sent to the backend, recognised locally and turned into
 * *candidates*. A human always confirms the result — nothing is ever created
 * or written to stock automatically. The manual entry below stays available
 * in every situation (no camera, no OCR engine, unreadable picture).
 */
export default function CameraCapture({ onPicked }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const [cameraOn, setCameraOn] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [candidates, setCandidates] = useState<VisionCandidate[]>([])
  const [captureId, setCaptureId] = useState<string | null>(null)
  const [ocrText, setOcrText] = useState('')
  const [manualText, setManualText] = useState('')

  const stopTracks = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
  }, [])

  const stopCamera = useCallback(() => {
    stopTracks()
    if (videoRef.current) videoRef.current.srcObject = null
    setCameraOn(false)
  }, [stopTracks])

  // Never leave the camera running when the page goes away.
  useEffect(() => stopTracks, [stopTracks])

  const startCamera = async () => {
    setError(null)
    setNotice(null)
    if (!navigator.mediaDevices?.getUserMedia) {
      setError('This browser does not offer a camera. Use the manual entry below.')
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'environment' },
      })
      streamRef.current = stream
      if (videoRef.current) {
        videoRef.current.srcObject = stream
        try {
          await videoRef.current.play()
        } catch {
          /* autoplay restrictions — the frame is still captured */
        }
      }
      setCameraOn(true)
    } catch {
      setError('The camera could not be started (no permission or no device). Use the manual entry below.')
    }
  }

  const captureFrame = async () => {
    const video = videoRef.current
    if (!video) return
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const canvas = document.createElement('canvas')
      canvas.width = video.videoWidth || 640
      canvas.height = video.videoHeight || 480
      const context = canvas.getContext('2d')
      if (!context) throw new ApiError(0, 'The camera frame could not be read.', 'capture_failed')
      context.drawImage(video, 0, 0, canvas.width, canvas.height)
      const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.9))
      if (!blob) throw new ApiError(0, 'The camera frame could not be encoded.', 'capture_failed')

      const form = new FormData()
      form.append('file', blob, 'capture.jpg')
      const capture = await api.postFile<VisionCapture>('/api/vision/captures', form)
      setCaptureId(capture.capture_id)
      setCandidates(capture.candidates)
      setOcrText(capture.ocr_text)
      if (capture.candidates.length === 0) {
        setNotice('Nothing in the picture could be matched. Try a closer picture or use the manual entry.')
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The picture could not be processed.')
    } finally {
      setBusy(false)
    }
  }

  const confirmCandidate = async (candidate: VisionCandidate) => {
    if (!captureId) return
    setBusy(true)
    setError(null)
    try {
      await api.post(`/api/vision/captures/${captureId}/confirm`, {
        decision: 'confirm',
        medicine_id: candidate.medicine_id,
      })
      setCandidates([])
      setCaptureId(null)
      setOcrText('')
      setNotice(`Confirmed: ${candidate.name}. The picture was deleted.`)
      onPicked?.(candidate, 'capture')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The identification could not be confirmed.')
    } finally {
      setBusy(false)
    }
  }

  const searchManual = async () => {
    const text = manualText.trim()
    if (!text) return
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const found = await api.post<VisionCandidate[]>('/api/vision/scan-text', { text })
      setCaptureId(null)
      setOcrText('')
      setCandidates(found)
      if (found.length === 0) {
        setNotice('No medicine matches that text. Check the spelling or search in the medicine list.')
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The search failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <div className="card">
        <h2>Camera</h2>
        <p className="muted">
          The picture is recognised on this server, never in the cloud. Nothing is created and no stock is
          written — you confirm the medicine yourself.
        </p>
        <video ref={videoRef} className="camera-preview" muted playsInline data-testid="camera-preview" />
        <div className="btn-row mt">
          {cameraOn ? (
            <>
              <button className="btn" onClick={() => void captureFrame()} disabled={busy}>
                {busy ? 'Reading picture…' : 'Capture frame'}
              </button>
              <button className="btn btn-ghost" onClick={stopCamera} disabled={busy}>
                Stop camera
              </button>
            </>
          ) : (
            <button className="btn" onClick={() => void startCamera()} disabled={busy}>
              Start camera
            </button>
          )}
        </div>
      </div>

      {error && <Alert kind="error">{error}</Alert>}
      {notice && <Alert kind="info">{notice}</Alert>}

      {ocrText && (
        <div className="card">
          <h2>Recognised text</h2>
          <div className="mono">{ocrText}</div>
        </div>
      )}

      {candidates.length > 0 && (
        <div className="card">
          <h2>Possible matches — please confirm</h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Medicine</th>
                  <th>Generic</th>
                  <th>Strength</th>
                  <th>Barcode</th>
                  <th>Match</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {candidates.map((candidate) => (
                  <tr key={candidate.medicine_id}>
                    <td>{candidate.name}</td>
                    <td>{candidate.generic_name ?? '—'}</td>
                    <td>{candidate.strength ?? '—'}</td>
                    <td className="mono">{candidate.barcode ?? '—'}</td>
                    <td className="muted">{MATCH_LABEL[candidate.match_type]}</td>
                    <td className="right">
                      {captureId ? (
                        <button
                          className="btn btn-sm"
                          onClick={() => void confirmCandidate(candidate)}
                          disabled={busy}
                        >
                          Confirm
                        </button>
                      ) : (
                        <button
                          className="btn btn-sm"
                          onClick={() => onPicked?.(candidate, 'manual')}
                          disabled={busy}
                        >
                          Use this medicine
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="card">
        <h2>Manual entry</h2>
        <Field label="Barcode or medicine name" hint="Always available — even without a camera or OCR engine.">
          <input
            value={manualText}
            onChange={(e) => setManualText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void searchManual()
            }}
            placeholder="e.g. 8901234567890 or Paracetamol"
          />
        </Field>
        <div className="btn-row">
          <button className="btn" onClick={() => void searchManual()} disabled={busy || !manualText.trim()}>
            {busy ? 'Searching…' : 'Search'}
          </button>
        </div>
      </div>
    </div>
  )
}
