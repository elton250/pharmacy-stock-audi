import { useEffect, useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { api } from '../lib/api'
import type { VisionStatus } from '../lib/types'

const NAV: { to: string; label: string; perm?: string; needsVision?: boolean }[] = [
  { to: '/', label: 'Dashboard' },
  { to: '/scan', label: 'Scan', perm: 'medicine:view' },
  { to: '/vision', label: 'Vision Scan', perm: 'medicine:view', needsVision: true },
  { to: '/counting', label: 'Static Counting', perm: 'count:perform' },
  { to: '/medicines', label: 'Medicines', perm: 'medicine:view' },
  { to: '/inventory', label: 'Inventory', perm: 'inventory:view' },
  { to: '/stock-in', label: 'Stock In', perm: 'inventory:transact' },
  { to: '/sales', label: 'Sales', perm: 'inventory:transact' },
  { to: '/expenses', label: 'Expenses', perm: 'inventory:transact' },
  { to: '/audits', label: 'Stock Audits', perm: 'audit:view' },
  { to: '/reports', label: 'Reports', perm: 'report:view' },
  { to: '/users', label: 'Users', perm: 'user:manage' },
  { to: '/audit-logs', label: 'Audit Log', perm: 'auditlog:view' },
]

export default function Layout() {
  const { user, can, logout } = useAuth()
  const navigate = useNavigate()

  // The vision feature is only offered when the backend confirms that it is
  // enabled. A backend that does not know the feature (404), a backend with
  // the feature disabled and an unreachable backend all hide the entry —
  // it fails closed, so nobody is offered something that cannot work.
  const [visionAvailable, setVisionAvailable] = useState(false)

  useEffect(() => {
    if (!can('medicine:view')) return
    let cancelled = false
    api
      .get<VisionStatus>('/api/vision/status')
      .then((value) => {
        if (!cancelled) setVisionAvailable(value?.enabled === true)
      })
      .catch(() => {
        if (!cancelled) setVisionAvailable(false)
      })
    return () => {
      cancelled = true
    }
  }, [can])

  // Derived during render so that a permission change alone can hide the entry.
  const showVision = can('medicine:view') && visionAvailable

  const handleLogout = async () => {
    await logout()
    navigate('/login')
  }

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">Rx</span>
          <div>
            <strong>Pharmacy Audit</strong>
            <div className="brand-sub">Stock Auditing System</div>
          </div>
        </div>
        <nav>
          {NAV.filter((item) => !item.perm || can(item.perm))
            .filter((item) => !item.needsVision || showVision)
            .map((item) => (
            <NavLink key={item.to} to={item.to} end={item.to === '/'} className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="user-chip">
            <div className="avatar">{(user?.username ?? '?')[0].toUpperCase()}</div>
            <div>
              <div className="user-name">{user?.full_name || user?.username}</div>
              <div className="user-roles">{user?.roles.map((r) => r.name).join(', ')}</div>
            </div>
          </div>
          <button className="btn btn-ghost btn-block" onClick={handleLogout}>
            Sign out
          </button>
        </div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  )
}
