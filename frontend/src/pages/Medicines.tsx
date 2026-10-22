import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Page } from '../lib/api'
import type { MedicineOut } from '../lib/types'
import { Pagination, Spinner, fmt } from '../components/ui'
import { useAuth } from '../context/AuthContext'

export default function Medicines() {
  const { can } = useAuth()
  const [search, setSearch] = useState('')
  const [active, setActive] = useState<'all' | 'active' | 'inactive'>('active')
  const [page, setPage] = useState(1)
  const [data, setData] = useState<Page<MedicineOut> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({ page: String(page), page_size: '25' })
      if (search) params.set('search', search)
      if (active !== 'all') params.set('is_active', active === 'active' ? 'true' : 'false')
      setData(await api.get<Page<MedicineOut>>(`/api/medicines?${params}`))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load medicines')
    } finally {
      setLoading(false)
    }
  }, [page, search, active])

  useEffect(() => {
    const t = setTimeout(() => void load(), 250)
    return () => clearTimeout(t)
  }, [load])

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Medicine Master</h1>
          <p>Search, register and maintain medicines, barcodes and package configurations.</p>
        </div>
        {can('medicine:manage') && (
          <Link className="btn" to="/medicines/new">+ New medicine</Link>
        )}
      </div>

      <div className="card">
        <div className="toolbar">
          <label className="field grow">
            <span className="field-label">Search (name, generic, brand, manufacturer)</span>
            <input value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }} placeholder="Type to search…" />
          </label>
          <label className="field">
            <span className="field-label">Status</span>
            <select value={active} onChange={(e) => { setActive(e.target.value as typeof active); setPage(1) }}>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
              <option value="all">All</option>
            </select>
          </label>
        </div>

        {error && <div className="alert alert-error">{error}</div>}
        {loading && !data ? (
          <Spinner />
        ) : (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Name</th>
                    <th>Generic</th>
                    <th>Strength / Form</th>
                    <th>Base unit</th>
                    <th>Expected stock</th>
                    <th>Reorder</th>
                    <th>Status</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {(data?.items ?? []).map((m) => (
                    <tr key={m.id}>
                      <td className="mono">{m.id}</td>
                      <td>
                        <strong>{m.name}</strong>
                        {m.barcodes[0] && <div className="mono muted">{m.barcodes[0].barcode}</div>}
                      </td>
                      <td>{m.generic_name ?? '—'}</td>
                      <td>{[m.strength, m.dosage_form].filter(Boolean).join(' · ') || '—'}</td>
                      <td>{m.base_unit}s</td>
                      <td>{fmt(m.expected_stock)}</td>
                      <td>{fmt(m.reorder_level)}</td>
                      <td>
                        <span className={`badge ${m.is_active ? 'badge-success' : 'badge-muted'}`}>
                          {m.is_active ? 'ACTIVE' : 'INACTIVE'}
                        </span>
                      </td>
                      <td>
                        {can('medicine:manage') ? (
                          <Link className="btn btn-ghost btn-sm" to={`/medicines/${m.id}/edit`}>Edit</Link>
                        ) : (
                          <Link className="btn btn-ghost btn-sm" to={`/medicines/${m.id}/edit`}>View</Link>
                        )}
                      </td>
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
