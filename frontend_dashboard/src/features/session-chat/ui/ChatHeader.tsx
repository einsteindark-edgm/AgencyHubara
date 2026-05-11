import { Phone, Video } from "lucide-react";

interface Props {
  phoneNumber: string;
}

export function ChatHeader({ phoneNumber }: Props) {
  return (
    <div className="chat-header">
      <div
        className="avatar-placeholder"
        style={{ width: "40px", height: "40px", fontSize: "1rem" }}
      >
        {phoneNumber.slice(-2)}
      </div>
      <div className="chat-header-info" style={{ flex: 1 }}>
        <h2>+{phoneNumber}</h2>
        <p>Online / Active Route</p>
      </div>
      <div
        style={{
          display: "flex",
          gap: "1rem",
          color: "var(--accent-color)",
          cursor: "pointer",
        }}
      >
        <Phone size={20} />
        <Video size={20} />
      </div>
    </div>
  );
}
