/**
 * Feature `session-chat`: panel central del dashboard.
 *
 * Consume `useSession(id)` — la MISMA query que `session-metadata` usa.
 * TanStack Query dedupea el fetch automáticamente vía `queryKey`, eliminando
 * el doble polling que existía en el código legado.
 */

import { useSession } from "@/entities/session";
import { ChatHeader } from "./ChatHeader";
import { ChatMessageList } from "./ChatMessageList";
import { ChatInput } from "./ChatInput";

interface Props {
  sessionId: string | null;
}

export function SessionChat({ sessionId }: Props) {
  const { data: details, isLoading } = useSession(sessionId);

  if (!sessionId) {
    return (
      <div
        style={{
          display: "flex",
          height: "100%",
          alignItems: "center",
          justifyContent: "center",
          color: "var(--text-secondary)",
        }}
      >
        <p>Select a chat to start viewing</p>
      </div>
    );
  }

  if (isLoading && !details) {
    return (
      <div style={{ padding: "2rem", color: "var(--text-secondary)" }}>
        Loading session...
      </div>
    );
  }

  return (
    <>
      <ChatHeader phoneNumber={details?.phone_number ?? ""} />
      <ChatMessageList messages={details?.messages ?? []} />
      <ChatInput />
    </>
  );
}
