import { useEffect, useState, type ReactNode } from 'react'

export function Spinner({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="spinner-wrap" role="status">
      <div className="spinner" />
      <span>{label}</span>
    </div>
  )
}

export function Alert({ kind, children }: { kind: 'error' | 'success' | 'info' | 'warning'; children: ReactNode }) {
  return <div className={`alert alert-${kind}`}>{children}</div>
}

export function Toast({ message, kind, onClose }: { message: string; kind: 'success' | 'error'; onClose: () => void }) {
  useEffect(() => {
    const t = setTimeout(onClose, 4000)
    return () => clearTimeout(t)
  }, [onClose])
  return (
    <div className={`toast toast-${kind}`} onClick={onClose}>
      {message}
    </div>
  )
}

export function ConfirmDialog({
  title,
  message,
  onConfirm,
  onCancel,
  busy,
  children,
}: {
  title: string
  message: string
  onConfirm: () => void
  onCancel: () => void
  busy?: boolean
  children?: ReactNode
}) {
  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>{title}</h3>
        <p>{message}</p>
        {children}
        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button className="btn btn-danger" onClick={onConfirm} disabled={busy}>
            {busy ? 'Working…' : 'Confirm'}
          </button>
        </div>
      </div>
    </div>
  )
}

export function Pagination({
  page,
  pageSize,
  total,
  onPage,
}: {
  page: number
  pageSize: number
  total: number
  onPage: (p: number) => void
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  if (total <= pageSize) return null
  return (
    <div className="pagination">
      <button className="btn btn-ghost" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        ← Prev
      </button>
      <span>
        Page {page} of {pages} · {total} records
      </span>
      <button className="btn btn-ghost" disabled={page >= pages} onClick={() => onPage(page + 1)}>
        Next →
      </button>
    </div>
  )
}

export function StatusBadge({ value }: { value?: string | null }) {
  if (!value) return <span className="badge">—</span>
  const cls =
    ({
      MATCH: 'badge-success',
      COMPLETED: 'badge-success',
      SURPLUS: 'badge-info',
      IN_PROGRESS: 'badge-info',
      SHORTAGE: 'badge-danger',
      CANCELLED: 'badge-muted',
      DRAFT: 'badge-muted',
      PENDING_REVIEW: 'badge-warning',
      OPEN: 'badge-info',
      UNCOUNTED: 'badge-muted',
    } as Record<string, string>)[value] ?? 'badge'
  return <span className={`badge ${cls}`}>{value.replaceAll('_', ' ')}</span>
}

export function Field({
  label,
  children,
  hint,
}: {
  label: string
  children: ReactNode
  hint?: string
}) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  )
}

export function useToast() {
  const [toast, setToast] = useState<{ message: string; kind: 'success' | 'error' } | null>(null)
  const element = toast ? (
    <Toast message={toast.message} kind={toast.kind} onClose={() => setToast(null)} />
  ) : null
  const show = (message: string, kind: 'success' | 'error' = 'success') => setToast({ message, kind })
  return { element, show }
}

export function fmt(value: string | number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || value === '') return '—'
  const n = Number(value)
  if (Number.isNaN(n)) return String(value)
  return n.toLocaleString(undefined, { maximumFractionDigits: digits })
}

export function fmtDate(value?: string | null): string {
  if (!value) return '—'
  const tz = localStorage.getItem('pharmacy_tz') || undefined
  try {
    return new Date(value).toLocaleString(undefined, {
      timeZone: tz,
      dateStyle: 'medium',
      timeStyle: 'short',
    })
  } catch {
    return value
  }
}
