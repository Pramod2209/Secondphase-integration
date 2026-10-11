"""Skill normalization and context-aware skill matching.

Operations sharing one matcher:
  * scan_taxonomy(text)         - every taxonomy skill found in a resume (informational)
  * match_skills(text, skills)  - the best evidence for each job skill (exact / alias / category /
                                  related, see "evidence tiers" below); used for scoring and filtering
  * find_skills(text, skills)   - job skills named exactly or by an equivalent name

How matching works
  * Text is tokenized into word tokens ("c++", "c#", ".net" are single tokens). Every run of
    1..N consecutive tokens is reduced to a *key* (lowercase, separators removed), so
    "React.js", "react js" and "ReactJS" all produce the key "reactjs". Keys are looked up
    in a dictionary built from the taxonomy (or from the job's skills), longest run first.
  * Ambiguous terms (ordinary English words like "go", "spring", "make", single letters
    like "r"/"c", and any surface form of <= 2 characters) are only accepted with technical
    evidence:
      - a confirming neighbour word ("spring boot", "apache spark", "r programming"), or
      - they are an item in a list (delimited by , | / ; ( ) : & or line edges) on a
        skills-heading line, or within ~100 characters of another unambiguous skill.
  * Equivalent names come from the taxonomy aliases plus acronyms derived from 3+ word skills
    ("search engine optimization" <-> "seo"; derived acronyms need list/skills context). Free-text
    expansion of arbitrary acronyms is deliberately NOT done: on real resumes it matched ordinary
    phrases ("schedules and procedures" -> SAP). Broader/narrower and related skills, and words that
    signal another sense of a name ("emr" next to "aws"), come from skill_relations.json.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import config

# Terms that are also ordinary words/names/abbreviations in resumes. Curated from the
# taxonomy; surface forms of <= 2 characters are always treated as ambiguous as well.
AMBIGUOUS_TERMS: Set[str] = {
    "go", "r", "c", "make", "ant", "ray", "spring", "swift", "unity", "express", "ember", "chef", "puppet",
    "black", "beam", "lambda", "vault", "consul", "segment", "rust", "ruby", "presto", "spark", "hive",
    "jest", "mocha", "chai", "babel", "backbone", "electron", "helm", "bamboo", "maven", "pulsar", "rag",
    "kms", "excel", "tally", "postman", "flask", "airflow", "celery", "poetry", "bandit", "amplitude",
    "accessibility", "opa", "mcp", "tox", "nox", "ruff", "calico", "bash", "node", "kube", "prisma",
    "storybook", "cypress", "solidity", "loki", "quasar", "nats", "deno", "falco", "dask", "oracle",
    "plc", "sas", "firmware", "elt", "unix", "sem", "outlook", "hdl", "tax", "looker", "creo", "react",
    "angular", "rtos",
}

# Neighbour words that confirm the technical meaning of an ambiguous term.
_CONFIRM_BEFORE = {"apache", "hashicorp", "aws", "amazon", "microsoft", "ms", "google", "azure", "gcp",
                   "using", "used", "utilizing", "utilising", "utilized", "utilised", "leveraging", "leveraged",
                   "via"}
_CONFIRM_AFTER = {
    "programming", "language", "lang", "framework", "developer", "development", "scripting", "script",
    "scripts", "sdk", "boot", "mvc", "cloud", "core", "studio", "shiny", "engine", "3d", "ui", "charts",
    "chart", "functions", "function", "streaming", "sql", "db", "database", "pipelines", "pipeline", "vba",
    "macros", "macro", "formulas", "pivot", "spreadsheets", "erp", "prime", "file", "files", "js", "server",
    "cluster", "testing", "tests", "automation", "modules", "module", "plsql", "forms", "apex", "routines",
    "certified", "foundation", "shell", "executive", "generalist", "operations", "policies", "recruiter",
    "components", "component", "hooks", "app", "apps", "application", "applications",
}

_LIST_LEFT = set(",;|/(:*-&+")
_LIST_RIGHT = set(",;|/).:")
# A lone letter opening a line, as PDF bullet fonts are often extracted ("e", "o", "l", "n"); "a" and "i" are words.
_BULLET_LETTER = re.compile(r"\s*[b-hj-z]\s+")
_SKILL_HEADINGS = re.compile(
    r"^\s*(?:technical\s+|key\s+|core\s+|it\s+|computer\s+|software\s+|professional\s+)?"
    r"(?:skills?|skill\s*set|technologies|tech\s*stack|tools(?:\s*&\s*technologies)?|languages|"
    r"programming(?:\s+languages)?|frameworks?|platforms?|competenc(?:y|ies)|expertise|proficiency|"
    r"front[\s-]?end|back[\s-]?end|libraries|databases?|web\s+technologies|devops|cloud)\b"
)
_TOKEN = re.compile(r"\.?[a-z0-9]+[+#]*")
_NOISE = {"http", "https"}  # protocols in the taxonomy; never reported as skills


def skill_key(term: str) -> str:
    """Comparison key: lowercase, separators (space . - _ /) removed; + # kept; leading '.' kept (.net)."""
    term = (term or "").lower().strip()
    lead = "." if term.startswith(".") else ""
    return lead + re.sub(r"[\s.\-_/]+", "", term)


def clean_skill(term: str) -> str:
    return re.sub(r"\s+", " ", (term or "").lower().strip()).strip(" ,;")


@dataclass
class Taxonomy:
    canonicals: Dict[str, List[str]]
    key_to_canonical: Dict[str, str]
    max_tokens: int
    version: str
    breaks: Dict[str, Set[int]] = field(default_factory=dict)  # key -> offsets where a written form has a word break


def _token_count(form: str) -> int:
    return max(1, len(_TOKEN.findall(form)))


def _word_breaks(form: str) -> Set[int]:
    """Offsets in skill_key(form) between two of its tokens: "c sharp" -> {1}, "a/b testing" -> {1, 2}, "ehr" -> {}."""
    ends = [m.end() for m in _TOKEN.finditer(form.lower())][:-1]
    return {len(skill_key(form[:e])) for e in ends}


@lru_cache(maxsize=1)
def load_taxonomy_index() -> Taxonomy:
    raw: Dict[str, List[str]] = {}
    if config.SKILL_TAXONOMY_PATH.exists():
        raw = json.loads(config.SKILL_TAXONOMY_PATH.read_text(encoding="utf-8"))
    canonicals = {clean_skill(k): [clean_skill(a) for a in (v or []) if clean_skill(a)] for k, v in raw.items()}
    key_to_canonical: Dict[str, str] = {}
    for canon in canonicals:  # canonical names win over aliases when keys collide
        key_to_canonical.setdefault(skill_key(canon), canon)
    for canon, aliases in canonicals.items():
        for alias in aliases:
            key_to_canonical.setdefault(skill_key(alias), canon)
    max_tokens = max([_token_count(f) for c, a in canonicals.items() for f in (c, *a)] or [1])
    breaks: Dict[str, Set[int]] = {}
    for form in (f for c, a in canonicals.items() for f in (c, *a)):
        breaks.setdefault(skill_key(form), set()).update(_word_breaks(form))
    digest = hashlib.sha256(json.dumps(canonicals, sort_keys=True).encode()).hexdigest()[:10]
    return Taxonomy(canonicals, key_to_canonical, max_tokens, digest, breaks)


def _lookup(key: str, table: Dict[str, str]) -> Optional[str]:
    if key in table:
        return table[key]
    # "vuejs" -> "vue", "expressjs" -> "express" (and the reverse)
    if len(key) > 4 and key.endswith("js") and key[:-2] in table:
        return table[key[:-2]]
    return None


def canonicalize(term: str) -> str:
    """Map a user/resume skill to the taxonomy canonical name; unknown skills keep their own name."""
    term = clean_skill(term)
    if not term:
        return ""
    table = load_taxonomy_index().key_to_canonical
    key = skill_key(term)
    return _lookup(key, table) or table.get(key + "js") or term


def normalize_skill_list(terms: Iterable[str]) -> List[str]:
    out: List[str] = []
    for term in terms:
        canon = canonicalize(term)
        if canon and canon not in out:
            out.append(canon)
    return out


def is_ambiguous(term: str) -> bool:
    t = clean_skill(term)
    return t in AMBIGUOUS_TERMS or len(skill_key(t)) <= 2


def _line_bounds(text: str, start: int, end: int) -> Tuple[int, int]:
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    return ls, (len(text) if le == -1 else le)


def _accept_ambiguous(text: str, start: int, end: int, anchors: Sequence[int]) -> bool:
    ls, le = _line_bounds(text, start, end)
    before, after = text[ls:start], text[end:le]
    prev_word = (re.findall(r"[a-z0-9+#]+", before)[-1:] or [""])[0]
    next_word = (re.findall(r"[a-z0-9+#]+", after)[:1] or [""])[0]
    if next_word in _CONFIRM_AFTER or prev_word in _CONFIRM_BEFORE:
        return True
    left_char = before.rstrip()[-1:]
    joined = re.search(r"([a-z0-9+#.]+)[\s,]+(?:and|or)\s+$", before)
    if joined and ls + joined.start(1) in anchors:  # "css and react": joined to another skill
        left_char = ","
    if _BULLET_LETTER.fullmatch(before):  # "e hr, payroll": a PDF bullet glyph extracted as a letter
        left_char = ""
    right_char = after.lstrip()[:1]
    if right_char == "&" and (after[1:2].isalnum() or len(skill_key(text[start:end])) <= 2):
        return False  # "R&D", "P&L", "NP & net worth"; a longer name before " & " is a list ("Tally & Focus")
    is_list_item = (left_char == "" or left_char in _LIST_LEFT) and (
        right_char == "" or right_char in _LIST_RIGHT or after.lstrip().startswith(("and ", "& ")))
    if not is_list_item:
        return False
    if _SKILL_HEADINGS.match(text[ls:le]):
        return True
    window = 100 if len(skill_key(text[start:end])) > 1 else 60
    # Another skill on the same line ("Python, Go, Rust") ...
    if any(a != start and ls <= a < le and abs(a - start) <= window for a in anchors):
        return True
    # ... or a vertical list: a short line whose neighbouring lines are skills too.
    if le - ls <= 30:
        block_start = text.rfind("\n", 0, max(0, ls - 1))
        block_start = text.rfind("\n", 0, max(0, block_start)) + 1  # two lines above
        block_end = le
        for _ in range(2):  # two lines below
            nxt = text.find("\n", block_end + 1)
            block_end = len(text) if nxt == -1 else nxt
        return any(a != start and block_start <= a < block_end for a in anchors)
    return False


def _tokens(text: str) -> List[Tuple[int, int]]:
    return [(m.start(), m.end()) for m in _TOKEN.finditer(text)]


def _stray_letter(text: str, toks: List[Tuple[int, int]], i: int, n: int, allowed: Iterable[int]) -> bool:
    """A multi-token match with a lone letter joined to its neighbour only by whitespace, where no written
    form of the skill has a word break at that point. "e hr" (a PDF bullet glyph before "HR") must not spell
    "ehr", nor "a/p and a/r" spell "panda"; "c sharp", "e commerce" and "a b testing" keep matching because
    "c sharp", "e-commerce" and "a/b testing" are written with a break there. Spaced initials inside a
    longer name ("m s office") are accepted; in a short key ("m s" for "ms") they are not."""
    start = toks[i][0]
    long_key = len(skill_key(text[start:toks[i + n - 1][1]])) > 4
    for left in range(i, i + n - 1):
        right = left + 1
        lone_left, lone_right = toks[left][1] - toks[left][0] == 1, toks[right][1] - toks[right][0] == 1
        if not (lone_left or lone_right) or (lone_left and lone_right and long_key):
            continue
        gap = text[toks[left][1]:toks[right][0]]
        if gap and gap.isspace() and len(skill_key(text[start:toks[left][1]])) not in allowed:
            return True
    return False


def _candidates(text: str, toks: List[Tuple[int, int]], table: Dict[str, str], max_n: int,
                breaks: Dict[str, Set[int]]):
    """Yield (start, end, canonical, surface) for the longest key match at each token, left to right."""
    i, n_tok = 0, len(toks)
    while i < n_tok:
        hit = None
        for n in range(min(max_n, n_tok - i), 0, -1):
            start, end = toks[i][0], toks[i + n - 1][1]
            surface = text[start:end]
            if n > 1 and ("\n" in surface or len(surface) > 60):
                continue
            if n > 1 and all(toks[k][1] - toks[k][0] == 1 for k in range(i, i + n)):
                continue  # letter-spaced headings ("h e m a n t h") must not spell skills ("a n t")
            key = skill_key(surface)
            canon = _lookup(key, table)
            if canon is not None and n > 1 and _stray_letter(text, toks, i, n, breaks.get(key, ())):
                continue  # a bullet glyph read as a letter ("e hr policy") must not spell "ehr"
            if canon is not None:
                hit = (start, end, canon, surface, n)
                break
        if hit:
            yield hit[:4]
            i += hit[4]
        else:
            i += 1


@dataclass
class SkillScan:
    skills: List[str] = field(default_factory=list)
    anchors: List[int] = field(default_factory=list)  # positions of unambiguous skill mentions


def scan_taxonomy(text: str) -> SkillScan:
    tax = load_taxonomy_index()
    hits = [(s, e, c, is_ambiguous(surf)) for s, e, c, surf in
            _candidates(text, _tokens(text), tax.key_to_canonical, tax.max_tokens, tax.breaks)]
    anchors = [s for s, _, _, amb in hits if not amb]
    found: Set[str] = set()
    for start, end, canon, amb in hits:
        if canon in found or canon in _NOISE:
            continue
        if amb and not _accept_ambiguous(text, start, end, anchors):
            continue
        found.add(canon)
    return SkillScan(sorted(found), anchors)


def extract_taxonomy_skills(text: str) -> List[str]:
    return scan_taxonomy(text).skills


def find_skills(text: str, skills: Sequence[str], anchors: Optional[Sequence[int]] = None) -> List[str]:
    """Return the subset of `skills` (canonical names) named in `text` exactly or by an equivalent name."""
    found = match_skills(text, skills, anchors)
    return [s for s in skills if s in found and found[s].tier in (EXACT, ALIAS)]


# ----------------------------------------------------------------------------- evidence tiers
#
# A job skill can be supported by four kinds of evidence, strongest first:
#   exact    - the skill's own name                                   ("sql" for SQL)
#   alias    - an equivalent name: taxonomy alias or acronym/expansion ("k8s", "ML" <-> "machine learning")
#   category - a specific skill that belongs to the broader job skill ("mysql" for SQL)  -> partial credit
#   related  - related but not interchangeable                         ("postgresql" for MySQL) -> no credit
# Relationships come from skill_relations.json; direction matters (MySQL supports SQL, SQL never
# proves MySQL; JavaScript never proves React).

EXACT, ALIAS, CATEGORY, RELATED = "exact", "alias", "category", "related"
_TIER_RANK = {EXACT: 0, ALIAS: 1, CATEGORY: 2, RELATED: 3}
_STOPWORDS = {"of", "and", "for", "the", "in", "to", "with", "on", "at", "by", "a", "an"}


@dataclass(frozen=True)
class SkillEvidence:
    skill: str        # the job skill (canonical name)
    tier: str         # exact | alias | category | related
    evidence: str     # what the resume contains (taxonomy canonical, or the matched surface form)
    credit: float     # share of the skill's weight this evidence earns (0..1)
    weak: str = ""    # why the mention is only weak contextual evidence ("" = explicit skill evidence)


_LINK = re.compile(r"(?:://|^www\.|\b[a-z0-9-]+\.(?:com|io|org|dev|me|co|in)/)")
_CONTACT_LINE = re.compile(r"linkedin|\S+@\S+\.[a-z]{2,}|\+?\d[\d ()-]{8,}\d")
_ORG_HEADS = {"department", "dept", "depart", "ministry", "authority", "bureau", "commission"}
_COMPANY_SUFFIXES = {"pvt", "private", "ltd", "limited", "inc", "llc", "llp", "corp", "corporation", "gmbh"}
_NAME_BREAKS = {"at", "in", "with", "for", "of", "from", "to", "and", "as", "developer", "engineer", "analyst",
                "executive", "manager", "consultant", "specialist", "officer", "intern", "trainee"}
_ITEM_EDGE = set(",;|/()[]•*·")
_WORD_END = re.compile(r"\s")


def weak_context(text: str, start: int, end: int) -> str:
    """Why a mention is weak contextual evidence rather than a stated skill ("" if it is explicit)."""
    ws = max(text.rfind(c, 0, start) for c in " \n\t") + 1
    m = _WORD_END.search(text, end)
    if _LINK.search(text[ws:m.start() if m else len(text)]):
        return "web or profile link"           # "https://github.com/user", "github.com/user"
    ls, le = _line_bounds(text, start, end)
    left = text[ls:start].rstrip()[-1:]
    right = text[end:le].lstrip()[:1]
    if (left == "" or left in _ITEM_EDGE) and (right == "" or right in _ITEM_EDGE) \
            and le - ls <= 160 and _CONTACT_LINE.search(text[ls:le]):
        return "label on a contact line"       # "<email> | github | linkedin"
    item = re.split(r"[,;|/()\[\]•·@:–—]|\s-\s", text[end:le], maxsplit=1)[0]
    following = re.findall(r"[a-z]+", item)[:3]
    if following and following[0] in _ORG_HEADS:
        return "part of an organisation name"  # "income tax department"
    for w in following:
        if w in _NAME_BREAKS:
            break                              # "data analyst at abc pvt ltd": a role, not the company
        if w in _COMPANY_SUFFIXES:
            return "part of an organisation name"  # "tally solutions pvt ltd"
    return ""


def _evidence_rank(credit: float, tier: str) -> Tuple[float, int]:
    """Lower is better: more credit first, then the more specific tier (weak exact beats related)."""
    return -credit, _TIER_RANK[tier]


def tier_credit(tier: str) -> float:
    return {EXACT: 1.0, ALIAS: 1.0, CATEGORY: config.CATEGORY_MATCH_CREDIT}.get(tier, 0.0)


@dataclass
class Relations:
    members: Dict[str, List[str]]   # broad skill -> specific skills that support it
    parents: Dict[str, Set[str]]    # specific skill -> broad skills it supports
    related: Dict[str, Set[str]]    # skill -> related, non-interchangeable skills
    conflicts: Dict[str, Set[str]] = field(default_factory=dict)  # surface form -> words signalling another sense


@lru_cache(maxsize=1)
def load_relations() -> Relations:
    raw: dict = {}
    if config.SKILL_RELATIONS_PATH.exists():
        raw = json.loads(config.SKILL_RELATIONS_PATH.read_text(encoding="utf-8"))
    members: Dict[str, List[str]] = {}
    parents: Dict[str, Set[str]] = {}
    related: Dict[str, Set[str]] = {}
    for broad, specifics in (raw.get("categories") or {}).items():
        b = canonicalize(broad)
        for spec in specifics or []:
            s = canonicalize(spec)
            if s and s != b and s not in members.setdefault(b, []):
                members[b].append(s)
                parents.setdefault(s, set()).add(b)
    for group in raw.get("related") or []:
        names = [canonicalize(x) for x in group if canonicalize(x)]
        for a in names:
            related.setdefault(a, set()).update(x for x in names if x != a)
    for b, specs in members.items():  # siblings are related; a member's broad skill is related to it
        for s in specs:
            related.setdefault(s, set()).update(x for x in specs if x != s)
            related[s].add(b)
    conflicts = {skill_key(k): {w.lower() for w in v} for k, v in (raw.get("conflicts") or {}).items()}
    return Relations(members, parents, related, conflicts)


def _descendants(skill: str, rel: Relations, depth: int = 2) -> List[str]:
    out: List[str] = []
    frontier = [skill]
    for _ in range(depth):
        nxt = []
        for s in frontier:
            for m in rel.members.get(s, []):
                if m != skill and m not in out:
                    out.append(m)
                    nxt.append(m)
        frontier = nxt
    return out


def initialism(term: str, keep_stopwords: bool = False) -> str:
    """'search engine optimization' -> 'seo', 'computer-aided design' -> 'cad'; '' for single words.
    keep_stopwords: 'bill of materials' -> 'bom' (default 'bm')."""
    words = [w for w in re.findall(r"[a-z]+", (term or "").lower()) if keep_stopwords or w not in _STOPWORDS]
    return "".join(w[0] for w in words) if len(words) >= 2 else ""


def _forms(canon: str) -> List[str]:
    return [canon, *load_taxonomy_index().canonicals.get(canon, [])]


@lru_cache(maxsize=256)
def _tier_tables(skills: Tuple[str, ...]):
    """key -> [(job_skill, tier, evidence, forced_ambiguous)] for the direct pass and the relations pass."""
    rel = load_relations()
    direct: Dict[str, list] = {}
    related: Dict[str, list] = {}
    breaks: Dict[str, Set[int]] = {}
    max_direct = max_rel = 1

    def put(table, key_form, entry):
        key = skill_key(key_form)
        if key and entry not in table.setdefault(key, []):
            table[key].append(entry)
        breaks.setdefault(key, set()).update(_word_breaks(key_form))
        return _token_count(key_form) + 1  # one spare token: spaced spellings ("m s office") split a token

    for skill in skills:
        for form in _forms(skill):
            tier = EXACT if skill_key(form) == skill_key(skill) else ALIAS
            max_direct = max(max_direct, put(direct, form, (skill, tier, skill, False)))
            for acr in {initialism(form), initialism(form, keep_stopwords=True)}:
                if len(acr) >= 3:  # derived acronym (3+ letters): only accepted in a skills/list context
                    put(direct, acr, (skill, ALIAS, acr, True))
        for member in _descendants(skill, rel):
            for form in _forms(member):
                max_rel = max(max_rel, put(related, form, (skill, CATEGORY, member, False)))
        for other in sorted(rel.related.get(skill, set()) | rel.parents.get(skill, set())):
            if other in skills:
                continue
            for form in _forms(other):
                max_rel = max(max_rel, put(related, form, (skill, RELATED, other, False)))
    return direct, max_direct, related, max_rel, breaks


def _other_sense(text: str, start: int, end: int, words: Optional[Set[str]], window: int = 80) -> bool:
    """True when a word signalling a different meaning of the name is nearby ("aws ... emr")."""
    if not words:
        return False
    near = text[max(0, start - window): start] + " " + text[end: end + window]
    return any(w in words for w in re.findall(r"[a-z0-9]+", near))


def match_skills(text: str, skills: Sequence[str], anchors: Optional[Sequence[int]] = None
                 ) -> Dict[str, SkillEvidence]:
    """Best evidence in `text` for each job skill (canonical names). Skills with no evidence are absent."""
    skills = tuple(s for s in skills if s)
    if not skills or not text:
        return {}
    if any(c.isupper() for c in text):  # parsed text is already lowercase; callers may pass raw text
        text = text.lower()
    if anchors is None:
        anchors = scan_taxonomy(text).anchors
    toks = _tokens(text)
    direct, max_direct, related, max_rel, breaks = _tier_tables(skills)
    conflicts = load_relations().conflicts
    best: Dict[str, SkillEvidence] = {}

    def offer(entries, start, end, surface):
        weak = None  # computed once per mention, only when needed
        for skill, tier, evidence, forced in entries:
            cur = best.get(skill)
            if cur is not None and _evidence_rank(cur.credit, cur.tier) <= _evidence_rank(tier_credit(tier), tier):
                continue  # already have evidence at least this good
            if (forced or is_ambiguous(surface)) and not _accept_ambiguous(text, start, end, anchors):
                continue
            if _other_sense(text, start, end, conflicts.get(skill_key(surface))):
                continue
            credit = tier_credit(tier)
            if credit > 0:
                if weak is None:
                    weak = weak_context(text, start, end)
                if weak:
                    credit = min(credit, config.WEAK_EVIDENCE_CREDIT)
                    if cur is not None and _evidence_rank(cur.credit, cur.tier) <= _evidence_rank(credit, tier):
                        continue
            best[skill] = SkillEvidence(skill, tier, evidence if tier != ALIAS or forced else surface.strip(),
                                        credit, weak or "")

    for start, end, entries, surface in _candidates(text, toks, direct, max_direct, breaks):
        offer(entries, start, end, surface)
    if any(s not in best or best[s].tier != EXACT or best[s].weak for s in skills):
        for start, end, entries, surface in _candidates(text, toks, related, max_rel, breaks):
            offer(entries, start, end, surface)
    return best
