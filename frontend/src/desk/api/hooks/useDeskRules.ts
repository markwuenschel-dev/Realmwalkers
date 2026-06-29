import { useCallback } from "react";
import { api } from "../client";
import type { RuleProposalDecisionIn, RuleProposalOut } from "../types";
import type { DeskFail } from "./shared";

export interface DeskRulesState {
  distillRules: (bookId: string | null, pov?: string) => Promise<number>;
  decideRuleProposal: (id: string, body: RuleProposalDecisionIn) => Promise<void>;
}

export function useDeskRules(
  fail: DeskFail,
  setRuleProposals: React.Dispatch<React.SetStateAction<RuleProposalOut[]>>,
): DeskRulesState {
  const distillRules = useCallback(
    async (bookId: string | null, pov?: string): Promise<number> => {
      if (!bookId) return 0;
      try {
        const created = await api.distill(bookId, pov);
        if (created.length) setRuleProposals((rs) => [...created, ...rs]);
        return created.length;
      } catch (e) {
        fail(e);
        return 0;
      }
    },
    [fail, setRuleProposals],
  );

  const decideRuleProposal = useCallback(
    async (id: string, body: RuleProposalDecisionIn): Promise<void> => {
      try {
        const updated = await api.decideRuleProposal(id, body);
        setRuleProposals((rs) => rs.map((r) => (r.id === updated.id ? updated : r)));
      } catch (e) {
        fail(e);
      }
    },
    [fail, setRuleProposals],
  );

  return { distillRules, decideRuleProposal };
}
