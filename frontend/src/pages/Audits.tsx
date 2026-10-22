import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { api, type Page } from '../lib/api'
import type { CountingSessionOut, MedicineOut, StockAuditOut, StockAuditItemOut } from '../lib/types'
import { Alert, ConfirmDialog, Field, Spinner, StatusBadge, fmt, fmtDate, useToast } from '../components/ui'
import { useAuth } from '../context/AuthContext'

export default function Audits() {
  const { can } = useAuth()
  const { element: toast, show } = useToast()
  const [audits, setAudits] = useState<StockAuditOut[]>([])
  const [active, setActive] = useState<StockAuditOut | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState<null | { title: string; message: string; action: () => Promise<void> }>(null)

  // Create form
  const [creating, setCreating] = useState(false)
  const [title, setTitle] = useState('')
  const [notes, setNotes] = useState('')
  const [medSearch, setMedSearch] = useState('')
  const [medResults, setMedResults] = useState<MedicineOut[]>([])
  const [selectedMeds, setSelectedMeds] = useState<MedicineOut[]>([])

  // Link session form
  const [sessions, setSessions] = useState<CountingSessionOut[]>([])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const page = await api.get<Page<StockAuditOut>>('/api/audits?page_size=25')
      setAudits(page.items)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load audits')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const openAudit = useCallback(async (id: number) => {
    setActive(await api.get<StockAuditOut>(`/api/audits/${id}`))
  }, [])

  useEffect(() => {
    if (!medSearch.trim()) {
      setMedResults([])
      return
    }
    const t = setTimeout(async () => {
      const res = await api.get<Page<MedicineOut>>(`/api/medicines?search=${encodeURIComponent(medSearch)}&page_size=8`)
      setMedResults(res.items.filter((m) => !selectedMeds.some((s) => s.id === m.id)))
    }, 250)
    return () => clearTimeout(t)
  }, [medSearch, selectedMeds])

  const createAudit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const audit = await api.post<StockAuditOut>('/api/audits', {
        title,
        notes: notes || null,
        medicine_ids: selectedMeds.map((m) => m.id),
      })
      show(`Audit ${audit.code} created`)
      setCreating(false)
      setTitle('')
      setNotes('')
      setSelectedMeds([])
      await load()
      await openAudit(audit.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create audit')
    } finally {
      setBusy(false)
    }
  }

  const completeAudit = async () => {
    if (!active) return
    setBusy(true)
    try {
      const updated = await api.post<StockAuditOut>(`/api/audits/${active.id}/complete`)
      setActive(updated)
      show('Audit completed')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to complete audit')
    } finally {
      setBusy(false)
      setConfirm(null)
    }
  }

  const linkSession = async (sessionId: number) => {
    if (!active) return
    setBusy(true)
    try {
      await api.post(`/api/audits/${active.id}/link-session`, { session_id: sessionId })
      show('Counting session linked')
      await openAudit(active.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to link session')
    } finally {
      setBusy(false)
    }
  }

  const loadLinkableSessions = async () => {
    if (!active) return
    const res = await api.get<Page<CountingSessionOut>>(
      '/api/counting/sessions?status=IN_PROGRESS&page_size=50',
    )
    setSessions(res.items.filter((s) => !s.stock_audit_id))
  }

  return (
    <div>
      {toast}
      {confirm && (
        <ConfirmDialog
          title={confirm.title}
          message={confirm.message}
          busy={busy}
          onCancel={() => setConfirm(null)}
          onConfirm={() => void confirm.action()}
        />
      )}
      <div className="page-header">
        <div>
          <h1>Physical Stock Audits</h1>
          <p>Expected stock comes from recorded movements; physical stock from reviewed counts. Variances are reported, never auto-corrected.</p>
        </div>
        {can('audit:initiate') && (
          <button className="btn" onClick={() => setCreating((c) => !c)}>
            {creating ? 'Close form' : '+ New audit'}
          </button>
        )}
      </div>

      {error && <Alert kind="error">{error}</Alert>}

      {creating && (
        <div className="card">
          <h2>Initiate stock audit</h2>
          <form onSubmit={createAudit}>
            <Field label="Title *">
              <input value={title} onChange={(e) => setTitle(e.target.value)} required placeholder="October 2026 full audit" />
            </Field>
            <Field label="Notes">
              <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
            </Field>
            <Field label="Add medicines" hint="Search and select each medicine to include in the audit scope.">
              <input value={medSearch} onChange={(e) => setMedSearch(e.target.value)} placeholder="Search medicines…" />
            </Field>
            {medResults.length > 0 && (
              <div className="table-wrap mb">
                <table>
                  <tbody>
                    {medResults.map((m) => (
                      <tr key={m.id}>
                        <td>{m.name}</td>
                        <td className="right">
                          <button
                            type="button"
                            className="btn btn-sm btn-ghost"
                            onClick={() => setSelectedMeds([...selectedMeds, m])}
                          >
                            Add
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {selectedMeds.length > 0 && (
              <div className="flex mb">
                {selectedMeds.map((m) => (
                  <span key={m.id} className="badge badge-info">
                    {m.name}{' '}
                    <button
                      type="button"
                      style={{ border: 'none', background: 'none', cursor: 'pointer', color: 'inherit' }}
                      onClick={() => setSelectedMeds(selectedMeds.filter((x) => x.id !== m.id))}
                    >
                      ✕
                    </button>
                  </span>
                ))}
              </div>
            )}
            <button className="btn" disabled={busy || !title.trim()}>
              {busy ? 'Creating…' : 'Create audit'}
            </button>
          </form>
        </div>
      )}

      <div className="grid" style={{ gridTemplateColumns: active ? '1fr 380px' : '1fr' }}>
        <div>
          {active ? (
            <AuditDetail
              audit={active}
              busy={busy}
              onComplete={() =>
                setConfirm({
                  title: 'Complete audit?',
                  message: 'Variances will be finalized. Inventory is NOT automatically adjusted — adjustments require a separate authorized transaction.',
                  action: completeAudit,
                })
              }
              onLinkSession={(id) => void linkSession(id)}
              onLoadLinkable={() => void loadLinkableSessions()}
              sessions={sessions}
            />
          ) : (
            <div className="card">
              <p className="muted">Select an audit from the list to view its variance report.</p>
            </div>
          )}
        </div>

        <div className="card">
          <h2>Audits</h2>
          {loading ? (
            <Spinner />
          ) : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Code</th><th>Title</th><th>Status</th><th></th></tr></thead>
                <tbody>
                  {audits.map((a) => (
                    <tr key={a.id}>
                      <td className="mono">{a.code}</td>
                      <td>{a.title}</td>
                      <td><StatusBadge value={a.status} /></td>
                      <td className="right">
                        <button className="btn btn-sm btn-ghost" onClick={() => void openAudit(a.id)}>Open</button>
                      </td>
                    </tr>
                  ))}
                  {audits.length === 0 && <tr><td colSpan={4} className="muted">No audits yet.</td></tr>}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function AuditDetail({
  audit,
  busy,
  onComplete,
  onLinkSession,
  onLoadLinkable,
  sessions,
}: {
  audit: StockAuditOut
  busy: boolean
  onComplete: () => void
  onLinkSession: (sessionId: number) => void
  onLoadLinkable: () => void
  sessions: CountingSessionOut[]
}) {
  return (
    <div className="card">
      <div className="flex" style={{ justifyContent: 'space-between' }}>
        <div>
          <span className="mono muted">{audit.code}</span> <StatusBadge value={audit.status} />
          <h2 style={{ marginTop: 6 }}>{audit.title}</h2>
          <div className="muted">
            Audit date: {audit.audit_date} · Initiated by {audit.initiator_username ?? '—'} ·{' '}
            {audit.items.length} medicine(s)
          </div>
        </div>
        {audit.status === 'OPEN' && (
          <button className="btn btn-success" onClick={onComplete} disabled={busy}>
            Complete audit
          </button>
        )}
      </div>

      <h2 className="mt">Variance report</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Medicine</th>
              <th>Expected</th>
              <th>Physical</th>
              <th>Variance</th>
              <th>Class</th>
              <th>Counter</th>
              <th>Reviewer</th>
            </tr>
          </thead>
          <tbody>
            {audit.items.map((item: StockAuditItemOut) => (
              <tr key={item.id}>
                <td>
                  <strong>{item.medicine_name ?? item.medicine_id}</strong>
                  {item.medicine_generic && <div className="muted">{item.medicine_generic}</div>}
                </td>
                <td>{fmt(item.expected_stock)}</td>
                <td>{fmt(item.physical_stock)}</td>
                <td>
                  {item.variance === null || item.variance === undefined ? (
                    '—'
                  ) : (
                    <strong style={{ color: Number(item.variance) < 0 ? 'var(--danger)' : Number(item.variance) > 0 ? 'var(--warning)' : 'var(--success)' }}>
                      {Number(item.variance) > 0 ? '+' : ''}
                      {fmt(item.variance)}
                    </strong>
                  )}
                </td>
                <td><StatusBadge value={item.classification} /></td>
                <td>{item.counter_username ?? '—'}</td>
                <td>{item.reviewer_username ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {audit.status === 'OPEN' && (
        <>
          <h2 className="mt">Link a counting session</h2>
          <p className="muted">
            Create a counting session for a medicine in this audit (from the Counting workspace), then link it here.
            When the session is approved, physical stock and variance are recorded on the audit item.
          </p>
          <button className="btn btn-ghost btn-sm" onClick={onLoadLinkable}>Load linkable sessions</button>
          {sessions.length > 0 && (
            <div className="table-wrap mt">
              <table>
                <thead><tr><th>Code</th><th>Medicine</th><th>Status</th><th></th></tr></thead>
                <tbody>
                  {sessions.map((s) => (
                    <tr key={s.id}>
                      <td className="mono">{s.code}</td>
                      <td>{s.active_medicine_name ?? '—'}</td>
                      <td><StatusBadge value={s.status} /></td>
                      <td className="right">
                        <button className="btn btn-sm" onClick={() => onLinkSession(s.id)} disabled={busy}>
                          Link
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {audit.completed_at && (
        <Alert kind="success">Completed {fmtDate(audit.completed_at)}.</Alert>
      )}
    </div>
  )
}
