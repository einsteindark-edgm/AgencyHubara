import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { ChatMessageListSkeleton } from "./ChatMessageListSkeleton";

describe("ChatMessageListSkeleton", () => {
  it("renders 4 bubble placeholder elements", () => {
    const { container } = render(<ChatMessageListSkeleton />);
    const bubbles = container.querySelectorAll(".bubble");
    expect(bubbles.length).toBe(4);
  });

  it("placeholder elements have no visible text content", () => {
    const { container } = render(<ChatMessageListSkeleton />);
    const bubbles = container.querySelectorAll(".bubble");
    bubbles.forEach((bubble) => {
      expect(bubble.textContent).toBe("");
    });
  });
});
