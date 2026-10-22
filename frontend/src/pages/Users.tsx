import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { api, type Page } from '../lib/api'
import type { UserOut } from '../lib/types'
import { Alert, ConfirmDialog, Field, Pagination, Spinner, fmtDate, useToast } from '../components/ui'

interface RoleOption {
  id: number
  name: string
  description: string
  permissions: string[]
}

export default function Users() {
  const { element: toast, show } = useToast()
  const [data, setData] = useState<Page<UserOut> | null>(null)
  const [roles, setRoles] = useState<RoleOption[]>([])
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({ username: '', password: '', full_name: '', email: '', role_names: ['stock_counter'] as string[] })
  const [editing, setEditing] = useState<UserOut | null>(null)
  const [editForm, setEditForm] = useState({ full_name: '', email: '', is_active: true, password: '', role_names: [] as string[] })
  const [confirm, setConfirm] = useState<null | { title: string; message: string; action: () => Promise<void> }>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ page: String(page), page_size: '25' })
      if (search) params.set('search', search)
      setData(await api.get<Page<UserOut>>(`/api/users?${params}`))
      setRoles(await api.get<RoleOption[]>('/api/users/roles/all'))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load users')
    } finally {
      setLoading(false)
    }
  }, [page, search])

  useEffect(() => {
    const t = setTimeout(() => void load(), 200)
    return () => clearTimeout(t)
  }, [load])

  const createUser = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.post('/api/users', form)
      show('User created')
      setCreating(false)
      setForm({ username: '', password: '', full_name: '', email: '', role_names: ['stock_counter'] })
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create user')
    } finally {
      setBusy(false)
    }
  }

  const saveEdit = async (e: FormEvent) => {
    e.preventDefault()
    if (!editing) return
    setBusy(true)
    setError(null)
    try {
      const payload: Record<string, unknown> = {
        full_name: editForm.full_name,
        email: editForm.email || null,
        is_active: editForm.is_active,
        role_names: editForm.role_names,
      }
      if (editForm.password) payload.password = editForm.password
      await api.patch(`/api/users/${editing.id}`, payload)
      show('User updated')
      setEditing(null)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update user')
    } finally {
      setBusy(false)
    }
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
          <h1>User Management</h1>
          <p>Create accounts, assign roles and control access. All changes are audit-logged.</p>
        </div>
        <button className="btn" onClick={() => setCreating((c) => !c)}>{creating ? 'Close' : '+ New user'}</button>
      </div>

      {error && <Alert kind="error">{error}</Alert>}

      {creating && (
        <div className="card">
          <h2>Create user</h2>
          <form onSubmit={createUser}>
            <div className="grid grid-2">
              <Field label="Username *">
                <input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required minLength={3} />
              </Field>
              <Field label="Full name *">
                <input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} required />
              </Field>
              <Field label="Password * (min 8)">
                <input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required minLength={8} />
              </Field>
              <Field label="Email">
                <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
              </Field>
            </div>
            <RolePicker roles={roles} selected={form.role_names} onChange={(r) => setForm({ ...form, role_names: r })} />
            <button className="btn" disabled={busy}>{busy ? 'Creating…' : 'Create user'}</button>
          </form>
        </div>
      )}

      {editing && (
        <div className="card">
          <h2>Edit: {editing.username}</h2>
          <form onSubmit={saveEdit}>
            <div className="grid grid-2">
              <Field label="Full name">
                <input value={editForm.full_name} onChange={(e) => setEditForm({ ...editForm, full_name: e.target.value })} />
              </Field>
              <Field label="Email">
                <input value={editForm.email ?? ''} onChange={(e) => setEditForm({ ...editForm, email: e.target.value })} />
              </Field>
              <Field label="New password (leave blank to keep)">
                <input type="password" value={editForm.password} onChange={(e) => setEditForm({ ...editForm, password: e.target.value })} minLength={8} />
              </Field>
              <Field label="Active">
                <select
                  value={editForm.is_active ? 'yes' : 'no'}
                  onChange={(e) => setEditForm({ ...editForm, is_active: e.target.value === 'yes' })}
                >
                  <option value="yes">Active</option>
                  <option value="no">Deactivated</option>
                </select>
              </Field>
            </div>
            <RolePicker roles={roles} selected={editForm.role_names} onChange={(r) => setEditForm({ ...editForm, role_names: r })} />
            <div className="btn-row">
              <button className="btn" disabled={busy}>{busy ? 'Saving…' : 'Save changes'}</button>
              <button type="button" className="btn btn-ghost" onClick={() => setEditing(null)}>Cancel</button>
            </div>
          </form>
        </div>
      )}

      <div className="card">
        <div className="toolbar">
          <label className="field grow">
            <span className="field-label">Search users</span>
            <input value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }} placeholder="Username or name…" />
          </label>
        </div>
        {loading && !data ? (
          <Spinner />
        ) : (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>Username</th><th>Name</th><th>Roles</th><th>Status</th><th>Created</th><th></th></tr>
                </thead>
                <tbody>
                  {(data?.items ?? []).map((u) => (
                    <tr key={u.id}>
                      <td className="mono">{u.username}</td>
                      <td>{u.full_name}</td>
                      <td>{u.roles.map((r) => <span key={r.id} className="badge badge-info" style={{ marginRight: 4 }}>{r.name}</span>)}</td>
                      <td>
                        <span className={`badge ${u.is_active ? 'badge-success' : 'badge-muted'}`}>
                          {u.is_active ? 'ACTIVE' : 'DEACTIVATED'}
                        </span>
                      </td>
                      <td>{fmtDate(u.created_at)}</td>
                      <td className="right">
                        <button
                          className="btn btn-sm btn-ghost"
                          onClick={() => {
                            setEditing(u)
                            setEditForm({
                              full_name: u.full_name,
                              email: u.email ?? '',
                              is_active: u.is_active,
                              password: '',
                              role_names: u.roles.map((r) => r.name),
                            })
                          }}
                        >
                          Edit
                        </button>
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

function RolePicker({
  roles,
  selected,
  onChange,
}: {
  roles: RoleOption[]
  selected: string[]
  onChange: (roles: string[]) => void
}) {
  return (
    <div className="field">
      <span className="field-label">Roles</span>
      <div className="flex">
        {roles.map((r) => {
          const on = selected.includes(r.name)
          return (
            <label key={r.id} className={`badge ${on ? 'badge-info' : 'badge-muted'}`} style={{ cursor: 'pointer', padding: '6px 12px' }}>
              <input
                type="checkbox"
                checked={on}
                style={{ width: 'auto', marginRight: 6 }}
                onChange={() => onChange(on ? selected.filter((n) => n !== r.name) : [...selected, r.name])}
              />
              {r.name}
            </label>
          )
        })}
      </div>
      <span className="field-hint">
        {roles
          .filter((r) => selected.includes(r.name))
          .map((r) => `${r.name}: ${r.permissions.length} permissions`)
          .join(' · ')}
      </span>
    </div>
  )
}
