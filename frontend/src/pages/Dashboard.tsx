import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Page } from '../lib/api'
import type { CountingSessionOut, MedicineOut, StockAuditOut, VarianceReportRow } from '../lib/types'
import { Spinner, StatusBadge, fmt, fmtDate } from '../components/ui'
import { useAuth } from '../context/AuthContext'

export default function Dashboard() {
  const { user, can } = useAuth()
  const [medicines, setMedicines] = useState<number | null>(null)
  const [openSessions, setOpenSessions] = useState<number | null>(null)
  const [openAudits, setOpenAudits] = useState<number | null>(null)
  const [variances, setVariances] = useState<VarianceReportRow[]>([])
  const [recentSessions, setRecentSessions] = useState<CountingSessionOut[]>([])
  const [lowStock, setLowStock] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const load = async () => {
      setLoading(true)
      try {
        if (can('medicine:view')) {
          const meds = await api.get<Page<MedicineOut>>('/api/medicines?is_active=true&page_size=1')
          setMedicines(meds.total)
        }
        if (can('count:perform')) {
          const draft = await api.get<Page<CountingSessionOut>>('/api/counting/sessions?status=DRAFT&page_size=1')
          const prog = await api.get<Page<CountingSessionOut>>('/api/counting/sessions?status=IN_PROGRESS&page_size=1')
          const pending = await api.get<Page<CountingSessionOut>>('/api/counting/sessions?status=PENDING_REVIEW&page_size=1')
          setOpenSessions(draft.total + prog.total + pending.total)
          const recent = await api.get<Page<CountingSessionOut>>('/api/counting/sessions?page_size=5')
          setRecentSessions(recent.items)
        }
        if (can('audit:view')) {
          const audits = await api.get<Page<StockAuditOut>>('/api/audits?status=OPEN&page_size=1')
          setOpenAudits(audits.total)
        }
        if (can('report:view')) {
          const report = await api.get<Page<VarianceReportRow>>('/api/reports/variance?page_size=100')
          setVariances(report.items)
          const levels = await api.get<Page<{ below_reorder: boolean }>>('/api/reports/stock-levels?page_size=200')
          setLowStock(levels.items.filter((r) => r.below_reorder).length)
        }
      } finally {
        setLoading(false)
      }
    }
    void load()
  }, [can])

  if (loading) return <Spinner label="Loading dashboard…" />

  const issues = variances.filter((v) => v.classification === 'SHORTAGE' || v.classification === 'SURPLUS')

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Dashboard</h1>
          <p>Welcome back, {user?.full_name || user?.username}.</p>
        </div>
      </div>

      <div className="grid grid-4 mb">
        {can('medicine:view') && (
          <div className="card stat-card">
            <span className="stat-value">{medicines ?? '—'}</span>
            <span className="stat-label">Active medicines</span>
          </div>
        )}
        {can('count:perform') && (
          <div className="card stat-card">
            <span className="stat-value">{openSessions ?? '—'}</span>
            <span className="stat-label">Open counting sessions</span>
          </div>
        )}
        {can('audit:view') && (
          <div className="card stat-card">
            <span className="stat-value">{openAudits ?? '—'}</span>
            <span className="stat-label">Open stock audits</span>
          </div>
        )}
        {can('report:view') && (
          <div className="card stat-card">
            <span className="stat-value">{lowStock ?? '—'}</span>
            <span className="stat-label">At/below reorder level</span>
          </div>
        )}
      </div>

      {can('report:view') && (
        <div className="card">
          <h2>Latest variances</h2>
          {issues.length === 0 ? (
            <p className="muted">No shortages or surpluses in the latest counts.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Medicine</th>
                    <th>Expected</th>
                    <th>Physical</th>
                    <th>Variance</th>
                    <th>Class</th>
                    <th>Audit date</th>
                  </tr>
                </thead>
                <tbody>
                  {issues.slice(0, 8).map((v) => (
                    <tr key={v.medicine_id}>
                      <td>{v.medicine_name}</td>
                      <td>{fmt(v.expected_stock)}</td>
                      <td>{fmt(v.physical_stock)}</td>
                      <td>{fmt(v.variance)}</td>
                      <td><StatusBadge value={v.classification} /></td>
                      <td>{v.audit_date ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {can('count:perform') && (
        <div className="card">
          <div className="flex" style={{ justifyContent: 'space-between' }}>
            <h2>Recent counting sessions</h2>
            <Link className="btn btn-sm" to="/counting">Open counting workspace</Link>
          </div>
          {recentSessions.length === 0 ? (
            <p className="muted">No counting sessions yet.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Code</th>
                    <th>Medicine</th>
                    <th>Status</th>
                    <th>Counted (base units)</th>
                    <th>Created</th>
                  </tr>
                </thead>
                <tbody>
                  {recentSessions.map((s) => (
                    <tr key={s.id}>
                      <td className="mono">{s.code}</td>
                      <td>{s.active_medicine_name ?? '—'}</td>
                      <td><StatusBadge value={s.status} /></td>
                      <td>{fmt(s.total_counted_base_units)}</td>
                      <td>{fmtDate(s.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
