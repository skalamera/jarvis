import { useState } from "react";

/** One search row for display cards: text box + optional extra controls; Enter or ⌕ runs it. */
export function SearchBar({ value, placeholder, busy, onSearch, children }: {
  value?: string; placeholder: string; busy?: boolean; onSearch: (q: string) => void; children?: React.ReactNode;
}) {
  const [q, setQ] = useState(value || "");
  return (
    <form className="cs-bar" onClick={(e) => e.stopPropagation()} onSubmit={(e) => { e.preventDefault(); onSearch(q.trim()); }}>
      <div className="cs-q">
        <span>⌕</span>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={placeholder} />
        {q && <button type="button" className="cs-x" title="Clear" onClick={() => setQ("")}>×</button>}
      </div>
      {children}
      <button type="submit" className="cs-go" disabled={busy}>{busy ? <span className="cx-spin" /> : "Search"}</button>
    </form>
  );
}
