export type IssueRef = {
  id: string;
  title: string;
  status: "open" | "in_progress" | "resolved" | "closed";
  assignee?: string;
};

export type CodeCitation = {
  path: string;
  startLine: number;
  endLine: number;
  snippet: string;
};
