import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import CameraCapture from '../components/CameraCapture'
import { Alert, Spinner } from '../components/ui'
import { useAuth } from '../context/AuthContext'
import { api, ApiError } from '../lib/api'
import type { MedicineOut, VisionCandidate, VisionStatus } from '../lib/types'

export default function VisionScan() {
  const { can } = useAuth()
  const [status, setStatus] = useState<VisionStatus | null>(null)
  const [statusError, setStatusError] = useState<string | null>(null)
  const [checking, setChecking] = useState(true)
  const [picked, setPicked] = useState<VisionCandidate | null>(null)
  const [medicine, setMedicine] = useState<MedicineOut | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let cancelled = false
    api
      .get<VisionStatus>('/api/vision/status')
      .then((value) => {
        if (!cancelled) {
          setStatus(value)
          setStatusError(null)
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setStatus(null)
        setStatusError(
          err instanceof ApiError && err.status !== 404
            ? err.message
            : 'Vision-assisted identification is not available on this server.',
        )
      })
      .finally(() => {
        if (!cancelled) setChecking(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  // The feature is only usable when the backend confirms that it is enabled.
  // Until then the page says so instead of offering a camera that cannot work.
  const available = status?.enabled === true

  const showPicked = async (candidate: VisionCandidate) => {
    setPicked(candidate)
    setMedicine(null)
    setLoading(true)
    try {
      setMedicine(await api.get<MedicineOut>(`/api/medicines/${candidate.medicine_id}`))
    } catch {
      setMedicine(null)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Vision-Assisted Identification</h1>
          <p>
            Photograph a medicine and let the server recognise it locally. The result is only a suggestion —
            you confirm it before it is used, and no stock is ever written automatically.
          </p>
        </div>
      </div>

      {checking && <Spinner label="Checking whether the feature is available…" />}

      {!checking && !available && (
        <Alert kind="warning">
          {statusError ?? 'Vision-assisted identification is not available on this server.'} It appears here on
          its own once the server offers the feature. In the meantime you can look up a barcode on the{' '}
          <Link to="/scan">Scan page</Link>.
        </Alert>
      )}

      {available && status && !status.engine_available && (
        <Alert kind="warning">
          {status.message ?? 'The OCR engine is not available on this server.'} The manual entry below still
          works.
        </Alert>
      )}

      {available && <CameraCapture onPicked={(candidate) => void showPicked(candidate)} />}

      {picked && (
        <div className="card">
          <h2>Selected medicine</h2>
          {loading && <Spinner label="Loading medicine…" />}
          {medicine && (
            <>
              <div className="flex" style={{ justifyContent: 'space-between' }}>
                <div>
                  <h3>{medicine.name}</h3>
                  <div className="muted">
                    {[medicine.generic_name, medicine.strength, medicine.dosage_form]
                      .filter(Boolean)
                      .join(' · ')}
                  </div>
                  <div className="mono muted">Barcode: {medicine.barcodes?.[0]?.barcode ?? '—'}</div>
                </div>
                <div className="right">
                  <div className="stat-value">{medicine.expected_stock}</div>
                  <div className="stat-label">{medicine.base_unit}s expected</div>
                </div>
              </div>
              <div className="btn-row mt">
                {can('count:perform') && (
                  <Link className="btn" to={`/counting?medicine_id=${medicine.id}`}>
                    Start count for this medicine
                  </Link>
                )}
                {can('medicine:view') && (
                  <Link className="btn btn-ghost" to={`/medicines/${medicine.id}/edit`}>
                    View medicine record
                  </Link>
                )}
              </div>
            </>
          )}
          {!loading && !medicine && (
            <Alert kind="warning">The medicine record could not be loaded. Use the medicine list instead.</Alert>
          )}
        </div>
      )}
    </div>
  )
}
