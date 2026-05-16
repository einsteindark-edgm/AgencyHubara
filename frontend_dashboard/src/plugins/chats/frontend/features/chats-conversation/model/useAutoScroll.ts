import { useEffect, useRef, useState } from "react";

export function useAutoScroll(messagesLength: number) {
  const containerRef = useRef<HTMLDivElement>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);
  const isAtBottomRef = useRef(true);
  // starts false; set to true inside the messagesLength effect on first run
  // so we skip the "new message" logic on initial mount
  const isMountedRef = useRef(false);
  const [showNewBadge, setShowNewBadge] = useState(false);

  // mount / chatId-change: scroll instantáneo al último mensaje
  useEffect(() => {
    sentinelRef.current?.scrollIntoView({ behavior: "auto" });
    isAtBottomRef.current = true;
    setShowNewBadge(false);
  }, []);

  // nuevo mensaje entrante
  useEffect(() => {
    if (!isMountedRef.current) {
      isMountedRef.current = true;
      return;
    }
    if (isAtBottomRef.current) {
      sentinelRef.current?.scrollIntoView({ behavior: "smooth" });
    } else {
      setShowNewBadge(true);
    }
  }, [messagesLength]);

  const handleScroll = () => {
    const el = containerRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distanceFromBottom <= 50) {
      isAtBottomRef.current = true;
      setShowNewBadge(false);
    } else {
      isAtBottomRef.current = false;
    }
  };

  const scrollToBottom = () => {
    sentinelRef.current?.scrollIntoView({ behavior: "smooth" });
    isAtBottomRef.current = true;
    setShowNewBadge(false);
  };

  return { containerRef, sentinelRef, showNewBadge, handleScroll, scrollToBottom };
}
