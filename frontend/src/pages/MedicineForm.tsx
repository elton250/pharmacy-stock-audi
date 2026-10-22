import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import type { MedicineOut } from '../lib/types'
import { Alert, Field, Spinner, fmt, useToast } from '../components/ui'
import { useAuth } from '../context/AuthContext'

interface PackageDraft {
  name: string
  parent_name: string
  units_per_parent: string
  sort_order: number
}

export default function MedicineForm() {
  const { id } = useParams()
  const isNew = !id || id === 'new'
  const navigate = useNavigate()
  const { can } = useAuth()
  const { element: toast, show } = useToast()
  const editable = can('medicine:manage')
  const canBarcodes = can('barcode:manage')
  const canPackages = can('package:manage')

  const [medicine, setMedicine] = useState<MedicineOut | null>(null)
  const [form, setForm] = useState({
    name: '',
    generic_name: '',
    brand_name: '',
    strength: '',
    dosage_form: '',
    manufacturer: '',
    category: '',
    base_unit: 'tablet',
    opening_stock: '0',
    reorder_level: '0',
  })
  const [barcodes, setBarcodes] = useState<{ barcode: string; is_primary: boolean }[]>([])
  const [packages, setPackages] = useState<PackageDraft[]>([])
  const [newBarcode, setNewBarcode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(!isNew)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (isNew) return
    void (async () => {
      try {
        const m = await api.get<MedicineOut>(`/api/medicines/${id}`)
        setMedicine(m)
        setForm({
          name: m.name,
          generic_name: m.generic_name ?? '',
          brand_name: m.brand_name ?? '',
          strength: m.strength ?? '',
          dosage_form: m.dosage_form ?? '',
          manufacturer: m.manufacturer ?? '',
          category: m.category ?? '',
          base_unit: m.base_unit,
          opening_stock: m.opening_stock,
          reorder_level: m.reorder_level,
        })
        setBarcodes(m.barcodes.map((b) => ({ barcode: b.barcode, is_primary: b.is_primary })))
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load medicine')
      } finally {
        setLoading(false)
      }
    })()
  }, [id, isNew])

  const save = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setSaving(true)
    try {
      if (isNew) {
        const payload = {
          ...form,
          opening_stock: Number(form.opening_stock || 0),
          reorder_level: Number(form.reorder_level || 0),
          barcodes: barcodes.map((b) => ({ barcode: b.barcode, is_primary: b.is_primary })),
          packages: packages.map((p) => ({
            name: p.name,
            parent_name: p.parent_name || null,
            units_per_parent: Number(p.units_per_parent),
            sort_order: p.sort_order,
          })),
        }
        const created = await api.post<MedicineOut>('/api/medicines', payload)
        show('Medicine created')
        navigate(`/medicines/${created.id}/edit`)
      } else {
        const payload: Record<string, unknown> = {
          ...form,
          opening_stock: Number(form.opening_stock || 0),
          reorder_level: Number(form.reorder_level || 0),
        }
        const updated = await api.patch<MedicineOut>(`/api/medicines/${id}`, payload)
        setMedicine(updated)
        show('Medicine saved')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const addBarcode = async () => {
    if (!newBarcode.trim() || !id) return
    try {
      await api.post(`/api/medicines/${id}/barcodes`, { barcode: newBarcode.trim(), is_primary: false })
      const m = await api.get<MedicineOut>(`/api/medicines/${id}`)
      setMedicine(m)
      setBarcodes(m.barcodes.map((b) => ({ barcode: b.barcode, is_primary: b.is_primary })))
      setNewBarcode('')
      show('Barcode registered')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Barcode registration failed')
    }
  }

  const deleteBarcode = async (barcode: string) => {
    const bc = medicine?.barcodes.find((b) => b.barcode === barcode)
    if (!bc) return
    try {
      await api.del(`/api/medicines/barcodes/${bc.id}`)
      const m = await api.get<MedicineOut>(`/api/medicines/${id}`)
      setMedicine(m)
      setBarcodes(m.barcodes.map((b) => ({ barcode: b.barcode, is_primary: b.is_primary })))
      show('Barcode removed')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  const addPackage = async (pkg: PackageDraft) => {
    if (!id) return
    try {
      await api.post(`/api/medicines/${id}/packages`, {
        name: pkg.name,
        parent_name: pkg.parent_name || null,
        parent_id: null,
        units_per_parent: Number(pkg.units_per_parent),
        sort_order: pkg.sort_order,
      })
      // parent_name is only supported on medicine create; set parent afterwards
      const m = await api.get<MedicineOut>(`/api/medicines/${id}`)
      if (pkg.parent_name) {
        const created = m.packages.find((p) => p.name === pkg.name)
        const parent = m.packages.find((p) => p.name === pkg.parent_name)
        if (created && parent && !created.parent_id) {
          await api.put(`/api/medicines/packages/${created.id}`, { parent_id: parent.id })
        }
      }
      const refreshed = await api.get<MedicineOut>(`/api/medicines/${id}`)
      setMedicine(refreshed)
      show('Package added')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Package add failed')
    }
  }

  if (loading) return <Spinner label="Loading medicine…" />

  return (
    <div>
      {toast}
      <div className="page-header">
        <div>
          <h1>{isNew ? 'Register medicine' : `Edit: ${medicine?.name ?? ''}`}</h1>
          <p>
            <Link to="/medicines">← Back to medicines</Link>
            {medicine && <> · Expected stock: <strong>{fmt(medicine.expected_stock)} {medicine.base_unit}s</strong></>}
          </p>
        </div>
      </div>

      {error && <Alert kind="error">{error}</Alert>}

      <form onSubmit={save}>
        <div className="card">
          <h2>Medicine details</h2>
          <div className="grid grid-3">
            <Field label="Medicine name *">
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required disabled={!editable} />
            </Field>
            <Field label="Generic name">
              <input value={form.generic_name} onChange={(e) => setForm({ ...form, generic_name: e.target.value })} disabled={!editable} />
            </Field>
            <Field label="Brand name">
              <input value={form.brand_name} onChange={(e) => setForm({ ...form, brand_name: e.target.value })} disabled={!editable} />
            </Field>
            <Field label="Strength">
              <input value={form.strength} onChange={(e) => setForm({ ...form, strength: e.target.value })} placeholder="500mg" disabled={!editable} />
            </Field>
            <Field label="Dosage form">
              <input value={form.dosage_form} onChange={(e) => setForm({ ...form, dosage_form: e.target.value })} placeholder="Tablet" disabled={!editable} />
            </Field>
            <Field label="Manufacturer">
              <input value={form.manufacturer} onChange={(e) => setForm({ ...form, manufacturer: e.target.value })} disabled={!editable} />
            </Field>
            <Field label="Category">
              <input value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} disabled={!editable} />
            </Field>
            <Field label="Base counting unit" hint="The smallest unit counted, e.g. tablet, capsule, ml">
              <input value={form.base_unit} onChange={(e) => setForm({ ...form, base_unit: e.target.value })} required disabled={!editable || !isNew} />
            </Field>
            <Field label="Opening stock">
              <input type="number" min="0" step="any" value={form.opening_stock} onChange={(e) => setForm({ ...form, opening_stock: e.target.value })} disabled={!editable} />
            </Field>
            <Field label="Reorder level">
              <input type="number" min="0" step="any" value={form.reorder_level} onChange={(e) => setForm({ ...form, reorder_level: e.target.value })} disabled={!editable} />
            </Field>
          </div>
        </div>

        <div className="card">
          <h2>Barcodes</h2>
          <p className="muted">Scanning a registered barcode identifies this medicine. Unknown barcodes are never auto-assigned.</p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Barcode</th><th>Primary</th><th></th></tr>
              </thead>
              <tbody>
                {barcodes.map((b) => (
                  <tr key={b.barcode}>
                    <td className="mono">{b.barcode}</td>
                    <td>{b.is_primary ? <span className="badge badge-success">PRIMARY</span> : <span className="muted">additional</span>}</td>
                    <td className="right">
                      {canBarcodes && !isNew && (
                        <button type="button" className="btn btn-ghost btn-sm" onClick={() => void deleteBarcode(b.barcode)}>
                          Remove
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
                {barcodes.length === 0 && (
                  <tr><td colSpan={3} className="muted">No barcodes registered.</td></tr>
                )}
              </tbody>
            </table>
          </div>
          {!isNew && canBarcodes && (
            <div className="flex mt">
              <input
                className="grow"
                style={{ maxWidth: 320 }}
                placeholder="Scan or type barcode…"
                value={newBarcode}
                onChange={(e) => setNewBarcode(e.target.value)}
              />
              <button type="button" className="btn" onClick={() => void addBarcode()} disabled={!newBarcode.trim()}>
                Register barcode
              </button>
            </div>
          )}
        </div>

        <div className="card">
          <h2>Package configuration</h2>
          <p className="muted">
            Configure medicine-specific conversions (e.g. 1 box = 10 blisters, 1 blister = 10 tablets). Used by the counting workspace.
          </p>
          {isNew ? (
            <>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr><th>Name</th><th>Parent package</th><th>Units per parent</th><th>Order</th><th></th></tr>
                  </thead>
                  <tbody>
                    {packages.map((p, i) => (
                      <tr key={i}>
                        <td>{p.name}</td>
                        <td>{p.parent_name || `1 ${p.name} = N base units`}</td>
                        <td>{p.units_per_parent}</td>
                        <td>{p.sort_order}</td>
                        <td className="right">
                          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setPackages(packages.filter((_, j) => j !== i))}>
                            Remove
                          </button>
                        </td>
                      </tr>
                    ))}
                    {packages.length === 0 && <tr><td colSpan={5} className="muted">No packages yet — counts will use loose base units only.</td></tr>}
                  </tbody>
                </table>
              </div>
              <PackageDraftForm onAdd={(p) => setPackages([...packages, p])} existing={packages.map((p) => p.name)} />
            </>
          ) : (
            <>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr><th>Name</th><th>Contains</th><th>Equals (base units)</th><th></th></tr>
                  </thead>
                  <tbody>
                    {(medicine?.packages ?? []).map((p) => {
                      const parent = p.parent_id ? medicine?.packages.find((x) => x.id === p.parent_id) : null
                      return (
                        <tr key={p.id}>
                          <td>{p.name}</td>
                          <td>{parent ? `1 ${p.name} = ${fmt(p.units_per_parent, 0)} ${parent.name}(s)` : `1 ${p.name} = ${fmt(p.units_per_parent, 0)} base unit(s)`}</td>
                          <td><strong>{fmt(p.units_in_base)}</strong></td>
                          <td className="right">
                            {canPackages && (
                              <button
                                type="button"
                                className="btn btn-ghost btn-sm"
                                onClick={async () => {
                                  try {
                                    await api.del(`/api/medicines/packages/${p.id}`)
                                    const m = await api.get<MedicineOut>(`/api/medicines/${id}`)
                                    setMedicine(m)
                                    show('Package removed')
                                  } catch (err) {
                                    setError(err instanceof Error ? err.message : 'Delete failed')
                                  }
                                }}
                              >
                                Remove
                              </button>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                    {(medicine?.packages ?? []).length === 0 && (
                      <tr><td colSpan={4} className="muted">No packages configured — counts will use loose base units only.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
              {canPackages && (
                <PackageDraftForm
                  onAdd={(p) => void addPackage(p)}
                  existing={(medicine?.packages ?? []).map((p) => p.name)}
                />
              )}
            </>
          )}
        </div>

        {editable && (
          <div className="btn-row">
            <button className="btn" disabled={saving}>{saving ? 'Saving…' : isNew ? 'Create medicine' : 'Save changes'}</button>
            <Link className="btn btn-ghost" to="/medicines">Cancel</Link>
          </div>
        )}
      </form>
    </div>
  )
}

function PackageDraftForm({ onAdd, existing }: { onAdd: (p: PackageDraft) => void; existing: string[] }) {
  const [pkg, setPkg] = useState<PackageDraft>({ name: '', parent_name: '', units_per_parent: '', sort_order: 0 })
  return (
    <div className="flex mt">
      <input placeholder="Name (e.g. Blister)" value={pkg.name} onChange={(e) => setPkg({ ...pkg, name: e.target.value })} style={{ maxWidth: 180 }} />
      <select value={pkg.parent_name} onChange={(e) => setPkg({ ...pkg, parent_name: e.target.value })} style={{ maxWidth: 200 }}>
        <option value="">— base units —</option>
        {existing.filter((n) => n !== pkg.name).map((n) => (
          <option key={n} value={n}>{n}</option>
        ))}
      </select>
      <input
        type="number"
        min="0.001"
        step="any"
        placeholder="Units per parent"
        value={pkg.units_per_parent}
        onChange={(e) => setPkg({ ...pkg, units_per_parent: e.target.value })}
        style={{ maxWidth: 150 }}
      />
      <button
        type="button"
        className="btn"
        disabled={!pkg.name.trim() || !pkg.units_per_parent || Number(pkg.units_per_parent) <= 0}
        onClick={() => {
          onAdd({ ...pkg, sort_order: existing.length + 1 })
          setPkg({ name: '', parent_name: '', units_per_parent: '', sort_order: 0 })
        }}
      >
        Add package
      </button>
    </div>
  )
}
