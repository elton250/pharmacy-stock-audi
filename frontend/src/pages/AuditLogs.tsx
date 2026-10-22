import { Fragment, useCallback, useEffect, useState } from 'react'
import { api, type Page } from '../lib/api'
import type { AuditLogModel } from '../lib/types'
import { Alert, Pagination, Spinner, fmtDate } from '../components/ui'

export default function AuditLogs() {
  const [data, setData] = useState<Page<AuditLogModel> | null>(null)
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState({ action: '', entity_type: '', actor_username: '' })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<number | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({ page: String(page), page_size: '50' })
      if (filters.action) params.set('action', filters.action)
      if (filters.entity_type) params.set('entity_type', filters.entity_type)
      if (filters.actor_username) params.set('actor_username', filters.actor_username)
      setData(await api.get<Page<AuditLogModel>>(`/api/audit-logs?${params}`))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load audit log')
    } finally {
      setLoading(false)
    }
  }, [page, filters])

  useEffect(() => {
    const t = setTimeout(() => void load(), 200)
    return () => clearTimeout(t)
  }, [load])

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Audit Log</h1>
          <p>Append-only record of logins, user changes, medicine edits, transactions, counts and reviews.</p>
        </div>
      </div>

      {error && <Alert kind="error">{error}</Alert>}

      <div className="card">
        <div className="toolbar">
          <label className="field">
            <span className="field-label">Action</span>
            <input
              value={filters.action}
              onChange={(e) => { setFilters({ ...filters, action: e.target.value }); setPage(1) }}
              placeholder="e.g. medicine.create"
            />
          </label>
          <label className="field">
            <span className="field-label">Entity type</span>
            <input
              value={filters.entity_type}
              onChange={(e) => { setFilters({ ...filters, entity_type: e.target.value }); setPage(1) }}
              placeholder="e.g. medicine"
            />
          </label>
          <label className="field">
            <span className="field-label">User</span>
            <input
              value={filters.actor_username}
              onChange={(e) => { setFilters({ ...filters, actor_username: e.target.value }); setPage(1) }}
              placeholder="username"
            />
          </label>
        </div>

        {loading && !data ? (
          <Spinner />
        ) : (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Actor</th>
                    <th>Action</th>
                    <th>Entity</th>
                    <th>IP</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {(data?.items ?? []).map((log) => (
                    <Fragment key={log.id}>
                      <tr>
                        <td>{fmtDate(log.created_at)}</td>
                        <td>{log.actor_username ?? '—'}</td>
                        <td><span className="badge badge-info">{log.action}</span></td>
                        <td className="mono">{log.entity_type}{log.entity_id ? ` #${log.entity_id}` : ''}</td>
                        <td className="mono muted">{log.ip_address ?? '—'}</td>
                        <td className="right">
                          <button
                            className="btn btn-sm btn-ghost"
                            onClick={() => setExpanded(expanded === log.id ? null : log.id)}
                          >
                            {expanded === log.id ? 'Hide' : 'Details'}
                          </button>
                        </td>
                      </tr>
                      {expanded === log.id && (
                        <tr key={`${log.id}-detail`}>
                          <td colSpan={6}>
                            <div className="grid grid-2">
                              <div>
                                <strong>Before</strong>
                                <pre className="mono" style={{ background: '#f6f8fb', padding: 10, borderRadius: 8, overflowX: 'auto' }}>
                                  {log.before_data ? JSON.stringify(log.before_data, null, 2) : '—'}
                                </pre>
                              </div>
                              <div>
                                <strong>After</strong>
                                <pre className="mono" style={{ background: '#f6f8fb', padding: 10, borderRadius: 8, overflowX: 'auto' }}>
                                  {log.after_data ? JSON.stringify(log.after_data, null, 2) : '—'}
                                </pre>
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
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
