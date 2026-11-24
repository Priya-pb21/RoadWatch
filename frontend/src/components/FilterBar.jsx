import { Search } from "lucide-react";

export default function FilterBar({ filters, setFilters }) {
  return (
    <div className="filters" role="search" aria-label="Filter pothole records">
      <label className="search"><Search size={16} aria-hidden="true" /><input value={filters.search} onChange={(event) => setFilters((current) => ({ ...current, search: event.target.value }))} placeholder="Search ID, source or notes" /></label>
      <select aria-label="Filter by pothole verification status" value={filters.verification} onChange={(event) => setFilters((current) => ({ ...current, verification: event.target.value }))}>
        <option value="all">All verification statuses</option><option value="pending_verification">Pending Verification</option><option value="verified">Verified</option><option value="rejected">Rejected</option>
      </select>
      <select aria-label="Filter by repair status" value={filters.repair} onChange={(event) => setFilters((current) => ({ ...current, repair: event.target.value }))}>
        <option value="all">All repair statuses</option><option value="not_started">Not Started</option><option value="under_maintenance">Under Maintenance</option><option value="repaired">Repaired</option>
      </select>
    </div>
  );
}
