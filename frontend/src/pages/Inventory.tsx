import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { api, type Page } from '../lib/api'
import type { InventoryTransactionOut, MedicineOut } from '../lib/types'
import { Alert, Field, Pagination, Spinner, fmt, fmtDate, useToast } from '../components/ui'
import { useAuth } from '../context/AuthContext'

const TYPES = [
  { value: 'RECEIVED', label: 'Stock received' },
  { value: 'ISSUED', label: 'Stock issued' },
  { value: 'RETURN_IN', label: 'Return in' },
  { value: 'RETURN_OUT', label: 'Return out' },
  { value: 'ADJUSTMENT_POSITIVE', label: 'Adjustment (+)' },
  { value: 'ADJUSTMENT_NEGATIVE', label: 'Adjustment (−)' },
]

export default function Inventory() {
  const { can } = useAuth()
  const { element: toast, show } = useToast()
  const [page, setPage] = useState(1)
  const [data, setData] = useState<Page<InventoryTransactionOut> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // New transaction form
  const [medSearch, setMedSearch] = useState('')
  const [medResults, setMedResults] = useState<MedicineOut[]>([])
  const [medicine, setMedicine] = useState<MedicineOut | null>(null)
  const [form, setForm] = useState({ transaction_type: 'RECEIVED', quantity: '', reference_number: '', notes: '' })
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(await api.get<Page<InventoryTransactionOut>>(`/api/inventory/transactions?page=${page}&page_size=25`))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load transactions')
    } finally {
      setLoading(false)
    }
  }, [page])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    if (!medSearch.trim()) {
      setMedResults([])
      return
    }
    const t = setTimeout(async () => {
      const res = await api.get<Page<MedicineOut>>(
        `/api/medicines?search=${encodeURIComponent(medSearch)}&page_size=8&is_active=true`,
      )
      setMedResults(res.items)
    }, 250)
    return () => clearTimeout(t)
  }, [medSearch])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!medicine) return
    setBusy(true)
    setError(null)
    try {
      await api.post('/api/inventory/transactions', {
        medicine_id: medicine.id,
        transaction_type: form.transaction_type,
        quantity: Number(form.quantity),
        reference_number: form.reference_number || null,
        notes: form.notes || null,
      })
      show('Transaction recorded')
      setForm({ transaction_type: 'RECEIVED', quantity: '', reference_number: '', notes: '' })
      setMedicine(null)
      setMedSearch('')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to record transaction')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      {toast}
      <div className="page-header">
        <div>
          <h1>Inventory Transactions</h1>
          <p>Every movement is tracked with type, quantity, user and timestamp. Expected stock is computed from these records.</p>
        </div>
      </div>

      {error && <Alert kind="error">{error}</Alert>}

      {can('inventory:transact') && (
        <div className="card">
          <h2>Record transaction</h2>
          <form onSubmit={submit}>
            <div className="grid grid-2">
              <div>
                <Field label="Medicine" hint="Search by name or generic, then select.">
                  <input
                    value={medicine ? `${medicine.name} (ID ${medicine.id})` : medSearch}
                    onChange={(e) => {
                      setMedicine(null)
                      setMedSearch(e.target.value)
                    }}
                    required
                  />
                </Field>
                {!medicine && medResults.length > 0 && (
                  <div className="table-wrap mb">
                    <table>
                      <tbody>
                        {medResults.map((m) => (
                          <tr key={m.id}>
                            <td>
                              {m.name}
                              <div className="muted">expected: {fmt(m.expected_stock)} {m.base_unit}s</div>
                            </td>
                            <td className="right">
                              <button type="button" className="btn btn-sm btn-ghost" onClick={() => setMedicine(m)}>
                                Select
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <Field label="Transaction type">
                  <select value={form.transaction_type} onChange={(e) => setForm({ ...form, transaction_type: e.target.value })}>
                    {TYPES.map((t) => (
                      <option key={t.value} value={t.value}>{t.label}</option>
                    ))}
                  </select>
                </Field>
              </div>
              <div>
                <Field label="Quantity (positive number)" hint="Stored in base units; the type determines the sign.">
                  <input type="number" min="0.001" step="any" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} required />
                </Field>
                <Field label="Reference number">
                  <input value={form.reference_number} onChange={(e) => setForm({ ...form, reference_number: e.target.value })} placeholder="GRN-001 / PO-42" />
                </Field>
                <Field label="Notes">
                  <textarea rows={2} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
                </Field>
              </div>
            </div>
            <button className="btn" disabled={busy || !medicine}>{busy ? 'Saving…' : 'Record transaction'}</button>
          </form>
        </div>
      )}

      <div className="card">
        <h2>Recent transactions</h2>
        {loading && !data ? (
          <Spinner />
        ) : (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Medicine</th>
                    <th>Type</th>
                    <th>Signed qty</th>
                    <th>By</th>
                    <th>Reference</th>
                    <th>Notes</th>
                  </tr>
                </thead>
                <tbody>
                  {(data?.items ?? []).map((t) => (
                    <tr key={t.id}>
                      <td>{fmtDate(t.occurred_at)}</td>
                      <td>{t.medicine_name ?? t.medicine_id}</td>
                      <td><span className="badge badge-info">{t.transaction_type.replaceAll('_', ' ')}</span></td>
                      <td className={Number(t.signed_quantity) < 0 ? 'right' : 'right'}>
                        <strong style={{ color: Number(t.signed_quantity) < 0 ? 'var(--danger)' : 'var(--success)' }}>
                          {Number(t.signed_quantity) > 0 ? '+' : ''}{fmt(t.signed_quantity)}
                        </strong>
                      </td>
                      <td>{t.recorded_by_username ?? '—'}</td>
                      <td className="mono">{t.reference_number ?? '—'}</td>
                      <td className="muted">{t.notes ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {data && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />}
          </>
        )}
      </div>
    </div>
  )
}
