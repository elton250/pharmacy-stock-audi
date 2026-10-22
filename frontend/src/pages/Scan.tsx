import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import type { MedicineOut, ScanResult } from '../lib/types'
import { Alert, Field, Spinner, fmt } from '../components/ui'
import { useAuth } from '../context/AuthContext'

export default function Scan() {
  const { can } = useAuth()
  const [barcode, setBarcode] = useState('')
  const [result, setResult] = useState<ScanResult | null>(null)
  const [search, setSearch] = useState('')
  const [results, setResults] = useState<MedicineOut[]>([])
  const [loading, setLoading] = useState(false)
  const [searching, setSearching] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const doScan = async (code?: string) => {
    const value = (code ?? barcode).trim()
    if (!value) return
    setLoading(true)
    setResult(null)
    try {
      setResult(await api.get<ScanResult>(`/api/medicines/scan?barcode=${encodeURIComponent(value)}`))
    } finally {
      setLoading(false)
    }
  }

  // Manual search fallback
  useEffect(() => {
    if (!search.trim()) {
      setResults([])
      return
    }
    const t = setTimeout(async () => {
      setSearching(true)
      try {
        const page = await api.get<{ items: MedicineOut[] }>(
          `/api/medicines?search=${encodeURIComponent(search)}&page_size=8&is_active=true`,
        )
        setResults(page.items)
      } finally {
        setSearching(false)
      }
    }, 250)
    return () => clearTimeout(t)
  }, [search])

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Barcode Scan</h1>
          <p>Scan a medicine barcode (USB scanners work like a keyboard) or search manually.</p>
        </div>
      </div>

      <div className="card">
        <Field label="Barcode" hint="Point the scanner at the barcode, or type it and press Enter.">
          <input
            ref={inputRef}
            className="scan-input"
            value={barcode}
            onChange={(e) => setBarcode(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void doScan()
            }}
            placeholder="Scan or type barcode…"
            autoFocus
          />
        </Field>
        <div className="btn-row">
          <button className="btn" onClick={() => void doScan()} disabled={loading || !barcode.trim()}>
            {loading ? 'Looking up…' : 'Look up barcode'}
          </button>
        </div>
      </div>

      {result && (
        <div className="card">
          {result.found && result.medicine ? (
            <>
              <div className="flex" style={{ justifyContent: 'space-between' }}>
                <div>
                  <h2>{result.medicine.name}</h2>
                  <div className="muted">
                    {[result.medicine.generic_name, result.medicine.strength, result.medicine.dosage_form].filter(Boolean).join(' · ')}
                  </div>
                  <div className="mono muted">Barcode: {result.barcode}</div>
                </div>
                <div className="right">
                  <div className="stat-value">{fmt(result.medicine.expected_stock)}</div>
                  <div className="stat-label">{result.medicine.base_unit}s expected</div>
                </div>
              </div>
              <div className="btn-row mt">
                {can('count:perform') && (
                  <Link className="btn" to={`/counting?medicine_id=${result.medicine.id}`}>
                    Start count for this medicine
                  </Link>
                )}
                {can('medicine:view') && (
                  <Link className="btn btn-ghost" to={`/medicines/${result.medicine.id}/edit`}>
                    View medicine record
                  </Link>
                )}
              </div>
            </>
          ) : (
            <Alert kind="warning">
              {result.message ?? 'Unknown barcode.'} You can search manually below, or ask an administrator to
              register the barcode against the correct medicine. Unknown barcodes are never silently assigned.
            </Alert>
          )}
        </div>
      )}

      <div className="card">
        <h2>Manual medicine search</h2>
        <Field label="Search by name, generic or brand">
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="e.g. Paracetamol" />
        </Field>
        {searching && <Spinner label="Searching…" />}
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Name</th><th>Generic</th><th>Stock</th><th></th></tr>
            </thead>
            <tbody>
              {results.map((m) => (
                <tr key={m.id}>
                  <td>{m.name}</td>
                  <td>{m.generic_name ?? '—'}</td>
                  <td>{fmt(m.expected_stock)} {m.base_unit}s</td>
                  <td className="right">
                    {can('count:perform') && (
                      <Link className="btn btn-sm" to={`/counting?medicine_id=${m.id}`}>Count</Link>
                    )}
                  </td>
                </tr>
              ))}
              {search && !searching && results.length === 0 && (
                <tr><td colSpan={4} className="muted">No medicines match your search.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
