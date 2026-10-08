"""Contract summary — descriptive, deterministic, risk-independent.

GET /contracts/{id}/summary answers ONE question:

    "What is this agreement as a whole about?"

It is assembled ONLY from:
  * contract metadata (filename, member-supplied type / counterparty),
  * the parsed document (document title, clause headings, clause text),
  * clause classifications when they exist (never required).

It never reads risk findings, clause risk states, lifecycle counters,
rules or recommendations — all evaluative content lives on
GET /contracts/{id}/risk-assessment.  This endpoint therefore keeps
working before (and without) any risk analysis.

No LLM: every sentence is either quoted from the document itself or
drawn from a fixed, neutral phrasing keyed by facts the document states.
Missing information is omitted, never invented (no fabricated parties,
dates or renewal terms).
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models import Clause, ClauseClassification, Obligation

# --------------------------------------------------------------- parties ---

# Reported ONLY when the document itself uses the word. Case-sensitive on
# purpose: lowercase "provider" in running text is not a party definition,
# and a role we never saw must never be invented.
ROLE_WORDS = (
    "Provider", "Customer", "Supplier", "Client", "Vendor", "Employer",
    "Employee", "Company", "Contractor", "Consultant", "Subscriber",
    "Purchaser", "Buyer", "Seller", "Licensor", "Licensee",
    "Disclosing Party", "Receiving Party", "Publisher", "Author",
)

NEUTRAL_PARTIES = "The agreement governs the relationship between the parties."

# ---------------------------------------------------------- contract type ---

# (pattern, label, "governs …" phrase for the overview, purpose sentence).
# Detection reads the document TITLE first and the file name second — a
# single clause's classification is NEVER used to type the whole contract.
TYPE_RULES: tuple[tuple[str, str, str, str], ...] = (
    (r"non[-\s]?disclosure|\bnda\b", "Non-Disclosure Agreement",
     "the parties' exchange of confidential information",
     "The agreement sets out the terms on which the parties may exchange "
     "confidential information."),
    (r"employment", "Employment Agreement",
     "the employment relationship between the parties",
     "The agreement sets out the terms of the employment relationship "
     "between the parties."),
    (r"\bsaas\b|subscription", "SaaS Subscription Agreement",
     "a customer's subscription to and use of a hosted software service",
     "The agreement sets out the terms on which the customer subscribes to "
     "and uses a hosted software service."),
    (r"vendor", "Vendor Agreement",
     "the supply of goods or services by a vendor",
     "The agreement sets out the terms on which a vendor supplies goods "
     "or services."),
    (r"licen[cs]e|licensing", "Licensing Agreement",
     "the grant of rights to use intellectual property",
     "The agreement sets out the terms on which intellectual property is "
     "licensed."),
    (r"services? agreement|professional services", "Services Agreement",
     "the provision of services between the parties",
     "The agreement sets out the terms on which the parties provide and "
     "receive services."),
)

NEUTRAL_PHRASE = "the relationship between the parties"
NEUTRAL_PURPOSE = ("The agreement sets out the terms that govern the "
                   "relationship between the parties.")

# -------------------------------------------------------- agreement areas ---

# (category, display label, clause-title regex, neutral description).
# The description says WHAT the area does — never whether it is risky.
AREA_RULES: tuple[tuple[str, str, str, str], ...] = (
    ("service", "Service / deliverables",
     r"subscription|service|scope|grant|access|support|maintenance|deliver|"
     r"availability|position|duties|engagement",
     "Sets out what is provided or performed under the agreement."),
    ("payment", "Payment",
     r"fee|payment|pric|invoice|billing|cost|charge|compensation|salary|wage",
     "Establishes the payment obligations, amounts and timing."),
    ("term_renewal", "Term & renewal",
     r"\bterm\b|duration|renew|effective date|commencement|extension",
     "States how long the arrangement lasts and how it is renewed or extended."),
    ("confidentiality", "Confidentiality",
     r"confidential|disclos",
     "Requires the parties to protect defined confidential information and "
     "limit its disclosure."),
    ("data_security", "Data & security",
     r"\bdata\b|privacy|security|retention|destruction|return",
     "Addresses how information and data are handled, returned and protected."),
    ("ip", "Intellectual property",
     r"intellectual property|copyright|trademark|proprietar|licen[cs]e|"
     r"non[-\s]?solicit",
     "Allocates ownership of and rights in intellectual property and "
     "related restrictions."),
    ("liability", "Liability & warranties",
     r"liabilit|indemn|warrant|disclaim|injunctive|insurance",
     "Allocates responsibility when losses, claims or defects arise."),
    ("termination", "Termination",
     r"terminat|cancell",
     "Describes when and how either side may end the agreement."),
    ("governing_law", "Governing law & disputes",
     r"governing|jurisdiction|dispute|arbitration|compliance|audit",
     "Specifies the law and forum that govern the agreement and its disputes."),
)

# Clause-classification categories map onto the same areas. This is a
# FALLBACK for documents whose headings carry no usable topic words — the
# classifier here is clause classification (descriptive), never the risk
# engine.
CLASSIFICATION_AREA: dict[str, str] = {
    "service": "service", "deliverable": "service", "scope": "service",
    "payment": "payment", "fees": "payment",
    "renewal": "term_renewal", "term": "term_renewal", "duration": "term_renewal",
    "confidentiality": "confidentiality",
    "data": "data_security", "privacy": "data_security", "security": "data_security",
    "intellectual_property": "ip",
    "liability": "liability", "indemnification": "liability",
    "warranty": "liability", "insurance": "liability",
    "termination": "termination",
    "governing_law": "governing_law", "jurisdiction": "governing_law",
    "dispute_resolution": "governing_law",
}

_MAX_AREAS = 8
_MAX_SENTENCE = 400
_MAX_DUTY = 260
_MAX_DUTIES = 4

# A sentence that states what a side must / will / agrees to do — used for
# the descriptive "Key responsibilities" section, never for evaluation.
_DUTY_RE = re.compile(
    r"\b(shall|must|will|agrees?\s+to|is\s+required\s+to|is\s+responsible|"
    r"undertakes?|grants|shall\s+not|may\s+not)\b", re.I)
_PURPOSE_RE = re.compile(r"purpose|objective|background|recital|intention", re.I)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


# ------------------------------------------------------------------ text ---


def _sentences(text: str) -> list[str]:
    flat = re.sub(r"\s+", " ", (text or "").strip())
    return [s.strip() for s in _SENT_SPLIT.split(flat) if s.strip()]


def _cap(s: str | None, n: int = _MAX_SENTENCE) -> str | None:
    if not s:
        return s
    if len(s) <= n:
        return s
    return s[:n - 1].rsplit(" ", 1)[0] + "…"


def _join(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _roles_list(roles: list[str]) -> str:
    quoted = [f"“{r}”" for r in roles]
    if len(quoted) == 1:
        return quoted[0]
    return f"{', '.join(quoted[:-1])} and {quoted[-1]}"


def _body(clause: Clause) -> str:
    """Clause text without its own heading line (heading == title)."""
    text = (clause.text or "").strip()
    lines = text.split("\n")
    if len(lines) >= 2:
        first = lines[0].strip()
        title = (clause.title or "").strip()
        if title and first and (first == title or title.startswith(first)):
            return "\n".join(lines[1:]).strip()
    return text


def _document_title(filename: str, first_clause: Clause | None) -> str:
    """Opening line of the document, else the humanized file name.
    Deterministic: no guessing, no language model."""
    if first_clause is not None and not first_clause.section_number:
        line = (first_clause.text or "").split("\n", 1)[0].strip()
        if line and len(line) <= 90 and not re.match(r"^\d", line):
            return line
        t = (first_clause.title or "").strip()
        if t and not t.startswith("Clause ") and len(t) <= 90:
            return t
    stem = re.sub(r"\.[a-z0-9]+$", "", filename or "", flags=re.I)
    stem = re.sub(r"[_\-]+", " ", stem)
    stem = re.sub(r"^\s*\d+\s*", "", stem).strip()
    return stem or (filename or "")


def _detect_type(stored: str | None, title: str, filename: str) -> str | None:
    """Member-supplied metadata wins; then the document title; then the
    file name. A clause's classification never types the whole contract."""
    if stored and stored.strip():
        return stored.strip()
    normalized_name = re.sub(r"[_\-]+", " ", filename or "")
    for hay in (title, normalized_name):
        if not hay:
            continue
        for pattern, label, _, _ in TYPE_RULES:
            if re.search(pattern, hay, re.I):
                return label
    return None


def _party_roles(clauses: list[Clause]) -> list[str]:
    text = "\n".join(c.text or "" for c in clauses)
    if not text.strip():
        return []
    return [r for r in ROLE_WORDS
            if re.search(rf"(^|[^A-Za-z]){re.escape(r)}([^A-Za-z]|$)", text)]


def _sectioned(clauses: list[Clause]) -> list[Clause]:
    """Structured clauses only — the untitled document header is not a topic."""
    return [c for c in clauses if c.section_number]


def _title_pick(clauses: list[Clause], pattern: str) -> list[Clause]:
    rx = re.compile(pattern, re.I)
    return [c for c in _sectioned(clauses) if rx.search(c.title or "")]


def _first_sentence(clause: Clause) -> str | None:
    s = _sentences(_body(clause))
    return _cap(s[0]) if s else None


# ------------------------------------------------------------ summarizer ---


def contract_summary(db: Session, contract) -> dict:
    """Descriptive summary for one contract (org ownership is the caller's
    responsibility). Reads clauses, classifications and obligations only —
    no risk tables, no risk engine, no LLM."""
    clauses = (db.query(Clause).filter(Clause.contract_id == contract.id)
               .order_by(Clause.order_index).all())
    obligations = (db.query(Obligation).filter(Obligation.contract_id == contract.id)
                   .order_by(Obligation.created_at).all())

    # Latest classification per clause (descriptive; optional signal).
    cat_by_clause: dict[str, str] = {}
    rows = (db.query(ClauseClassification)
            .filter(ClauseClassification.contract_id == contract.id)
            .order_by(ClauseClassification.created_at.desc()).all())
    for r in rows:
        cat_by_clause.setdefault(str(r.clause_id), r.predicted_category)

    title = _document_title(contract.filename or "", clauses[0] if clauses else None)
    ctype = _detect_type(contract.contract_type, title, contract.filename or "")
    phrase = next((p for _, l, p, _ in TYPE_RULES if l == ctype), None)
    purpose_tpl = next((t for _, l, _, t in TYPE_RULES if l == ctype),
                       NEUTRAL_PURPOSE)

    roles = _party_roles(clauses)
    if roles:
        parties_desc = (f"The document uses the role{'s' if len(roles) > 1 else ''} "
                        f"{_roles_list(roles)}.")
    elif contract.counterparty:
        parties_desc = f"The agreement names “{contract.counterparty}” as the counterparty."
    else:
        parties_desc = NEUTRAL_PARTIES

    sectioned = _sectioned(clauses)

    # Purpose: the document's own purpose clause when it has one, else the
    # neutral type phrasing — never a fabricated purpose.
    purpose_hits = _title_pick(clauses, _PURPOSE_RE.pattern)
    purpose = (purpose_hits and _first_sentence(purpose_hits[0])) or purpose_tpl

    # Commercial structure: first sentences of the payment/fees clause.
    pay_hits = _title_pick(clauses, AREA_RULES[1][2])
    commercial = None
    if pay_hits:
        commercial = _cap(" ".join(_sentences(_body(pay_hits[0]))[:2]))

    # Term & renewal: only when duration/renewal clauses actually exist.
    term_hits = _title_pick(clauses, AREA_RULES[2][2])
    term_and_renewal = None
    if term_hits:
        parts = [s for s in (_first_sentence(c) for c in term_hits[:2]) if s]
        term_and_renewal = _cap(" ".join(parts)) or None

    # Key responsibilities: verbatim duty sentences, one per clause, capped.
    responsibilities: list[str] = []
    seen: set[str] = set()
    for c in sectioned:
        for s in _sentences(_body(c)):
            if _DUTY_RE.search(s) and len(s) >= 30:
                s = _cap(s, _MAX_DUTY) or ""
                key = s.lower()[:60]
                if key not in seen:
                    seen.add(key)
                    responsibilities.append(s)
                break
        if len(responsibilities) >= _MAX_DUTIES:
            break

    # Agreement areas: clause headings first, classifications as fallback.
    hits: dict[str, dict] = {}
    for c in sectioned:
        for cat, label, pattern, desc in AREA_RULES:
            if re.search(pattern, c.title or "", re.I):
                h = hits.setdefault(cat, {"label": label, "summary": desc,
                                          "order": c.order_index or 0,
                                          "sections": [], "clause_ids": []})
                sec = c.section_number
                if sec and sec not in h["sections"]:
                    h["sections"].append(sec)
                h["clause_ids"].append(str(c.id))
    for c in clauses:
        cat = CLASSIFICATION_AREA.get(cat_by_clause.get(str(c.id), ""))
        if cat and cat not in hits:
            h = hits.setdefault(cat, {
                "label": next(l for k, l, _, _ in AREA_RULES if k == cat),
                "summary": next(d for k, _, _, d in AREA_RULES if k == cat),
                "order": (c.order_index or 10 ** 6),
                "sections": [], "clause_ids": []})
            if c.section_number:
                h["sections"].append(c.section_number)
            h["clause_ids"].append(str(c.id))
    agreement_areas = [
        {"category": cat, "label": h["label"], "summary": h["summary"],
         "sections": h["sections"], "clause_ids": h["clause_ids"]}
        for cat, h in sorted(hits.items(), key=lambda kv: kv[1]["order"])[:_MAX_AREAS]
    ]

    # ---- overview: 2–5 plain sentences, all grounded ----------------------
    sentences: list[str] = []
    if ctype:
        sentences.append(f"This {ctype} governs {phrase or NEUTRAL_PHRASE}.")
    else:
        sentences.append(f"This agreement governs {NEUTRAL_PHRASE}.")

    cats = {a["category"] for a in agreement_areas}
    if agreement_areas:
        labels = [re.sub(r"^.", lambda m: m.group(0).lower(),
                         a["label"].replace(" / ", " and ")
                         .replace("&", "and")) for a in agreement_areas]
        if len(labels) > 6:
            labels = labels[:6] + ["related subjects"]
        sentences.append(f"It addresses {_join(labels)}.")
    else:
        topics: list[str] = []
        for c in sectioned:
            t = re.sub(r"^\s*\d+(?:\.\d+)*\.?\s+", "", c.title or "").strip()
            if t and t.lower() not in (x.lower() for x in topics):
                topics.append(t)
        if topics:
            head = topics[:6]
            if len(topics) > len(head):
                head.append("and more")
            sentences.append(f"Its clauses address {_join(head)}.")

    if roles:
        sentences.append(f"It identifies the parties through the roles {_roles_list(roles)}.")

    if "term_renewal" in cats and "termination" in cats:
        sentences.append("It also states how the arrangement continues over "
                         "time and how either side may end it.")
    elif "termination" in cats:
        sentences.append("It also states how either side may end the agreement.")
    elif "term_renewal" in cats:
        sentences.append("It also states how long the arrangement lasts.")

    if responsibilities:
        sentences.append("It also sets out the principal duties of each side.")

    overview = " ".join(sentences[:5])

    return {
        "contract_id": str(contract.id),
        "filename": contract.filename,
        "contract_type": ctype,
        "counterparty": contract.counterparty,
        "document_title": title,
        "overview": overview,
        "purpose": purpose,
        "parties": {
            "roles": roles,
            "counterparty": contract.counterparty,
            "description": parties_desc,
        },
        "commercial_terms": commercial,
        "term_and_renewal": term_and_renewal,
        "key_responsibilities": responsibilities,
        "agreement_areas": agreement_areas,
        "obligations": [{"id": str(o.id), "title": o.title, "status": o.status,
                         "type": o.type, "party": o.party}
                        for o in obligations],
        "generated_from": ("contract metadata, clause titles and clause text "
                           "(no LLM synthesis, no risk analysis)"),
    }
