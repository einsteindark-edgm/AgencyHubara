interface Props {
  tags: string[];
  active: string | null;
  onChange: (tag: string | null) => void;
}

export function TagFilterBar({ tags, active, onChange }: Props) {
  return (
    <div className="tags-filter">
      <button
        className={`pill-btn ${active === null ? "active" : ""}`}
        onClick={() => onChange(null)}
      >
        All
      </button>
      {tags.map((t) => (
        <button
          key={t}
          className={`pill-btn ${active === t ? "active" : ""}`}
          onClick={() => onChange(t)}
        >
          {t.replace(/_/g, " ")}
        </button>
      ))}
    </div>
  );
}
