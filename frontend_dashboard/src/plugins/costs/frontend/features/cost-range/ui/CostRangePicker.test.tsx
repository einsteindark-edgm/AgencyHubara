import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { CostRangePicker } from "./CostRangePicker";

const TODAY = "2026-11-04";
const VALUE = { start: "2026-10-22", end: "2026-11-04" };

function setup(onChange = vi.fn()) {
  render(<CostRangePicker value={VALUE} onChange={onChange} today={TODAY} maxDays={92} />);
  return onChange;
}

describe("CostRangePicker", () => {
  it("shows the current range in the two date fields", () => {
    setup();
    expect(screen.getByLabelText("Desde")).toHaveValue("2026-10-22");
    expect(screen.getByLabelText("Hasta")).toHaveValue("2026-11-04");
  });

  it("a preset changes the whole range", () => {
    const onChange = setup();
    fireEvent.click(screen.getByRole("button", { name: "Mes pasado" }));
    expect(onChange).toHaveBeenCalledWith({ start: "2026-10-01", end: "2026-10-31" });
  });

  it("a valid typed date changes the range", () => {
    const onChange = setup();
    fireEvent.change(screen.getByLabelText("Desde"), { target: { value: "2026-10-30" } });
    expect(onChange).toHaveBeenCalledWith({ start: "2026-10-30", end: "2026-11-04" });
  });

  it("an invalid range is explained and not applied", () => {
    const onChange = setup();
    fireEvent.change(screen.getByLabelText("Hasta"), { target: { value: "2026-10-01" } });
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/inicio/);
  });

  it("the end field cannot go past today", () => {
    setup();
    expect(screen.getByLabelText("Hasta")).toHaveAttribute("max", TODAY);
  });
});
