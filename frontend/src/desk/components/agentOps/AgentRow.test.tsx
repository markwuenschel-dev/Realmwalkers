import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AgentRow } from "./AgentRow";
import type { AgentOpsAgentOut } from "../../api/types";

vi.mock("../../state", () => ({
  useDesk: () => ({ t: { warn: "#e0b15a" } }),
}));

const PROVIDER_TIERS: Record<string, Record<string, string>> = {
  anthropic: { haiku: "claude-haiku-4-5", sonnet: "claude-sonnet-5", opus: "claude-opus-4-8" },
  openai: { haiku: "gpt-5.4-nano", sonnet: "gpt-5.4-mini", opus: "gpt-5.5" },
  google: { sonnet: "gemini-3.5-flash", opus: "gemini-3.1-pro-preview" },
  xai: { opus: "grok-4.3" },
};

function agent(over: Partial<AgentOpsAgentOut> = {}): AgentOpsAgentOut {
  return {
    setting: "draft_model",
    label: "Drafter & planner",
    description: "Writes scene prose",
    model: "claude-sonnet-5",
    tier: "sonnet",
    provider: "anthropic",
    policy: {
      setting: "draft_model",
      primary_tier: "sonnet",
      primary_model: "claude-sonnet-5",
      fallback_tier: null,
      fallback_model: null,
      fallback_provider: null,
      never_fallback: [],
      escalation_rules: [],
      semantic_escalation: true,
      quality_level: "balanced",
    },
    contract: {
      inputs: ["outline"],
      outputs: ["prose"],
      temperature: 0.7,
      max_retries: 3,
      context_load: "Full packet",
      uses_memory: true,
      writes_artifacts: true,
      requires_approval: true,
    },
    permissions: {
      auto_run: true,
      require_approval: true,
      can_modify_packet: false,
      can_block_downstream: false,
      can_write_summaries: false,
      can_update_canon: false,
      can_only_suggest: false,
    },
    estimate: {
      cost_band: "high",
      speed_band: "slow",
      typical_calls_per_chapter: 14,
      estimated_usd_per_chapter: 1.2,
      estimated_latency_sec_per_chapter: 30,
    },
    warnings: [],
    controls: {
      quality_live: true,
      semantic_escalation_live: true,
      auto_run_live: true,
      fallback_mode: "escalation",
    },
    ...over,
  } as AgentOpsAgentOut;
}

function renderOpen(props: Partial<Parameters<typeof AgentRow>[0]> = {}) {
  const onPickTier = vi.fn();
  const onSetFallback = vi.fn();
  const onSetNeverFallback = vi.fn();
  render(
    <AgentRow
      agent={agent()}
      stats={undefined}
      busy={false}
      providerTiers={PROVIDER_TIERS}
      onPickTier={onPickTier}
      onSetFallback={onSetFallback}
      onSetQuality={vi.fn()}
      onSetSemanticEscalation={vi.fn()}
      onSetAutoRun={vi.fn()}
      onSetNeverFallback={onSetNeverFallback}
      onSetBackend={vi.fn()}
      {...props}
    />,
  );
  fireEvent.click(screen.getByText("Drafter & planner"));
  return {
    onPickTier,
    onSetFallback,
    onSetNeverFallback,
    primary: within(screen.getByTestId("primary-model-picker")),
    fallback: within(screen.getByTestId("fallback-model-picker")),
  };
}

describe("AgentRow flat model picker", () => {
  it("shows all 9 models in one row, including Gemini Flash and Gemini Pro beside the non-Anthropic models", () => {
    const { primary } = renderOpen();
    const buttons = primary
      .getAllByRole("button")
      .map((b) => b.textContent)
      .filter((text) => text !== "None");
    expect(buttons).toEqual([
      "Haiku",
      "Sonnet",
      "Opus",
      "GPT 5.4 Nano",
      "GPT 5.4 Mini",
      "GPT 5.5",
      "Gemini Flash",
      "Gemini Pro",
      "Grok",
    ]);
  });

  it("picking a model calls onPickTier with its (tier, provider) pair", () => {
    const { onPickTier, primary } = renderOpen();
    fireEvent.click(primary.getByText("GPT 5.5"));
    expect(onPickTier).toHaveBeenCalledWith("draft_model", "opus", "openai");
  });

  it("picking Gemini Flash calls onPickTier with google/sonnet", () => {
    const { onPickTier, primary } = renderOpen();
    fireEvent.click(primary.getByText("Gemini Flash"));
    expect(onPickTier).toHaveBeenCalledWith("draft_model", "sonnet", "google");
  });

  it("picking Gemini Pro calls onPickTier with google/opus", () => {
    const { onPickTier, primary } = renderOpen();
    fireEvent.click(primary.getByText("Gemini Pro"));
    expect(onPickTier).toHaveBeenCalledWith("draft_model", "opus", "google");
  });

  it("picking Grok calls onPickTier with xai/opus", () => {
    const { onPickTier, primary } = renderOpen();
    fireEvent.click(primary.getByText("Grok"));
    expect(onPickTier).toHaveBeenCalledWith("draft_model", "opus", "xai");
  });

  it("highlights the active model with its brand color and white text", () => {
    const { primary } = renderOpen();
    const sonnetBtn = primary.getByText("Sonnet"); // agent() fixture defaults to anthropic/sonnet
    expect(sonnetBtn).toHaveStyle({ background: "#E67E51", color: "#FFFFFF" });
  });

  it("highlights an active OpenAI model with the OpenAI brand color", () => {
    const { primary } = renderOpen({
      agent: agent({ provider: "openai", tier: "haiku", model: "gpt-5.4-nano" }),
    });
    const btn = primary.getByText("GPT 5.4 Nano");
    expect(btn).toHaveStyle({ background: "#10A37F", color: "#FFFFFF" });
  });

  it("highlights an active Gemini primary with the Google brand color", () => {
    const { primary } = renderOpen({
      agent: agent({ provider: "google", tier: "sonnet", model: "gemini-3.5-flash" }),
    });
    const btn = primary.getByText("Gemini Flash");
    expect(btn).toHaveStyle({ background: "#4796E3", color: "#FFFFFF" });
  });

  it("highlights an active Grok fallback with the xAI brand color", () => {
    const { fallback } = renderOpen({
      agent: agent({
        policy: {
          ...agent().policy,
          fallback_tier: "opus",
          fallback_model: "grok-4.3",
          fallback_provider: "xai",
        },
      }),
    });
    const btn = fallback.getByText("Grok");
    expect(btn).toHaveStyle({ background: "#0A0A0A", color: "#FFFFFF" });
  });

  it("fallback row includes a None option that clears the fallback", () => {
    const { onSetFallback, fallback } = renderOpen({
      agent: agent({
        policy: {
          ...agent().policy,
          fallback_tier: "sonnet",
          fallback_model: "claude-sonnet-5",
          fallback_provider: "anthropic",
        },
      }),
    });
    fireEvent.click(fallback.getByText("None"));
    // Clearing the fallback passes an empty provider too (SettingsScreen nulls it when tier is empty);
    // it no longer hardcodes "anthropic", which mislabeled a non-Anthropic agent's cleared fallback.
    expect(onSetFallback).toHaveBeenCalledWith("draft_model", "", "");
  });

  it("fallback row picks a model with (tier, provider), same as primary", () => {
    const { onSetFallback, fallback } = renderOpen();
    fireEvent.click(fallback.getByText("GPT 5.4 Mini"));
    expect(onSetFallback).toHaveBeenCalledWith("draft_model", "sonnet", "openai");
  });

  it("only shows models that a partial provider catalog actually offers", () => {
    const { primary } = renderOpen({ providerTiers: { anthropic: PROVIDER_TIERS.anthropic } });
    expect(primary.queryByText("Grok")).not.toBeInTheDocument();
    expect(primary.queryByText("GPT 5.5")).not.toBeInTheDocument();
  });
});

describe("AgentRow controls honesty", () => {
  const controls = (over: Partial<AgentOpsAgentOut["controls"]> = {}) =>
    agent({ controls: { ...agent().controls, ...over } });

  it("shows the quality toggle, temp line, and mechanism caption when quality is live", () => {
    renderOpen();
    expect(screen.getByText("Balanced")).toBeInTheDocument();
    expect(screen.getByText("temp 0.7")).toBeInTheDocument();
    expect(
      screen.getByText("applies as temperature, or effort on flagship models"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/quality knob not wired/)).not.toBeInTheDocument();
  });

  it("hides the quality toggle and derived temp when quality_live is false, showing the not-wired note", () => {
    renderOpen({ agent: controls({ quality_live: false }) });
    expect(screen.queryByText("Balanced")).not.toBeInTheDocument();
    expect(screen.queryByText(/^temp /)).not.toBeInTheDocument();
    expect(
      screen.getByText("sampling: provider default — quality knob not wired for this agent"),
    ).toBeInTheDocument();
  });

  it("hides the semantic escalation checkbox when semantic_escalation_live is false", () => {
    renderOpen({ agent: controls({ semantic_escalation_live: false }) });
    expect(
      screen.queryByText("Semantic escalation (canon conflict / high QA risk)"),
    ).not.toBeInTheDocument();
  });

  it("replaces the auto-run checkbox with static pipeline-stage text when auto_run_live is false", () => {
    renderOpen({ agent: controls({ auto_run_live: false }) });
    expect(screen.queryByText("Auto-run in pipeline")).not.toBeInTheDocument();
    expect(screen.getByText("always runs — pipeline stage")).toBeInTheDocument();
  });

  it("captions the fallback picker with escalation semantics by default", () => {
    const { fallback } = renderOpen();
    expect(fallback.getByText("escalates on parse failure / truncation")).toBeInTheDocument();
    expect(fallback.queryByText("retried on provider rate limit only")).not.toBeInTheDocument();
  });

  it("captions the fallback picker with rate-limit-only semantics for rate_limit_only agents", () => {
    const { fallback } = renderOpen({ agent: controls({ fallback_mode: "rate_limit_only" }) });
    expect(fallback.getByText("retried on provider rate limit only")).toBeInTheDocument();
    expect(fallback.queryByText("escalates on parse failure / truncation")).not.toBeInTheDocument();
  });

  it("clicking an inactive never-fallback chip emits the full expanded tier list", () => {
    const { onSetNeverFallback } = renderOpen({
      agent: agent({ policy: { ...agent().policy, never_fallback: ["haiku"] } }),
    });
    const group = within(screen.getByTestId("never-fallback-group"));
    fireEvent.click(group.getByText("sonnet"));
    expect(onSetNeverFallback).toHaveBeenCalledWith("draft_model", ["haiku", "sonnet"]);
  });

  it("clicking an active never-fallback chip emits the list without that tier", () => {
    const { onSetNeverFallback } = renderOpen({
      agent: agent({ policy: { ...agent().policy, never_fallback: ["haiku", "opus"] } }),
    });
    const group = within(screen.getByTestId("never-fallback-group"));
    fireEvent.click(group.getByText("haiku"));
    expect(onSetNeverFallback).toHaveBeenCalledWith("draft_model", ["opus"]);
  });

  it("prefixes the permissions summary with the advisory disclaimer", () => {
    renderOpen();
    expect(screen.getByText("Advisory — descriptive, not enforced at runtime")).toBeInTheDocument();
  });
});
