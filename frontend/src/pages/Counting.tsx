import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, ApiError, type Page } from '../lib/api'
import type { CountingSessionOut, MedicineOut, PackageOut } from '../lib/types'
import { Alert, ConfirmDialog, Field, Spinner, StatusBadge, fmt, fmtDate, useToast } from '../components/ui'
import CameraCapture from '../components/CameraCapture'
import { useAuth } from '../context/AuthContext'

interface LineDraft {
  key: number
  package_config_id: number | null
  package_label: string
  quantity: string
  partial_units: string
  units_per_unit: number // base units per full package (1 for loose)
}

let lineSeq = 1

/** Turn an API/network failure into a message the operator can act on. */
function describeError(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (err.code === 'backend_unavailable') return err.message
    switch (err.code) {
      case 'not_found':
        return 'The counting session no longer exists. Refresh the list.'
      case 'already_completed':
        return 'This session was already completed and reviewed — it cannot be changed any more.'
      case 'already_cancelled':
        return 'This session was already cancelled.'
      case 'archived':
        return 'This session is archived and can no longer be modified.'
      case 'active_session_exists':
        return 'Another counting session is still active. It must be completed (and reviewed) or cancelled before a new one can be started.'
      case 'forbidden':
        return err.message
      default:
        return err.message || fallback
    }
  }
  if (err instanceof Error) return err.message
  return fallback
}

export default function Counting() {
  const { can, user } = useAuth()
  const { element: toast, show } = useToast()
  const [searchParams, setSearchParams] = useSearchParams()
  const [sessions, setSessions] = useState<CountingSessionOut[]>([])
  const [statusFilter, setStatusFilter] = useState<'ALL' | 'MINE' | 'PENDING_REVIEW'>('MINE')
  const [includeArchived, setIncludeArchived] = useState(false)
  const [active, setActive] = useState<CountingSessionOut | null>(null)
  const [activeSession, setActiveSession] = useState<CountingSessionOut | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // New-session medicine picker
  const [pickerOpen, setPickerOpen] = useState(false)
  const [medSearch, setMedSearch] = useState('')
  const [medResults, setMedResults] = useState<MedicineOut[]>([])
  const [pickedMedicine, setPickedMedicine] = useState<MedicineOut | null>(null)
  const [scanCode, setScanCode] = useState('')

  // Count entry
  const [lines, setLines] = useState<LineDraft[]>([])
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState<null | {
    title: string
    message: string
    action: () => Promise<void>
    withReason?: boolean
  }>(null)
  const [cancelReason, setCancelReason] = useState('')
  // The confirm action is stored in state and therefore closes over the render
  // in which the dialog was opened. The reason is typed *after* that, so it is
  // kept in a ref and read at submit time — otherwise the typed reason would
  // be lost and never reach the API.
  const cancelReasonRef = useRef('')

  // Unsaved draft lines are kept per session id so switching panels (or a page
  // reload) never silently discards a half-entered count.
  const draftsRef = useRef<Record<number, LineDraft[]>>({})
  const linesRef = useRef<LineDraft[]>([])
  const activeIdRef = useRef<number | null>(null)
  // Reuse the same idempotency key for retries of an identical unsaved payload.
  // A changed draft gets a different key; a successful save clears the retry key.
  const countRetryRef = useRef<{ fingerprint: string; key: string } | null>(null)
  // Synchronous locks prevent two clicks/events before React re-renders.
  const scanInFlightRef = useRef(false)
  const submitInFlightRef = useRef(false)
  useEffect(() => {
    linesRef.current = lines
  }, [lines])

  const loadSessions = useCallback(async () => {
    setLoading(true)
    try {
      let url = '/api/counting/sessions?page_size=50'
      if (statusFilter === 'PENDING_REVIEW') url += '&status=PENDING_REVIEW'
      else if (statusFilter === 'MINE') url += '&mine=true'
      if (includeArchived) url += '&include_archived=true'
      const page = await api.get<Page<CountingSessionOut>>(url)
      setSessions(page.items)
    } catch (err) {
      setError(describeError(err, 'Failed to load sessions'))
    } finally {
      setLoading(false)
    }
  }, [statusFilter, includeArchived])

  const loadActiveSession = useCallback(async () => {
    try {
      const s = await api.get<CountingSessionOut | null>('/api/counting/sessions/active')
      setActiveSession(s)
    } catch {
      setActiveSession(null)
    }
  }, [])

  useEffect(() => {
    void loadSessions()
  }, [loadSessions])

  useEffect(() => {
    void loadActiveSession()
  }, [loadActiveSession])

  const openSession = useCallback(async (id: number) => {
    const s = await api.get<CountingSessionOut>(`/api/counting/sessions/${id}`)
    if (s.active_medicine_id) {
      try {
        s.active_medicine = await api.get<MedicineOut>(`/api/medicines/${s.active_medicine_id}`)
      } catch {
        s.active_medicine = null
      }
    }
    // Stash the draft of the session we are leaving, restore the one we enter.
    if (activeIdRef.current !== null && activeIdRef.current !== id) {
      draftsRef.current[activeIdRef.current] = linesRef.current
    }
    activeIdRef.current = id
    setActive(s)
    setLines(draftsRef.current[id] ?? [])
  }, [])

  const closeSessionPanel = useCallback(() => {
    if (activeIdRef.current !== null) draftsRef.current[activeIdRef.current] = linesRef.current
    activeIdRef.current = null
    setActive(null)
    setLines([])
  }, [])

  // Deep-link: /counting?medicine_id=3 → open the new-session flow with that medicine
  useEffect(() => {
    const mid = searchParams.get('medicine_id')
    if (mid) {
      void (async () => {
        const m = await api.get<MedicineOut>(`/api/medicines/${mid}`)
        setPickedMedicine(m)
        setPickerOpen(true)
      })()
      setSearchParams({}, { replace: true })
    }
  }, [searchParams, setSearchParams])

  // Medicine search for picker
  useEffect(() => {
    if (!medSearch.trim()) {
      setMedResults([])
      return
    }
    const t = setTimeout(async () => {
      const page = await api.get<Page<MedicineOut>>(
        `/api/medicines?search=${encodeURIComponent(medSearch)}&page_size=8&is_active=true`,
      )
      setMedResults(page.items)
    }, 250)
    return () => clearTimeout(t)
  }, [medSearch])

  const scanMedicine = async () => {
    if (!scanCode.trim()) return
    const res = await api.get<{ found: boolean; medicine?: MedicineOut | null; message?: string }>(
      `/api/medicines/scan?barcode=${encodeURIComponent(scanCode.trim())}`,
    )
    if (res.found && res.medicine) {
      setPickedMedicine(res.medicine)
      setScanCode('')
    } else {
      setError(res.message ?? 'Unknown barcode')
    }
  }

  const createSession = async () => {
    if (!pickedMedicine) return
    setBusy(true)
    setError(null)
    try {
      const s = await api.post<CountingSessionOut>('/api/counting/sessions', { medicine_id: pickedMedicine.id })
      show(`Session ${s.code} started`)
      setPickerOpen(false)
      setPickedMedicine(null)
      await loadSessions()
      await loadActiveSession()
      await openSession(s.id)
    } catch (err) {
      setError(describeError(err, 'Failed to create session'))
      if (err instanceof ApiError && err.code === 'active_session_exists') await loadActiveSession()
    } finally {
      setBusy(false)
    }
  }

  const addLine = () => {
    if (!active?.active_medicine) return
    setLines((prev) => [
      ...prev,
      { key: lineSeq++, package_config_id: null, package_label: 'Loose base units', quantity: '1', partial_units: '', units_per_unit: 1 },
    ])
  }

  const packages: PackageOut[] = useMemo(() => active?.active_medicine?.packages ?? [], [active])

  const setLinePackage = (key: number, pkgId: number | null) => {
    setLines((prev) =>
      prev.map((l) => {
        if (l.key !== key) return l
        if (pkgId === null) return { ...l, package_config_id: null, package_label: 'Loose base units', units_per_unit: 1 }
        const pkg = packages.find((p) => p.id === pkgId)!
        return {
          ...l,
          package_config_id: pkgId,
          package_label: pkg.name,
          units_per_unit: Number(pkg.units_in_base ?? pkg.units_per_parent),
          partial_units: '',
        }
      }),
    )
  }

  const lineBase = (l: LineDraft): number => {
    const qty = Number(l.quantity || 0)
    if (l.package_config_id === null) return qty
    if (l.partial_units !== '' && Number(l.partial_units) >= 0) {
      // Cut package: partial_units are in the child level.
      const pkg = packages.find((p) => p.id === l.package_config_id)
      if (!pkg) return 0
      const childFactor = pkg.parent_id ? Number(pkg.units_in_base ?? 1) / Number(pkg.units_per_parent) : 1
      return Number(l.partial_units) * childFactor
    }
    return qty * l.units_per_unit
  }

  const total = useMemo(() => lines.reduce((acc, l) => acc + lineBase(l), 0), [lines, packages])

  const scanActivePackage = async (code: string) => {
    if (!active?.active_medicine || busy || scanInFlightRef.current) return
    scanInFlightRef.current = true
    setBusy(true)
    setError(null)
    try {
      const res = await api.get<{ found: boolean; medicine?: MedicineOut | null; message?: string }>(
        `/api/medicines/scan?barcode=${encodeURIComponent(code.trim())}`,
      )
      if (!res.found || !res.medicine) throw new Error(res.message ?? 'Unknown barcode')
      if (res.medicine.id !== active.active_medicine.id) {
        throw new Error('This barcode belongs to a different medicine. This audit session is locked to ' + active.active_medicine.name)
      }
      const configured = active.active_medicine.packages
      if (configured.length !== 1) {
        throw new Error('Medicine identified, but its box size is not uniquely configured. Select the package manually before counting.')
      }
      const pkg = configured[0]
      setLines(prev => {
        const existing = prev.find(l => l.package_config_id === pkg.id && l.partial_units === '')
        if (existing) return prev.map(l => l.key === existing.key ? { ...l, quantity: String(Number(l.quantity || 0) + 1) } : l)
        return [...prev, { key: lineSeq++, package_config_id: pkg.id, package_label: pkg.name, quantity: '1', partial_units: '', units_per_unit: Number(pkg.units_in_base ?? pkg.units_per_parent) }]
      })
      show('One ' + pkg.name + ' added to this audit count')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Barcode scan failed')
    } finally {
      scanInFlightRef.current = false
      setBusy(false)
    }
  }

  const submitCount = async () => {
    if (!active || busy || submitInFlightRef.current) return
    submitInFlightRef.current = true
    setBusy(true)
    setError(null)
    try {
      const countLines = lines.map((l) => ({
        package_config_id: l.package_config_id,
        quantity: Number(l.quantity || 0),
        partial_units:
          l.package_config_id !== null && l.partial_units !== '' ? Number(l.partial_units) : null,
      }))
      const fingerprint = JSON.stringify({ session_id: active.id, lines: countLines })
      if (countLines.length === 0) {
        countLines.push({ package_config_id: null, quantity: 0, partial_units: null })
      }
      if (!countRetryRef.current || countRetryRef.current.fingerprint !== fingerprint) {
        countRetryRef.current = { fingerprint, key: crypto.randomUUID() }
      }
      const payload = {
        idempotency_key: countRetryRef.current.key,
        lines: countLines,
      }
      await api.post(`/api/counting/sessions/${active.id}/counts`, payload)
      show('Count saved')
      countRetryRef.current = null
      setLines([])
      draftsRef.current[active.id] = []
      await openSession(active.id)
      await loadSessions()
    } catch (err) {
      setError(describeError(err, 'Failed to save count'))
    } finally {
      submitInFlightRef.current = false
      setBusy(false)
    }
  }

  const submitForReview = async () => {
    if (!active) return
    setBusy(true)
    try {
      await api.post(`/api/counting/sessions/${active.id}/submit`)
      show('Submitted for review')
      await openSession(active.id)
      await loadSessions()
    } catch (err) {
      setError(describeError(err, 'Submit failed'))
    } finally {
      setBusy(false)
    }
  }

  const review = async (decision: 'APPROVED' | 'REJECTED') => {
    if (!active) return
    setBusy(true)
    try {
      await api.post(`/api/counting/sessions/${active.id}/review`, { decision })
      show(decision === 'APPROVED' ? 'Session approved and completed' : 'Session rejected — back to counting')
      await openSession(active.id)
      await loadSessions()
    } catch (err) {
      setError(describeError(err, 'Review failed'))
    } finally {
      setBusy(false)
    }
  }

  /** Reopen the existing session — never creates a new one, never loses progress. */
  const resumeSession = async (session: CountingSessionOut) => {
    setBusy(true)
    setError(null)
    try {
      const s = await api.post<CountingSessionOut>(`/api/counting/sessions/${session.id}/resume`)
      show(`Session ${s.code} resumed — saved counts kept (${fmt(s.total_counted_base_units)} base units)`)
      await loadActiveSession()
      await openSession(s.id)
      await loadSessions()
    } catch (err) {
      setError(describeError(err, 'Failed to resume session'))
      await loadActiveSession()
    } finally {
      setBusy(false)
    }
  }

  const cancelSession = async (session: CountingSessionOut) => {
    setBusy(true)
    setError(null)
    const reason = cancelReasonRef.current.trim()
    try {
      await api.post(`/api/counting/sessions/${session.id}/cancel`, reason ? { reason } : {})
      show(`Session ${session.code} cancelled — it no longer contributes to physical stock`)
      // activeIdRef is always current, unlike the captured `active` state.
      if (activeIdRef.current === session.id) closeSessionPanel()
      await loadSessions()
      await loadActiveSession()
    } catch (err) {
      setError(describeError(err, 'Cancel failed'))
      await loadActiveSession()
    } finally {
      setBusy(false)
      setConfirm(null)
      setCancelReason('')
      cancelReasonRef.current = ''
    }
  }

  const archiveSession = async (session: CountingSessionOut) => {
    setBusy(true)
    try {
      await api.post(`/api/counting/sessions/${session.id}/archive`, { reason: 'Archived from the session list' })
      show(`Session ${session.code} archived — it is hidden from the normal list but preserved`)
      if (active?.id === session.id) closeSessionPanel()
      await loadSessions()
    } catch (err) {
      setError(describeError(err, 'Archive failed'))
    } finally {
      setBusy(false)
      setConfirm(null)
    }
  }

  const unarchiveSession = async (session: CountingSessionOut) => {
    setBusy(true)
    try {
      await api.post(`/api/counting/sessions/${session.id}/unarchive`)
      show(`Session ${session.code} restored`)
      await loadSessions()
    } catch (err) {
      setError(describeError(err, 'Restore failed'))
    } finally {
      setBusy(false)
    }
  }

  /** Attach a medicine to a DRAFT session (the session's medicine is immutable afterwards). */
  const activateMedicine = async (session: CountingSessionOut, medicine: MedicineOut) => {
    setBusy(true)
    setError(null)
    try {
      const s = await api.post<CountingSessionOut>(`/api/counting/sessions/${session.id}/activate`, {
        medicine_id: medicine.id,
      })
      show(`Session ${s.code} is now counting ${medicine.name}`)
      await loadActiveSession()
      await openSession(s.id)
      await loadSessions()
    } catch (err) {
      setError(describeError(err, 'Failed to set the medicine'))
    } finally {
      setBusy(false)
    }
  }

  const canOperate = (s: CountingSessionOut) => s.created_by === user?.id
  const canCancel = (s: CountingSessionOut) => canOperate(s) || can('count:manage')
  const canResume = (s: CountingSessionOut) =>
    canOperate(s) || can('count:manage') || can('count:review')

  return (
    <div>
      {toast}
      {confirm && (
        <ConfirmDialog
          title={confirm.title}
          message={confirm.message}
          busy={busy}
          onCancel={() => {
            setConfirm(null)
            setCancelReason('')
            cancelReasonRef.current = ''
          }}
          onConfirm={() => void confirm.action()}
        >
          {confirm.withReason && (
            <Field label="Reason (optional)" hint="Stored in the audit log together with your name.">
              <textarea
                className="input"
                rows={3}
                maxLength={500}
                value={cancelReason}
                onChange={(e) => {
                  setCancelReason(e.target.value)
                  cancelReasonRef.current = e.target.value
                }}
                placeholder="Why is this session being cancelled?"
              />
            </Field>
          )}
        </ConfirmDialog>
      )}
      <div className="page-header">
        <div>
          <h1>Static Counting</h1>
          <p>One medicine at a time. Counts are saved with idempotency protection and require review.</p>
        </div>
        {can('count:perform') && (
          <button className="btn" onClick={() => setPickerOpen(true)}>+ New counting session</button>
        )}
      </div>

      {error && <Alert kind="error">{error}</Alert>}

      {activeSession && (
        <ActiveSessionCard
          session={activeSession}
          busy={busy}
          canCancel={canCancel(activeSession)}
          canResume={canResume(activeSession)}
          isCreator={canOperate(activeSession)}
          onResume={() => void resumeSession(activeSession)}
          onCancel={() =>
            setConfirm({
              title: 'Cancel counting session?',
              message: `Session ${activeSession.code} (ID ${activeSession.id}) and its saved counts will be marked CANCELLED. It will no longer contribute to physical stock and a new session can be started immediately. This cannot be undone.`,
              action: () => cancelSession(activeSession),
              withReason: true,
            })
          }
        />
      )}

      {pickerOpen && (
        <div className="card">
          <h2>New counting session</h2>
          <Alert kind="info">
            Scan the medicine barcode or search manually. The selected medicine becomes the session's
            <strong> active medicine</strong> and cannot be switched until the session is completed, reviewed or cancelled.
          </Alert>
          <div className="flex mb">
            <input
              className="grow scan-input"
              style={{ maxWidth: 360 }}
              placeholder="Scan barcode…"
              value={scanCode}
              onChange={(e) => setScanCode(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && void scanMedicine()}
            />
            <button className="btn" onClick={() => void scanMedicine()}>Scan</button>
          </div>
          <Field label="Or search medicines">
            <input value={medSearch} onChange={(e) => setMedSearch(e.target.value)} placeholder="Name, generic, brand…" />
          </Field>
          {pickedMedicine && (
            <Alert kind="success">
              Selected: <strong>{pickedMedicine.name}</strong> ({pickedMedicine.strength ?? ''}{' '}
              {pickedMedicine.dosage_form ?? ''}) · barcode{' '}
              {pickedMedicine.barcodes[0]?.barcode ?? '—'}
            </Alert>
          )}
          {medResults.length > 0 && (
            <div className="table-wrap mb">
              <table>
                <thead><tr><th>Name</th><th>Generic</th><th>Stock</th><th></th></tr></thead>
                <tbody>
                  {medResults.map((m) => (
                    <tr key={m.id}>
                      <td>{m.name}</td>
                      <td>{m.generic_name ?? '—'}</td>
                      <td>{fmt(m.expected_stock)} {m.base_unit}s</td>
                      <td className="right">
                        <button className="btn btn-sm btn-ghost" onClick={() => setPickedMedicine(m)}>Select</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="btn-row">
            <button className="btn" disabled={!pickedMedicine || busy} onClick={() => void createSession()}>
              {busy ? 'Starting…' : 'Start session'}
            </button>
            <button className="btn btn-ghost" onClick={() => { setPickerOpen(false); setPickedMedicine(null) }}>
              Cancel
            </button>
          </div>
        </div>
      )}

      <div className="grid" style={{ gridTemplateColumns: active ? '1fr 360px' : '1fr' }}>
        <div>
          {active ? (
            <ActiveSession
              session={active}
              lines={lines}
              total={total}
              busy={busy}
              onAddLine={addLine}
              onScanPackage={(code) => scanActivePackage(code)}
              onRemoveLine={(key) => setLines((p) => p.filter((l) => l.key !== key))}
              onSetLinePackage={setLinePackage}
              onSetLineValue={(key, field, value) =>
                setLines((p) => p.map((l) => (l.key === key ? { ...l, [field]: value } : l)))
              }
              onSubmitCount={() => void submitCount()}
              onSubmitForReview={() => void submitForReview()}
              onReview={(d) => void review(d)}
              onCancel={() =>
                setConfirm({
                  title: 'Cancel counting session?',
                  message: `Session ${active.code} and its saved counts will be marked CANCELLED. It will no longer contribute to physical stock and a new session can be started immediately. This cannot be undone.`,
                  action: () => cancelSession(active),
                  withReason: true,
                })
              }
              onClose={closeSessionPanel}
              canOperate={canOperate(active)}
              canManage={can('count:manage')}
              canCancel={canCancel(active)}
              onSetMedicine={(medicine) => void activateMedicine(active, medicine)}
              onArchive={() =>
                setConfirm({
                  title: 'Archive cancelled session?',
                  message: `Session ${active.code} will be hidden from the normal list. It is preserved in the database and can be restored.`,
                  action: () => archiveSession(active),
                })
              }
            />
            {active.status === 'IN_PROGRESS' && active.active_medicine && canOperate(active) && can('count:perform') && (
              <div className="mt">
                <h2>Count tablets from blister</h2>
                <p className="muted">AI counts visible tablets. The medicine must match the active medicine above; confirm the result to add it to this session's unsaved draft.</p>
                <CameraCapture
                  activeMedicine={{ id: active.active_medicine.id, name: active.active_medicine.name }}
                  onBlisterCountAccepted={(count) => {
                    setLines((prev) => [...prev, {
                      key: lineSeq++,
                      package_config_id: null,
                      package_label: 'AI-counted blister tablets',
                      quantity: String(count),
                      partial_units: '',
                      units_per_unit: 1,
                    }])
                    show(count + ' tablets added to the current session draft. Press Save count to record.')
                  }}
                />
              </div>
            )}
          ) : (
            <div className="card">
              <p className="muted">
                Select a session from the list, or start a new one. During an active count the medicine is locked —
                the system will reject counts for any other medicine.
              </p>
            </div>
          )}
        </div>

        <div className="card">
          <div className="flex" style={{ justifyContent: 'space-between' }}>
            <h2>Sessions</h2>
            <div className="flex" style={{ gap: 8 }}>
              <label className="flex" style={{ gap: 6, alignItems: 'center' }}>
                <input
                  type="checkbox"
                  checked={includeArchived}
                  onChange={(e) => setIncludeArchived(e.target.checked)}
                  disabled={!can('count:manage')}
                />
                <span className="muted">Show archived</span>
              </label>
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value as typeof statusFilter)}
                style={{ maxWidth: 170 }}
              >
                <option value="MINE">Mine</option>
                <option value="PENDING_REVIEW">Pending review</option>
                <option value="ALL">All</option>
              </select>
            </div>
          </div>
          {loading ? (
            <Spinner />
          ) : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Code</th><th>Medicine</th><th>Status</th><th></th></tr></thead>
                <tbody>
                  {sessions.map((s) => (
                    <tr key={s.id} className={active?.id === s.id ? 'selected-row' : ''}>
                      <td className="mono">
                        {s.code}
                        {s.archived_at && (
                          <div className="muted" style={{ fontSize: 11 }}>
                            archived {fmtDate(s.archived_at)}
                            {s.archived_by_username ? ` by ${s.archived_by_username}` : ''}
                          </div>
                        )}
                      </td>
                      <td>{s.active_medicine_name ?? '—'}</td>
                      <td>
                        <StatusBadge value={s.status} />
                        {s.archived_at && <div className="muted" style={{ fontSize: 11 }}>ARCHIVED</div>}
                      </td>
                      <td className="right">
                        <button className="btn btn-sm btn-ghost" onClick={() => void openSession(s.id)}>Open</button>
                        {['DRAFT', 'IN_PROGRESS', 'PENDING_REVIEW'].includes(s.status) && !s.archived_at && canResume(s) && (
                          <button className="btn btn-sm btn-ghost" onClick={() => void resumeSession(s)}>
                            Resume
                          </button>
                        )}
                        {['DRAFT', 'IN_PROGRESS', 'PENDING_REVIEW'].includes(s.status) && !s.archived_at && canCancel(s) && (
                          <button
                            className="btn btn-sm btn-ghost"
                            onClick={() =>
                              setConfirm({
                                title: 'Cancel counting session?',
                                message: `Session ${s.code} (ID ${s.id}) and its saved counts will be marked CANCELLED. It will no longer contribute to physical stock and a new session can be started immediately. This cannot be undone.`,
                                action: () => cancelSession(s),
                                withReason: true,
                              })
                            }
                          >
                            Cancel
                          </button>
                        )}
                        {can('count:manage') && s.status === 'CANCELLED' && !s.archived_at && (
                          <button
                            className="btn btn-sm btn-ghost"
                            onClick={() =>
                              setConfirm({
                                title: 'Archive cancelled session?',
                                message: `Session ${s.code} will be hidden from the normal list. It is preserved in the database and can be restored.`,
                                action: () => archiveSession(s),
                              })
                            }
                          >
                            Archive
                          </button>
                        )}
                        {can('count:manage') && s.archived_at && (
                          <button className="btn btn-sm btn-ghost" onClick={() => void unarchiveSession(s)}>
                            Restore
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                  {sessions.length === 0 && <tr><td colSpan={4} className="muted">No sessions.</td></tr>}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function ActiveSession({
  session,
  lines,
  total,
  busy,
  onAddLine,
  onScanPackage,
  onRemoveLine,
  onSetLinePackage,
  onSetLineValue,
  onSubmitCount,
  onSubmitForReview,
  onReview,
  onCancel,
  onClose,
  canOperate,
  canManage,
  canCancel,
  onSetMedicine,
  onArchive,
}: {
  session: CountingSessionOut
  lines: LineDraft[]
  total: number
  busy: boolean
  onAddLine: () => void
  onScanPackage: (code: string) => Promise<void>
  onRemoveLine: (key: number) => void
  onSetLinePackage: (key: number, pkgId: number | null) => void
  onSetLineValue: (key: number, field: 'quantity' | 'partial_units', value: string) => void
  onSubmitCount: () => void
  onSubmitForReview: () => void
  onReview: (d: 'APPROVED' | 'REJECTED') => void
  onCancel: () => void
  onClose: () => void
  canOperate: boolean
  canManage: boolean
  canCancel: boolean
  onSetMedicine: (medicine: MedicineOut) => void
  onArchive: () => void
}) {
  const medicine = session.active_medicine
  const packages = medicine?.packages ?? []
  const status = session.status
  const [packageBarcode, setPackageBarcode] = useState('')

  return (
    <div className="card">
      <div className="flex" style={{ justifyContent: 'space-between' }}>
        <div>
          <span className="mono muted">{session.code}</span> <StatusBadge value={status} />
          {session.stock_audit_id && <span className="badge badge-info" style={{ marginLeft: 6 }}>AUDIT #{session.stock_audit_id}</span>}
        </div>
        <button className="btn btn-ghost btn-sm" onClick={onClose}>Close panel</button>
      </div>

      <div className="active-medicine mt mb">
        <div>
          <div className="lock-note">🔒 ACTIVE MEDICINE (locked for this session)</div>
          <h2 style={{ margin: '4px 0' }}>{medicine?.name ?? session.active_medicine_name ?? 'None'}</h2>
          <div className="muted">
            {[medicine?.generic_name, medicine?.strength, medicine?.dosage_form].filter(Boolean).join(' · ')}
            {medicine && <> · counting in <strong>{medicine.base_unit}s</strong></>}
          </div>
        </div>
        <div className="right">
          <div className="stat-value">{fmt(session.total_counted_base_units)}</div>
          <div className="stat-label">base units counted so far ({session.events.length} count event{session.events.length === 1 ? '' : 's'})</div>
        </div>
      </div>

      {(status === 'IN_PROGRESS' && canOperate) && (
        <>
          <h2>Add count</h2>
          <Field label="Scan full-box barcode" hint="Each successful scan adds one configured package to this session only.">
            <input value={packageBarcode} onChange={e => setPackageBarcode(e.target.value)} onKeyDown={e => {
              if (e.key === 'Enter') { e.preventDefault(); const code = packageBarcode; setPackageBarcode(''); void onScanPackage(code) }
            }} placeholder="Scan box barcode…" disabled={busy} />
          </Field>
          <p className="muted">
            Enter full packages, cut (partial) packages and loose base units. Totals convert using this medicine's
            own package configuration.
          </p>
          {lines.map((l) => (
            <div className="count-line-row" key={l.key}>
              <Field label="Package">
                <select
                  value={l.package_config_id ?? ''}
                  onChange={(e) => onSetLinePackage(l.key, e.target.value === '' ? null : Number(e.target.value))}
                >
                  <option value="">Loose base units</option>
                  {packages.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} (1 = {fmt(p.units_in_base, 0)} {medicine?.base_unit}s)
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Full packages">
                <input
                  type="number"
                  min="0"
                  step="any"
                  value={l.quantity}
                  onChange={(e) => onSetLineValue(l.key, 'quantity', e.target.value)}
                  disabled={l.package_config_id === null && l.quantity === ''}
                />
              </Field>
              <Field label="Cut: units inside" hint={l.package_config_id === null ? '—' : `in ${medicine?.base_unit}s of child level`}>
                <input
                  type="number"
                  min="0"
                  step="any"
                  placeholder="optional"
                  value={l.partial_units}
                  onChange={(e) => onSetLineValue(l.key, 'partial_units', e.target.value)}
                  disabled={l.package_config_id === null}
                />
              </Field>
              <Field label="Base units">
                <input readOnly value={fmt(lineBaseLocal(l, packages))} />
              </Field>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => onRemoveLine(l.key)}>
                ✕
              </button>
            </div>
          ))}
          <div className="btn-row mb">
            <button type="button" className="btn btn-ghost" onClick={onAddLine}>+ Add line</button>
          </div>
          <div className="flex mb" style={{ justifyContent: 'space-between' }}>
            <div className="total-pill">Count total: {total.toLocaleString()} {medicine?.base_unit}s</div>
            <div className="btn-row">
              <button className="btn" onClick={onSubmitCount} disabled={busy || lines.length === 0}>
                {busy ? 'Saving…' : 'Save count'}
              </button>
            </div>
          </div>
        </>
      )}

      {status === 'IN_PROGRESS' && !canOperate && (
        <Alert kind="info">Counts can only be recorded by the session creator ({session.creator_username}).</Alert>
      )}
      {status === 'DRAFT' && (
        <>
          <Alert kind="warning">
            This session has no active medicine yet. Set the medicine to start counting — it is locked for the rest
            of the session.
          </Alert>
          {canOperate && <MedicinePickerForSession busy={busy} onPick={onSetMedicine} />}
        </>
      )}

      <div className="btn-row mb">
        {status === 'IN_PROGRESS' && canOperate && (
          <button className="btn btn-success" onClick={onSubmitForReview} disabled={busy || session.events.length === 0}>
            Submit for review
          </button>
        )}
        {status === 'PENDING_REVIEW' && (
          <>
            <Alert kind="info">
              Waiting for review by someone other than the creator/operator.
            </Alert>
            <button className="btn btn-success" onClick={() => onReview('APPROVED')} disabled={busy}>
              Approve & complete
            </button>
            <button className="btn btn-danger" onClick={() => onReview('REJECTED')} disabled={busy}>
              Reject (back to counting)
            </button>
          </>
        )}
        {['DRAFT', 'IN_PROGRESS', 'PENDING_REVIEW'].includes(status) && canCancel && (
          <button className="btn btn-ghost" onClick={onCancel} disabled={busy}>
            Cancel session
          </button>
        )}
        {status === 'CANCELLED' && canManage && (
          <button className="btn btn-ghost" onClick={onArchive} disabled={busy}>
            Archive session
          </button>
        )}
      </div>


      {status === 'CANCELLED' && (
        <Alert kind="info">
          This session was cancelled. It does not contribute to physical stock. Archive it to hide it from the
          normal list — the record is preserved either way.
        </Alert>
      )}

      {session.events.length > 0 && (
        <>
          <h2>Saved counts</h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>When</th><th>Operator</th><th>Lines</th><th>Total (base)</th></tr>
              </thead>
              <tbody>
                {session.events.map((e) => (
                  <tr key={e.id}>
                    <td>{fmtDate(e.created_at)}</td>
                    <td>{e.operator_username ?? e.operator_id}</td>
                    <td>
                      {e.lines.map((l, i) => (
                        <div key={i} className="mono" style={{ fontSize: 12 }}>
                          {l.is_partial_package
                            ? `cut ${l.package_name ?? 'base'} (${fmt(l.partial_units)}) = ${fmt(l.base_units)}`
                            : `${fmt(l.quantity, 0)} × ${l.package_name ?? medicine?.base_unit ?? 'unit'} = ${fmt(l.base_units)}`}
                        </div>
                      ))}
                    </td>
                    <td><strong>{fmt(e.total_base_units)}</strong></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {session.review_decision && (
        <Alert kind={session.review_decision === 'APPROVED' ? 'success' : 'warning'}>
          Review: {session.review_decision} by {session.reviewer_username ?? '—'}
          {session.review_notes ? ` — “${session.review_notes}”` : ''}
        </Alert>
      )}
    </div>
  )
}

function ActiveSessionCard({
  session,
  busy,
  canCancel,
  canResume,
  isCreator,
  onResume,
  onCancel,
}: {
  session: CountingSessionOut
  busy: boolean
  canCancel: boolean
  canResume: boolean
  isCreator: boolean
  onResume: () => void
  onCancel: () => void
}) {
  return (
    <div className="card" style={{ borderLeft: '4px solid var(--primary)' }}>
      <div className="flex" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h2 style={{ margin: 0 }}>Active counting session</h2>
          <p className="muted" style={{ margin: '4px 0 0' }}>
            Only one counting session can be open at a time. Resume it to continue where you left off, or cancel it
            to unblock new counting.
          </p>
        </div>
        <StatusBadge value={session.status} />
      </div>

      <div className="grid grid-4 mt">
        <div className="stat-card">
          <div className="stat-label">Session ID</div>
          <div className="stat-value mono">#{session.id}</div>
          <div className="stat-label mono">{session.code}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Medicine</div>
          <div className="stat-value" style={{ fontSize: '1.15rem' }}>
            {session.active_medicine_name ?? 'Not set yet'}
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Barcode</div>
          <div className="stat-value mono" style={{ fontSize: '1.15rem' }}>
            {session.active_medicine_barcode ?? '—'}
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Started</div>
          <div className="stat-value" style={{ fontSize: '1.15rem' }}>
            {fmtDate(session.created_at)}
          </div>
          <div className="stat-label">by {session.creator_username ?? '—'}</div>
        </div>
      </div>

      <div className="flex mt" style={{ justifyContent: 'space-between' }}>
        <div className="muted">
          Counted so far: <strong>{fmt(session.total_counted_base_units)}</strong> base units in{' '}
          {session.events.length} count event{session.events.length === 1 ? '' : 's'}
        </div>
        <div className="btn-row">
          {canResume && (
            <button className="btn" onClick={onResume} disabled={busy}>
              {busy ? 'Working…' : 'Resume session'}
            </button>
          )}
          {canCancel && (
            <button className="btn btn-danger" onClick={onCancel} disabled={busy}>
              Cancel session
            </button>
          )}
        </div>
      </div>

      {!isCreator && !canCancel && (
        <p className="muted mt" style={{ marginBottom: 0 }}>
          You can view this session, but only its creator or a user with the count:manage permission can cancel it.
        </p>
      )}
    </div>
  )
}

function MedicinePickerForSession({
  busy,
  onPick,
}: {
  busy: boolean
  onPick: (medicine: MedicineOut) => void
}) {
  const [search, setSearch] = useState('')
  const [results, setResults] = useState<MedicineOut[]>([])

  useEffect(() => {
    if (!search.trim()) {
      setResults([])
      return
    }
    const t = setTimeout(async () => {
      try {
        const page = await api.get<Page<MedicineOut>>(
          `/api/medicines?search=${encodeURIComponent(search.trim())}&page_size=8&is_active=true`,
        )
        setResults(page.items)
      } catch {
        setResults([])
      }
    }, 250)
    return () => clearTimeout(t)
  }, [search])

  return (
    <div className="mb">
      <Field label="Set the medicine to count">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by name, generic or brand…"
          disabled={busy}
        />
      </Field>
      {results.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Stock</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {results.map((m) => (
                <tr key={m.id}>
                  <td>{m.name}</td>
                  <td>
                    {fmt(m.expected_stock)} {m.base_unit}s
                  </td>
                  <td className="right">
                    <button className="btn btn-sm btn-ghost" disabled={busy} onClick={() => onPick(m)}>
                      Use this medicine
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function lineBaseLocal(l: LineDraft, packages: PackageOut[]): number {
  const qty = Number(l.quantity || 0)
  if (l.package_config_id === null) return qty
  const pkg = packages.find((p) => p.id === l.package_config_id)
  if (!pkg) return 0
  if (l.partial_units !== '') {
    const childFactor = pkg.parent_id ? Number(pkg.units_in_base ?? 1) / Number(pkg.units_per_parent) : 1
    return Number(l.partial_units || 0) * childFactor
  }
  return qty * Number(pkg.units_in_base ?? pkg.units_per_parent)
}
