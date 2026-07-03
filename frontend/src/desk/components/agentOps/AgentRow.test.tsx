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
    ...over,
  } as AgentOpsAgentOut;
}

function renderOpen(props: Partial<Parameters<typeof AgentRow>[0]> = {}) {
  const onPickTier = vi.fn();
  const onSetFallback = vi.fn();
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
      {...props}
    />,
  );
  fireEvent.click(screen.getByText("Drafter & planner"));
  return {
    onPickTier,
    onSetFallback,
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
