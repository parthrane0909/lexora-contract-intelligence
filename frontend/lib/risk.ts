"use client";

// ---------------------------------------------------------------------------
// Shared risk-analysis types + deterministic display helpers.
//
// Everything in this module only *formats* what the backend already decided.
// The risk states, counts, priorities, reasons and explanations shown to the
// reviewer come from GET /api/v1/contracts/{id}/risk-assessment (and the
// contract detail lifecycle). The browser never re-derives a risk state, a
// count or an explanation of its own — otherwise the page could disagree
// with the API that produced the numbers.
// ---------------------------------------------------------------------------

/** One stage of the analysis lifecycle, as reported by the backend. */
export type Stage = {
  state: string;
  error?: string | null;
  clauses?: number;
  classified?: number;
  unclassified?: number;
  findings?: number;
  stale?: boolean;
};

/** processing / classification / risk_analysis / overall, all backend-derived. */
export type Life = {
  states_version?: string;
  overall: string;
  processing: Stage;
  classification: Stage;
  risk_analysis: Stage;
};

/** A clause as the risk-assessment endpoint reports it (state included). */
export type RaClause = {
  id: string;
  section_number?: string | null;
  title?: string | null;
  text: string;
  page_start?: number | null;
  page_end?: number | null;
  order_index?: number;
  risk_state: string;
  state_reason: string;
  top_priority?: string | null;
  classification?: {
    category: string;
    confidence: number;
    model_name?: string;
    model_version?: string;
  } | null;
  finding_ids?: string[];
};

/** A recorded finding: rule, priority, evidence, facts, deterministic
 *  explanation. Identical shape on /findings and /risk-assessment. */
export type RiskFinding = {
  id: string;
  contract_id?: string;
  clause_id: string;
  analysis_run_id?: string | null;
  risk_category: string;
  finding_type: string;
  priority: string;
  title: string;
  reason: string;
  evidence?: string | null;
  section_number?: string | null;
  clause_title?: string | null;
  clause_text?: string;
  page_start?: number | null;
  page_end?: number | null;
  requires_attention?: boolean;
  attention_reason?: string | null;
  rule_id: string;
  rule_version: string;
  policy_id?: string | null;
  policy_version?: number | null;
  contract_value?: Record<string, unknown> | null;
  expected_value?: Record<string, unknown> | null;
  extracted_facts?: Record<string, unknown> | null;
  priority_reasons?: string[];
  classification_model?: string | null;
  classification_version?: string | null;
  classification_confidence?: number | null;
  status?: string;
  created_at?: string;
  explanation?: {
    explanation_version?: string;
    source?: string;
    what?: string;
    why?: string;
    attention?: string;
    detected_condition?: {
      rule_id?: string;
      rule_version?: string;
      finding_type?: string;
      finding_type_label?: string;
      risk_category?: string;
      priority?: string;
    };
    evidence?: {
      clause_text?: string;
      facts?: Record<string, unknown>;
      section_number?: string | null;
      page_start?: number | null;
      page_end?: number | null;
    };
    priority_reasons?: string[];
  } | null;
};

/** Contract-level aggregation — counts come from clause states. */
export type RaSummary = {
  contract_id?: string;
  filename?: string;
  classification_state: string;
  risk_analysis_state: string;
  analysis_overall: string;
  total_clauses: number;
  classified_clauses: number;
  unclassified_clauses: number;
  analyzed_clauses: number;
  high_risk: number;
  medium_risk: number;
  low_risk: number;
  no_detected_risk: number;
  needs_review: number;
  needs_attention: number;
  findings: number;
  findings_by_priority?: Record<string, number>;
  risk_categories?: string[];
  states_version?: string;
};

/** Full GET /contracts/{id}/risk-assessment response. */
export type RiskAssessment = {
  contract_id?: string;
  filename?: string;
  analysis: Life;
  summary: RaSummary;
  clauses: RaClause[];
  findings: RiskFinding[];
  runs?: {
    classification?: Record<string, unknown> | null;
    risk_analysis?: Record<string, unknown> | null;
  } | null;
  generated_from?: string;
};

/** One major subject the agreement covers, as reported by the summary
 *  endpoint: a neutral label, a sentence saying what that subject does,
 *  and the clauses it came from (for jump-to navigation). */
export type SummaryArea = {
  category: string;
  label: string;
  summary: string;
  sections: string[];
  clause_ids: string[];
};

/** GET /contracts/{id}/summary — DESCRIPTIVE document briefing only.
 *  Deliberately contains NO risk counts, findings, priorities, rules or
 *  recommendations; every evaluative field lives on the risk-assessment
 *  endpoint below. The backend never invents parties, dates or renewal
 *  terms: fields are null / empty when the document does not state them. */
export type ContractSummaryPayload = {
  contract_id: string;
  filename: string;
  contract_type?: string | null;
  counterparty?: string | null;
  document_title?: string;
  overview: string;
  purpose: string;
  parties: {
    roles: string[];
    counterparty?: string | null;
    description: string;
  };
  commercial_terms?: string | null;
  term_and_renewal?: string | null;
  key_responsibilities: string[];
  agreement_areas: SummaryArea[];
  obligations: {
    id: string;
    title: string;
    status: string;
    type?: string | null;
    party?: string | null;
  }[];
  generated_from?: string;
};

// --------------------------------------------------------------- wording ---

/** Display order inside the clause-by-clause list: worst first, then the
 *  clause's own document order (never re-scored). */
export const STATE_ORDER: Record<string, number> = {
  high_risk: 0,
  medium_risk: 1,
  low_risk: 2,
  needs_review: 3,
  no_detected_risk: 4,
};

/** Filters offered on the risk-assessment section (client-side only). */
export const RISK_FILTERS: { key: string; label: string }[] = [
  { key: "all", label: "All" },
  { key: "needs_attention", label: "Needs Attention" },
  { key: "high_risk", label: "High Risk" },
  { key: "medium_risk", label: "Medium Risk" },
  { key: "low_risk", label: "Low Risk" },
  { key: "no_detected_risk", label: "No Detected Risk" },
  { key: "needs_review", label: "Needs Review" },
];

/** Needs attention = risky clauses + clauses nobody has analyzed yet. */
export function matchesFilter(state: string, filter: string): boolean {
  if (filter === "all") return true;
  if (filter === "needs_attention") {
    return state === "high_risk" || state === "medium_risk" || state === "needs_review";
  }
  return state === filter;
}

/** Risk-first ordering that keeps document order within a state. */
export function sortForReview<T extends { risk_state: string; order_index?: number }>(
  rows: T[]
): T[] {
  return [...rows].sort(
    (a, b) =>
      (STATE_ORDER[a.risk_state] ?? 9) - (STATE_ORDER[b.risk_state] ?? 9) ||
      (a.order_index ?? 0) - (b.order_index ?? 0)
  );
}

/** Chip colour for a clause's analysis state (backend decides the state). */
export function stateChip(state: string): string {
  if (state === "no_detected_risk") return "bg-emerald-100 text-emerald-800";
  if (state === "needs_review") return "bg-slate-200 text-slate-700";
  if (state === "medium_risk") return "bg-amber-100 text-amber-800";
  return "bg-red-100 text-red-800";
}

/** Plain wording for why a clause is in its current state. */
export function stateReason(reason: string): string {
  switch (reason) {
    case "unclassified": return "clause was never classified";
    case "classification_failed": return "classification failed";
    case "classification_stale": return "contract was re-processed after classification";
    case "risk_analysis_not_run": return "risk analysis has not run yet";
    case "risk_analysis_failed": return "risk analysis failed";
    case "risk_analysis_stale": return "results predate the latest change";
    case "rule_matched": return "a risk rule matched this clause";
    case "no_rule_matched": return "analyzed — no risk rule matched";
    case "empty_clause": return "no text to analyze";
    default: return reason;
  }
}

/** Why the contract's risk analysis is not finished — states the backend
 *  reported, never a claim the data does not support. Returns null when the
 *  analysis is complete (nothing to warn about). */
export function analysisNotice(life: Life | null | undefined): string | null {
  if (!life) return null;
  switch (life.overall) {
    case "complete": return null;
    case "not_started":
      return "Clause classification and risk analysis have not run for this contract — "
        + "0 findings is not evidence of a low-risk contract. Run classification, then risk analysis.";
    case "classified_only":
      return "Every clause is classified, but the risk rules have not been evaluated yet. "
        + "Run risk analysis to get priorities, categories and evidence.";
    case "partial":
      return "Only some clauses were analyzed. The results below cover those clauses; "
        + "the others still need review.";
    case "failed":
      return "Classification or risk analysis failed (see the error above). "
        + "Treat the results below as incomplete and re-run the failed step.";
    case "stale":
      return "This contract was re-processed or re-classified after the last risk run, "
        + "so earlier findings no longer describe it. Re-run classification and risk analysis.";
    case "running":
      return "Analysis is still running — statuses refresh automatically.";
    default:
      return null;
  }
}

/** Header line that distinguishes "analyzed, nothing found" from
 *  "analysis has not finished" — the two must never read the same. */
export function analysisHeadline(life: Life | null | undefined, s: RaSummary | null | undefined): string {
  if (!life) return "Risk assessment unavailable.";
  if (life.overall !== "complete") return "Risk analysis has not completed yet.";
  if (!s) return "Analysis complete.";
  if (s.findings === 0) return "Analysis complete — no detected risk.";
  if (s.high_risk + s.medium_risk === 0) {
    return "Analysis complete — no high or medium priority findings detected.";
  }
  return "Analysis complete.";
}

/** `§4 — Limitation of Liability`, or the title alone when unnumbered.
 *  A title that repeats the section number ("4. Limitation of Liability")
 *  is de-duplicated so the label never reads "§4 — 4. Limitation …". */
export function clauseLabel(c: {
  section_number?: string | null;
  title?: string | null;
}): string {
  let title = (c.title || "Untitled clause").trim();
  const sec = c.section_number;
  if (sec) {
    const prefix = title.match(new RegExp(`^\\s*§?\\s*0*${Number(sec) || sec}[.)]?\\s+`));
    if (prefix) title = title.slice(prefix[0].length).trim() || title;
  }
  return sec ? `§${sec} — ${title}` : title;
}

/** First-line document title when the contract opens with a title clause,
 *  otherwise the humanized file name. Deterministic, no guessing. */
function documentTitle(filename: string, clauses: RaClause[]): string {
  const first = clauses[0];
  const line = ((first?.text || "").split("\n")[0] || "").trim();
  if (first && !first.section_number && line && line.length <= 90 && !/^\d/.test(line)) {
    return line;
  }
  const stem = filename
    .replace(/\.[^.]+$/, "")
    .replace(/[_-]+/g, " ")
    .replace(/^\s*\d+\s*/, "")
    .trim();
  return stem || filename;
}

function clauseTopic(c: RaClause): string | null {
  if (!c.section_number) return null; // title/preamble clause is not a topic
  const t = (c.title || "").replace(/^\s*\d+\.?\s*/, "").trim();
  return t ? t.toLowerCase() : null;
}

/** "services, fees and payment, automatic renewal … and 4 more" — built from
 *  the contract's own clause titles, never from a language model. */
function topicList(clauses: RaClause[]): string {
  const seen: string[] = [];
  for (const c of clauses) {
    const t = clauseTopic(c);
    if (t && !seen.includes(t)) seen.push(t);
  }
  if (!seen.length) return "";
  const max = 8;
  const head = seen.slice(0, max);
  const rest = seen.length - head.length;
  if (rest > 0) return `${head.join(", ")}, and ${rest} more`;
  return head.length > 1
    ? `${head.slice(0, -1).join(", ")} and ${head[head.length - 1]}`
    : head[0];
}

/** Neutral-summary helpers: see `summaryParagraphs` below. */

// ---------------------------------------------------------------------------
// NEUTRAL CONTRACT SUMMARY (no risk content lives here).
//
// Everything below only describes what the document *is*: its title, the
// subjects its own clause titles name, the party-role words it actually
// uses, and the obligations the lifecycle extractor recorded. Nothing is
// scored, counted for risk, ranked or recommended — that is exclusively
// GET /contracts/{id}/risk-assessment.
// ---------------------------------------------------------------------------

/** Minimal clause shape the summary needs (risk fields are irrelevant). */
export type SummaryClause = {
  id?: string;
  section_number?: string | null;
  title?: string | null;
  text: string;
};

/** Party-role words we only report when the document itself uses them.
 *  Case-sensitive on purpose: lowercase "provider" in running text is not a
 *  party definition, and a role we never saw must never be invented. */
const ROLE_WORDS = [
  "Provider", "Customer", "Supplier", "Client", "Vendor", "Employer",
  "Employee", "Company", "Contractor", "Consultant", "Subscriber",
  "Purchaser", "Buyer", "Seller", "Licensor", "Licensee",
  "Disclosing Party", "Receiving Party", "Publisher", "Author",
];

/** Role words that literally occur in the clause text, in list order. */
export function partyRoles(clauses: SummaryClause[]): string[] {
  const text = clauses.map((c) => c.text || "").join("\n");
  if (!text.trim()) return [];
  return ROLE_WORDS.filter((r) => new RegExp(`(^|[^A-Za-z])${r}([^A-Za-z]|$)`).test(text));
}

/** `“Provider”` / `“Provider” and “Customer”`. */
function rolesList(roles: string[]): string {
  const quoted = roles.map((r) => `“${r}”`);
  return quoted.length === 1
    ? quoted[0]
    : `${quoted.slice(0, -1).join(", ")} and ${quoted[quoted.length - 1]}`;
}

/** "The document uses the role “Provider”." /
 *  "The document uses the roles “Provider” and “Customer”." */
function rolesSentence(roles: string[]): string {
  return `The document uses the role${roles.length === 1 ? "" : "s"} ${rolesList(roles)}.`;
}

/** Plain join: "a, b and c". */
function listJoin(items: string[]): string {
  if (items.length <= 1) return items[0] ?? "";
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/** Subject areas, grouped from the contract's OWN clause titles. A group is
 *  listed only when one of its clause titles actually matched — the wording
 *  is a neutral topic label, never an assessment. */
const SUBJECT_GROUPS: { label: string; re: RegExp }[] = [
  { label: "the scope of the service", re: /(subscription|service|scope|grant|access|support|maintenance|deliver|licen[cs]e|work)/i },
  { label: "fees and payment", re: /(fee|payment|pric|invoice|billing|cost|charge|compensation|salary|wage)/i },
  { label: "the term of the agreement and its renewal", re: /(renew|\bterm\b|duration|extension)/i },
  { label: "confidentiality and data handling", re: /(confidential|\bdata\b|privacy|security)/i },
  { label: "intellectual property", re: /(intellectual property|copyright|trademark|proprietar|licen[cs]e)/i },
  { label: "liability, indemnity and warranties", re: /(liabilit|indemn|warrant|disclaim)/i },
  { label: "termination", re: /(terminat|cancell)/i },
  { label: "governing law and disputes", re: /(governing|jurisdiction|dispute|arbitration|compliance|audit)/i },
  { label: "general provisions", re: /(miscell|notice|assignment|amendment|severab|waiver|entire agreement|relationship)/i },
];

function subjectGroups(clauses: SummaryClause[]): string[] {
  const titles = clauses.map((c) => c.title || "").filter(Boolean);
  return SUBJECT_GROUPS.filter((g) => titles.some((t) => g.re.test(t))).map((g) => g.label);
}

/** 2–4 plain paragraphs answering "what is this contract about?" — the
 *  FALLBACK used only when the /summary payload failed to load. It reads
 *  nothing but the clause list: title, clause count, role words,
 *  clause-title subjects. No risk content, no invention. */
export function summaryParagraphs(
  filename: string,
  clauses: SummaryClause[]
): string[] {
  if (!clauses.length) return [`No clauses were extracted from ${filename}.`];

  const n = clauses.length;
  const roles = partyRoles(clauses);
  const p1 = [`This ${documentTitle(filename, clauses as RaClause[])} contains ${n} clause${n === 1 ? "" : "s"}.`,
    roles.length ? rolesSentence(roles) : ""]
    .filter(Boolean).join(" ");

  const groups = subjectGroups(clauses);
  const topics = topicList(clauses as RaClause[]);
  const p2 = groups.length ? `It covers ${listJoin(groups)}.`
    : topics ? `Its clauses address ${topics}.` : "";

  return [p1, p2].filter((s) => s.trim()).map((s) => s.trim());
}

/** Fallback "Key Contract Terms" rows — one row per subject the document
 *  actually contains, built purely from clause TITLES plus the opening
 *  sentence of that clause. Used only when the /summary payload failed to
 *  load; rows with no real value are simply not emitted. */
export type ContractTermRow = { label: string; value: string; clauseId?: string };

const TERM_ROWS: { label: string; re: RegExp }[] = [
  { label: "Duration", re: /^(term|duration|effective date|commencement|initial term)/i },
  { label: "Renewal", re: /renew/i },
  { label: "Payment", re: /(fee|payment|pric|invoice|billing|cost|charge)/i },
  { label: "Termination", re: /terminat/i },
  { label: "Confidentiality", re: /confidential/i },
  // "Data" rather than "Data protection": the heading it matches may be about
  // data ownership or location, and we must not relabel what it says.
  { label: "Data", re: /(\bdata\b|privacy|security)/i },
  // Deliberately no bare "ownership" here — a Data Ownership clause is not
  // necessarily an intellectual-property clause, so the row is omitted
  // instead of shown under a heading the document never used.
  { label: "Intellectual property", re: /(intellectual property|copyright|trademark|proprietar|licen[cs]e)/i },
  { label: "Liability", re: /(liabilit|indemn)/i },
  { label: "Warranty", re: /warrant/i },
  { label: "Governing law", re: /(governing law|jurisdiction|dispute|arbitration)/i },
];

/** Opening sentence of a clause, with its own heading ("2. Fees") removed,
 *  capped so a table row stays a row. Quoted, because it is the document. */
function clauseOpening(text: string): string {
  const raw = (text || "").trim();
  const head = raw.match(/^\d+(?:\.\d+)*\.?\s+[^\n]{1,60}(\n\s*\n?|\s{2,})/);
  let body = head ? raw.slice(head[0].length) : raw;
  body = body.replace(/\s+/g, " ").trim();
  if (!body) body = raw.replace(/\s+/g, " ").trim();
  const m = body.match(/^(.+?[.!?])(\s|$)/);
  let s = m ? m[1] : body;
  if (s.length > 180) s = `${s.slice(0, 177).replace(/\s+\S*$/, "")}…`;
  return s;
}

export function contractTerms(clauses: SummaryClause[]): ContractTermRow[] {
  const rows: ContractTermRow[] = [];

  const roles = partyRoles(clauses);
  if (roles.length) {
    rows.push({ label: "Parties / roles", value: rolesList(roles) });
  }

  // Topic rows come from clause TITLES (the contract's own headings), so a
  // label can never point at a clause that does not concern it. The first
  // clause is skipped when it is the untitled document header.
  const topicClauses = clauses.filter((c, i) => (i > 0 || c.section_number) && c.title);
  for (const t of TERM_ROWS) {
    if (rows.some((r) => r.label === t.label)) continue;
    const c = topicClauses.find((x) => t.re.test(x.title || ""));
    if (!c) continue;
    const opening = clauseOpening(c.text);
    rows.push({
      label: t.label,
      value: `${clauseLabel(c)}${opening ? ` — “${opening}”` : ""}`,
      clauseId: c.id,
    });
  }
  return rows;
}


/** Rendered form for an extracted fact: booleans become yes/no so the
 *  evidence reads like a sentence, not like JSON. */
export function factLine(key: string, value: unknown): string {
  const k = key.replace(/_/g, " ");
  let v: string;
  if (value === null || value === undefined) v = "not identified";
  else if (typeof value === "boolean") v = value ? "yes" : "no";
  else if (Array.isArray(value)) v = value.map((x) => String(x)).join(", ");
  else if (typeof value === "object") v = JSON.stringify(value);
  else v = String(value);
  return `${k}: ${v}`;
}

export function factLines(facts: Record<string, unknown> | null | undefined): string[] {
  if (!facts) return [];
  return Object.entries(facts).map(([k, v]) => factLine(k, v));
}

/** Lower-is-worse priority ranking, matching the engine's own order. */
const PRIORITY_RANK = ["critical", "high", "medium", "low"];
export function priorityRank(p: string | null | undefined): number {
  const i = PRIORITY_RANK.indexOf(p || "");
  return i === -1 ? PRIORITY_RANK.length : i;
}

/** The finding that best explains a clause (highest priority first). */
export function topFinding(
  findings: RiskFinding[],
  clauseId: string
): RiskFinding | undefined {
  const rows = findings.filter((f) => f.clause_id === clauseId);
  return [...rows].sort((a, b) => priorityRank(a.priority) - priorityRank(b.priority))[0];
}

/** Attention verdict for a whole clause, derived from what the API returned. */
export function clauseAttention(
  clause: RaClause,
  findings: RiskFinding[]
): { required: boolean; text: string } {
  const rows = findings.filter((f) => f.clause_id === clause.id);
  if (rows.some((f) => f.requires_attention)) {
    return { required: true, text: "Requires human review." };
  }
  if (clause.risk_state === "needs_review") {
    return { required: true, text: "Requires human review — this clause has not been analyzed." };
  }
  if (rows.length) {
    return {
      required: false,
      text: rows[0].explanation?.attention
        || "No action required — recorded for the reviewer's context.",
    };
  }
  return { required: false, text: "No action required." };
}

/** Short "why" sentence for a clause, always traceable to the API payload. */
export function clauseWhy(
  clause: RaClause,
  findings: RiskFinding[]
): string {
  const top = topFinding(findings, clause.id);
  if (top) return top.explanation?.why || top.reason;
  switch (clause.risk_state) {
    case "no_detected_risk":
      return "No risk rule matched this clause after analysis.";
    case "needs_review":
      return `Not analyzed yet — ${stateReason(clause.state_reason)}.`;
    default:
      return stateReason(clause.state_reason);
  }
}
