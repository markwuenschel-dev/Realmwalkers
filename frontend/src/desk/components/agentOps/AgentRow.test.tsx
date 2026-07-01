import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AgentRow } from "./AgentRow";
import type { AgentOpsAgentOut, ProviderOut } from "../../api/types";

vi.mock("../../state", () => ({
  useDesk: () => ({ t: { warn: "#e0b15a" } }),
}));

const PROVIDER_TIERS: Record<string, Record<string, string>> = {
  anthropic: { haiku: "claude-haiku-4-5", sonnet: "claude-sonnet-5", opus: "claude-opus-4-8" },
  openai: { haiku: "gpt-5.4-nano", sonnet: "gpt-5.4-mini", opus: "gpt-5.5" },
  xai: { opus: "grok-4.3" },
};

const PROVIDERS: ProviderOut[] = [
  { id: "anthropic", label: "Anthropic", status: "active" },
  { id: "openai", label: "OpenAI", status: "active" },
  { id: "xai", label: "xAI", status: "active" },
];

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
    ...over,
  } as AgentOpsAgentOut;
}

function renderOpen(props: Partial<Parameters<typeof AgentRow>[0]> = {}) {
  const onPickTier = vi.fn();
  render(
    <AgentRow
      agent={agent()}
      stats={undefined}
      busy={false}
      providerTiers={PROVIDER_TIERS}
      providers={PROVIDERS}
      onPickTier={onPickTier}
      onSetFallback={vi.fn()}
      onSetQuality={vi.fn()}
      onSetSemanticEscalation={vi.fn()}
      onSetAutoRun={vi.fn()}
      {...props}
    />,
  );
  fireEvent.click(screen.getByText("Drafter & planner"));
  return { onPickTier, picker: within(screen.getByTestId("primary-model-picker")) };
}

describe("AgentRow provider picker", () => {
  it("shows a provider selector when more than one provider is wired", () => {
    const { picker } = renderOpen();
    expect(picker.getByText("Anthropic")).toBeInTheDocument();
    expect(picker.getByText("OpenAI")).toBeInTheDocument();
    expect(picker.getByText("xAI")).toBeInTheDocument();
  });

  it("picking a tier under the current provider calls onPickTier with that provider", () => {
    const { onPickTier, picker } = renderOpen();
    fireEvent.click(picker.getByText("Opus"));
    expect(onPickTier).toHaveBeenCalledWith("draft_model", "opus", "anthropic");
  });

  it("switching provider narrows the tier choices to what that provider offers", () => {
    const { onPickTier, picker } = renderOpen();
    fireEvent.click(picker.getByText("xAI"));
    // xAI only ships one model, slotted at opus -- haiku/sonnet must disappear from this picker
    // (the separate Fallback picker is unaffected and still offers all Anthropic tiers).
    expect(picker.queryByText("Haiku")).not.toBeInTheDocument();
    expect(picker.queryByText("Sonnet")).not.toBeInTheDocument();
    fireEvent.click(picker.getByText("Opus"));
    expect(onPickTier).toHaveBeenCalledWith("draft_model", "opus", "xai");
  });

  it("hides the provider selector when only Anthropic is wired", () => {
    const { picker } = renderOpen({
      providerTiers: { anthropic: PROVIDER_TIERS.anthropic },
      providers: undefined,
    });
    expect(picker.queryByText("OpenAI")).not.toBeInTheDocument();
    expect(picker.queryByText("xAI")).not.toBeInTheDocument();
  });
});
