import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import type { Agent } from "@/entities/agent";
import { AgentsPrompts } from "./AgentsPrompts";

/**
 * Render proof for AgentsPrompts.
 *
 * The vitest hook tests (entities/agent/api.test.tsx) verify the FETCH + mapping,
 * but not that the component actually RENDERS the backend-sourced workspace. This
 * test closes that gap (evaluator R-194116: "un bug en AgentsPrompts leyendo la
 * fuente equivocada pasaría invisible"). Each workspace field carries a unique
 * marker so a regression that reads the wrong source (or a hardcoded mock) fails
 * loudly.
 */
function makeAgent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: "chats:sales",
    plugin_id: "chats",
    worker_name: "sales",
    name: "Ventas Velas",
    role: "Asesor de ventas · catálogo de velas",
    workspace: {
      identity: "IDENTITY_MARKER · soy el asesor de ventas de la marca de velas.",
      soul: "SOUL_MARKER · valoro la calidez y la honestidad.",
      tools: "TOOLS_MARKER · catálogo, base de conocimiento y links de pago.",
      agents: "AGENTS_MARKER · coordino vía handoff con el agente de triage.",
      users: "USERS_MARKER · clientes B2C que valoran el trato cercano.",
      skills: [],
    },
    model: "deepseek-chat",
    icon: "bolt",
    color: "blue",
    status: "online",
    calls: null,
    csat: null,
    category: "Sales",
    capabilities: [],
    ...overrides,
  };
}

describe("AgentsPrompts", () => {
  it("renders the agent's name and role", () => {
    render(<AgentsPrompts agent={makeAgent()} />);
    expect(screen.getByText("Ventas Velas")).toBeInTheDocument();
    expect(
      screen.getByText("Asesor de ventas · catálogo de velas"),
    ).toBeInTheDocument();
  });

  it("renders the REAL workspace content for every prompt section", () => {
    render(<AgentsPrompts agent={makeAgent()} />);
    // Each marker proves the component reads agent.workspace[key] (the
    // backend-sourced field) — not a hardcoded/mock value or the wrong key.
    expect(screen.getByText(/IDENTITY_MARKER/)).toBeInTheDocument();
    expect(screen.getByText(/SOUL_MARKER/)).toBeInTheDocument();
    expect(screen.getByText(/TOOLS_MARKER/)).toBeInTheDocument();
    expect(screen.getByText(/AGENTS_MARKER/)).toBeInTheDocument();
    expect(screen.getByText(/USERS_MARKER/)).toBeInTheDocument();
  });

  it("swaps the rendered workspace when a different agent is selected", () => {
    const { rerender } = render(<AgentsPrompts agent={makeAgent()} />);
    expect(screen.getByText(/IDENTITY_MARKER/)).toBeInTheDocument();

    rerender(
      <AgentsPrompts
        agent={makeAgent({
          name: "Remarketing",
          workspace: {
            identity: "REMARKETING_IDENTITY · reactivo leads tibios.",
            soul: "s",
            tools: "t",
            agents: "a",
            users: "u",
            skills: [],
          },
        })}
      />,
    );
    expect(screen.getByText(/REMARKETING_IDENTITY/)).toBeInTheDocument();
    expect(screen.queryByText(/IDENTITY_MARKER/)).not.toBeInTheDocument();
  });
});
