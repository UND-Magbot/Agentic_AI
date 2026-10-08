export type DealStage = "lead" | "qualified" | "proposal" | "negotiation" | "closed_won" | "closed_lost";

export type Deal = {
  id: string;
  account: string;
  stage: DealStage;
  amount: number;
  expectedCloseDate: string;
  owner: string;
};
