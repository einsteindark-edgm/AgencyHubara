import { Search } from "lucide-react";

interface Props {
  value: string;
  onChange: (v: string) => void;
}

export function SearchBar({ value, onChange }: Props) {
  return (
    <div className="search-bar">
      <h2 style={{ marginBottom: "1rem", fontSize: "1.2rem" }}>Chats</h2>
      <div style={{ position: "relative" }}>
        <Search
          size={18}
          style={{
            position: "absolute",
            left: "12px",
            top: "10px",
            color: "var(--text-secondary)",
          }}
        />
        <input
          type="text"
          className="search-input"
          placeholder="Search contacts, reasons..."
          style={{ paddingLeft: "38px" }}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      </div>
    </div>
  );
}
