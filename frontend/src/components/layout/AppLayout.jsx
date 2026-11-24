import { useEffect, useRef, useState } from "react";
import { ChevronDown, Loader2, LogOut, Menu, RefreshCw, ShieldCheck, X } from "lucide-react";
import { PageHeader, ThemeToggle } from "../ui";

function ProfileMenu({ user, onLogout }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const closeOnOutside = (event) => { if (!rootRef.current?.contains(event.target)) setOpen(false); };
    const closeOnEscape = (event) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", closeOnOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);
  const initials = (user.full_name || user.email || "RW").split(/[\s@]+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("");
  const roleLabel = user.role === "administrator" ? "Administrator" : user.role === "admin" ? "Admin" : "User";
  return (
    <div className="profile-menu" ref={rootRef}>
      <button className="profile-trigger" type="button" aria-expanded={open} aria-haspopup="menu" onClick={() => setOpen((value) => !value)}>
        <span className="profile-avatar" aria-hidden="true">{initials || "RW"}</span>
        <span className="profile-copy"><strong>{user.full_name}</strong><small>{roleLabel}</small></span>
        <ChevronDown size={15} aria-hidden="true" />
      </button>
      {open && <div className="profile-dropdown" role="menu"><div className="profile-dropdown-heading"><strong>{user.full_name}</strong><span>{user.email}</span></div><button role="menuitem" type="button" onClick={() => { setOpen(false); onLogout(); }}><LogOut size={16} />Sign out</button></div>}
    </div>
  );
}

export default function AppLayout({
  navigation,
  activePath,
  pageTitle,
  pageDescription,
  user,
  notificationUnreadCount = 0,
  connected,
  lastSync,
  refreshing,
  onRefresh,
  onLogout,
  onNavigate,
  theme,
  onToggleTheme,
  children,
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const closeMobile = () => setMobileOpen(false);
  useEffect(() => {
    if (!mobileOpen) return undefined;
    const closeOnEscape = (event) => { if (event.key === "Escape") closeMobile(); };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [mobileOpen]);

  return (
    <div className={`app-shell ${collapsed ? "nav-collapsed" : ""} ${mobileOpen ? "mobile-nav-open" : ""}`}>
      {mobileOpen && <button className="sidebar-backdrop" aria-label="Close navigation" onClick={closeMobile} />}
      <aside className={`sidebar ${collapsed ? "collapsed" : ""} ${mobileOpen ? "mobile-open" : ""}`} aria-label="Main navigation">
        <div className="brand">
          <div className="brand-mark"><ShieldCheck size={22} aria-hidden="true" /></div>
          {!collapsed && <div className="brand-copy"><h1>RoadWatch AI</h1><p>Road intelligence platform</p></div>}
          <button className="mobile-close" aria-label="Close navigation" onClick={closeMobile}><X size={18} /></button>
        </div>
        <p className="nav-section-label">WORKSPACE</p>
        <nav>
          {navigation.map(([label, Icon, path]) => (
            <button key={label} className={`nav-item ${activePath === path ? "active" : ""}`} onClick={() => { onNavigate(path); closeMobile(); }} title={collapsed ? label : undefined} aria-label={label} aria-current={activePath === path ? "page" : undefined}>
              <Icon size={18} aria-hidden="true" />{!collapsed && <span>{label}</span>}
              {label.toLowerCase().includes("notification") && notificationUnreadCount > 0 && <span className="nav-notification-count" aria-label={`${notificationUnreadCount} unread notifications`}>{notificationUnreadCount > 99 ? "99+" : notificationUnreadCount}</span>}
            </button>
          ))}
        </nav>
        <div className="sidebar-footer"><span className="sidebar-status-dot" /><span>{collapsed ? "Online" : "RoadWatch services available"}</span></div>
        <button className="collapse-button" type="button" onClick={() => setCollapsed((value) => !value)} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
          <Menu size={17} aria-hidden="true" />{!collapsed && <span>Collapse navigation</span>}
        </button>
      </aside>
      <main className="app-main">
        <header className="app-topbar">
          <button className="mobile-menu-button" aria-label="Open navigation" onClick={() => setMobileOpen(true)}><Menu size={20} /></button>
          <PageHeader title={pageTitle} description={pageDescription} />
          <div className="topbar-actions">
            <div className={`sync-status ${connected ? "online" : "offline"}`}><span className="connection-dot" /><span>{connected ? "Backend connected" : "Backend unavailable"}</span><small>{lastSync ? `Updated ${lastSync.toLocaleTimeString()}` : "Waiting for sync"}</small></div>
            <ThemeToggle theme={theme} onToggle={onToggleTheme} />
            <button className="icon-button refresh-button" type="button" onClick={() => onRefresh()} title="Refresh data" aria-label="Refresh data" disabled={refreshing}>{refreshing ? <Loader2 className="spin" size={17} /> : <RefreshCw size={17} />}</button>
            <ProfileMenu user={user} onLogout={onLogout} />
          </div>
        </header>
        <div className="workspace">{children}</div>
      </main>
    </div>
  );
}
