import { Loader2, Moon, Sun } from "lucide-react";

export function ThemeToggle({ theme, onToggle, className = "" }) {
  const isDark = theme === "dark";
  const Icon = isDark ? Sun : Moon;
  const nextTheme = isDark ? "light" : "dark";
  return (
    <button
      className={`theme-toggle ${className}`.trim()}
      type="button"
      onClick={onToggle}
      aria-label={`Switch to ${nextTheme} mode`}
      aria-pressed={isDark}
      title={`Switch to ${nextTheme} mode`}
    >
      <Icon size={17} aria-hidden="true" />
      <span>{isDark ? "Light mode" : "Dark mode"}</span>
    </button>
  );
}

export function Panel({ title, subtitle, action, className = "", children }) {
  return (
    <section className={`panel ${className}`.trim()}>
      {(title || subtitle || action) && (
        <header className="panel-head">
          <div className="panel-heading-copy">{title && <h3>{title}</h3>}{subtitle && <p>{subtitle}</p>}</div>
          {action && <div className="panel-action">{action}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function PageHeader({ eyebrow = "ROAD INTELLIGENCE", title, description, actions }) {
  return (
    <div className="page-header">
      <div><p className="page-eyebrow">{eyebrow}</p><h1>{title}</h1>{description && <p className="page-description">{description}</p>}</div>
      {actions && <div className="page-actions">{actions}</div>}
    </div>
  );
}

export function StatCard({ title, value, detail, icon: Icon, tone = "blue" }) {
  return (
    <article className="stat-card">
      <span className={`metric-icon ${tone}`}><Icon size={19} aria-hidden="true" /></span>
      <div className="stat-card-copy"><p>{title}</p><strong>{value ?? "—"}</strong>{detail && <small>{detail}</small>}</div>
    </article>
  );
}

export function StatusBadge({ value, tone }) {
  const rawLabel = String(value ?? "Unknown").replaceAll("_", " ");
  const label = ["pending", "approved", "rejected"].includes(rawLabel.toLowerCase())
    ? `${rawLabel[0].toUpperCase()}${rawLabel.slice(1).toLowerCase()}`
    : rawLabel;
  const normalized = String(tone || value || "unknown").toLowerCase().replaceAll(" ", "_");
  const statusTone = ["critical", "high", "rejected", "inactive", "error"].includes(normalized)
    ? "danger"
    : ["medium", "pending", "pending_verification", "awaiting_verification", "unverified", "under_maintenance", "in_progress", "repair_in_progress"].includes(normalized)
      ? "warning"
      : ["low", "verified", "repaired", "resolved", "active", "approved", "repair_verified", "connected"].includes(normalized)
        ? "success"
        : "neutral";
  return <span className={`status-badge ${statusTone}`}>{label}</span>;
}

export function EmptyState({ icon: Icon, title, body, action }) {
  return (
    <div className="empty-state">
      {Icon && <span className="empty-state-icon"><Icon size={22} aria-hidden="true" /></span>}
      <strong>{title}</strong><span>{body}</span>{action}
    </div>
  );
}

export function LoadingState({ label = "Loading RoadWatch AI data..." }) {
  return <div className="loading-state" role="status"><Loader2 className="spin" size={20} aria-hidden="true" /><span>{label}</span></div>;
}

export function DataTable({ children, className = "" }) {
  return <div className={`table-wrap ${className}`.trim()}><table className="data-table">{children}</table></div>;
}

export function AccessDenied({ navigate, destination, theme, onToggleTheme }) {
  return <main className="auth-page"><ThemeToggle theme={theme} onToggle={onToggleTheme} className="auth-theme-toggle" /><section className="auth-card"><div className="auth-mark">403</div><h1>Access denied</h1><p>This page is not available to your account role.</p><button className="button button-primary auth-submit" onClick={() => navigate(destination)}>Go to your dashboard</button></section></main>;
}
