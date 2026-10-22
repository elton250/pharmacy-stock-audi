import { useCallback, useEffect, useState } from 'react'
import { api, type Page } from '../lib/api'
import type { StockLevelRow, VarianceReportRow } from '../lib/types'
import { Alert, Pagination, Spinner, StatusBadge, fmt } from '../components/ui'

export default function Reports() {
  const [tab, setTab] = useState<'variance' | 'levels'>('variance')
  const [rows, setRows] = useState<Page<VarianceReportRow> | Page<StockLevelRow> | null>(null)
  const [page, setPage] = useState(1)
  const [classFilter, setClassFilter] = useState<'ALL' | 'SHORTAGE' | 'SURPLUS' | 'MATCH' | 'UNCOUNTED'>('ALL')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      if (tab === 'variance') {
        const res = await api.get<Page<VarianceReportRow>>(`/api/reports/variance?page=${page}&page_size=50`)
        const filtered =
          classFilter === 'ALL' ? res.items : res.items.filter((r) => r.classification === classFilter)
        setRows({ ...res, items: filtered })
      } else {
        setRows(await api.get<Page<StockLevelRow>>(`/api/reports/stock-levels?page=${page}&page_size=50`))
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load report')
    } finally {
      setLoading(false)
    }
  }, [tab, page, classFilter])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Reports</h1>
          <p>Expected vs physical stock, variances and reorder levels.</p>
        </div>
        <div className="btn-row">
          <button className={`btn ${tab === 'variance' ? '' : 'btn-ghost'}`} onClick={() => { setTab('variance'); setPage(1) }}>
            Variance report
          </button>
          <button className={`btn ${tab === 'levels' ? '' : 'btn-ghost'}`} onClick={() => { setTab('levels'); setPage(1) }}>
            Stock levels
          </button>
        </div>
      </div>

      {error && <Alert kind="error">{error}</Alert>}

      <div className="card">
        {tab === 'variance' && (
          <div className="toolbar">
            <label className="field">
              <span className="field-label">Classification</span>
              <select value={classFilter} onChange={(e) => { setClassFilter(e.target.value as typeof classFilter); setPage(1) }}>
                <option value="ALL">All</option>
                <option value="SHORTAGE">Shortage</option>
                <option value="SURPLUS">Surplus</option>
                <option value="MATCH">Match</option>
                <option value="UNCOUNTED">Uncounted</option>
              </select>
            </label>
          </div>
        )}

        {loading && !rows ? (
          <Spinner />
        ) : tab === 'variance' ? (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Medicine</th>
                    <th>Opening</th>
                    <th>Recd</th>
                    <th>Issued</th>
                    <th>± Adj</th>
                    <th>Expected</th>
                    <th>Physical</th>
                    <th>Variance</th>
                    <th>Class</th>
                    <th>Operator / Reviewer</th>
                    <th>Session</th>
                  </tr>
                </thead>
                <tbody>
                  {((rows as Page<VarianceReportRow>).items ?? []).map((r) => (
                    <tr key={r.medicine_id}>
                      <td>
                        <strong>{r.medicine_name}</strong>
                        {r.generic_name && <div className="muted">{r.generic_name}</div>}
                      </td>
                      <td>{fmt(r.opening_stock, 0)}</td>
                      <td>{fmt(r.received, 0)}</td>
                      <td>{fmt(r.issued, 0)}</td>
                      <td>
                        {fmt(Number(r.adjustments_positive) - Number(r.adjustments_negative), 0)}
                      </td>
                      <td><strong>{fmt(r.expected_stock)}</strong></td>
                      <td>{fmt(r.physical_stock)}</td>
                      <td>
                        {r.variance === null || r.variance === undefined ? '—' : (
                          <strong style={{ color: Number(r.variance) < 0 ? 'var(--danger)' : Number(r.variance) > 0 ? 'var(--warning)' : 'var(--success)' }}>
                            {Number(r.variance) > 0 ? '+' : ''}{fmt(r.variance)}
                          </strong>
                        )}
                      </td>
                      <td><StatusBadge value={r.classification} /></td>
                      <td className="muted">
                        {r.counting_operator ?? '—'}
                        {r.reviewer && <> / {r.reviewer}</>}
                      </td>
                      <td className="mono">{r.counting_session_code ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {rows && <Pagination page={rows.page} pageSize={rows.page_size} total={rows.total} onPage={setPage} />}
          </>
        ) : (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Medicine</th>
                    <th>Base unit</th>
                    <th>Expected stock</th>
                    <th>Reorder level</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {((rows as Page<StockLevelRow>).items ?? []).map((r) => (
                    <tr key={r.medicine_id}>
                      <td>{r.medicine_name}</td>
                      <td>{r.base_unit}s</td>
                      <td><strong>{fmt(r.expected_stock)}</strong></td>
                      <td>{fmt(r.reorder_level)}</td>
                      <td>
                        {r.below_reorder ? <span className="badge badge-danger">BELOW REORDER</span> : <span className="badge badge-success">OK</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {rows && <Pagination page={rows.page} pageSize={rows.page_size} total={rows.total} onPage={setPage} />}
          </>
        )}
      </div>
    </div>
  )
}
