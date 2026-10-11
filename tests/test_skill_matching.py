"""Skill matching across domains: equivalent names, broader/narrower skills, related skills, false
positives, scoring credit, filtering and ranking (synthetic text only; no real resumes)."""
from tests import helpers  # noqa: F401

import asyncio
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from ats import config
from ats.api import build_job_profile
from ats.matcher import match_job_skills
from ats.orchestrator import run_bulk
from ats.parser import normalize_text
from ats.prefilter import passes_prefilter
from ats.scorer import compute_scores, describe_match, related_line
from ats.skills import (ALIAS, scan_taxonomy, CATEGORY, EXACT, RELATED, canonicalize, find_skills, initialism, load_relations,
                        load_taxonomy_index, match_skills, normalize_skill_list, skill_key)


def tiers(text, skills):
    """{job skill: (tier, evidence)} for raw resume text, through the production text normalizer."""
    found = match_skills(normalize_text(text)[0], normalize_skill_list(skills))
    return {k: (v.tier, v.evidence) for k, v in found.items()}


def tier_of(text, skill):
    return tiers(text, [skill]).get(canonicalize(skill), (None, None))[0]


class EquivalentNameTests(unittest.TestCase):
    """Abbreviations, acronyms, spellings: full credit (exact or alias), in both directions."""

    CASES = [  # (job skill, resume text, expected tier)
        ("javascript", "Skills: JS, HTML, CSS", ALIAS),
        ("typescript", "Skills: TS, Angular", ALIAS),
        ("machine learning", "Skills: ML, Python", ALIAS),
        ("ml", "Built machine learning models for churn", EXACT),  # job acronym -> canonical full name
        ("deep learning", "Skills: DL, PyTorch", ALIAS),
        ("natural language processing", "Skills: NLP, spaCy", EXACT),
        ("nlp", "Worked on natural language processing pipelines", ALIAS),
        ("aws", "Deployed on Amazon Web Services", ALIAS),
        ("amazon web services", "Skills: AWS, Linux", EXACT),
        ("kubernetes", "Skills: k8s, helm", ALIAS),
        ("postgresql", "Skills: Postgres, Redis", ALIAS),
        ("sql", "Skills: T-SQL, SSIS", ALIAS),
        ("sql server", "Skills: MSSQL, SSRS", ALIAS),
        ("cad", "Proficient in Computer-Aided Design for sheet metal parts", ALIAS),
        ("computer aided design", "Skills: CAD, GD&T", EXACT),
        ("plc", "Programmed Programmable Logic Controllers for packaging lines", ALIAS),
        ("cnc", "Operated Computer Numerical Control machines", ALIAS),
        ("human resources", "Skills: HR, payroll, onboarding", ALIAS),
        ("hr", "Human Resources generalist for 300 staff", EXACT),
        ("seo", "Skills: Search Engine Optimisation, Google Ads", ALIAS),
        ("search engine optimization", "Skills: SEO, SEM", EXACT),
        ("financial modeling", "Built financial modelling templates", ALIAS),
        ("gd&t", "Applied geometric dimensioning and tolerancing on drawings", ALIAS),
        ("electronic health records", "Charted patient notes in EMR, BLS certified", ALIAS),
        ("basic life support", "Certifications: BLS, ACLS", ALIAS),
        ("power bi", "Skills: Power-BI, Excel", EXACT),  # separators are normalized away
        ("linux", "Skills: RHEL, Bash", ALIAS),
    ]

    def test_equivalent_names_match_with_full_credit(self):
        for skill, text, expected in self.CASES:
            with self.subTest(skill=skill, text=text):
                job = build_job_profile("Role", "", [skill], [], 0, "any")
                m = match_job_skills(normalize_text(text)[0], job)
                ev = {e["skill"]: e for e in m.evidence}.get(job.required_skills[0])
                self.assertIsNotNone(ev, f"{skill!r} not found in {text!r}")
                self.assertEqual(ev["tier"], expected)
                self.assertEqual(m.required_credit, 1.0)
                self.assertEqual(m.matched, job.required_skills)

    def test_job_terms_normalize_to_one_canonical_name(self):
        for group in (["ML", "Machine Learning", "machine-learning"], ["AWS", "Amazon Web Services"],
                      ["HR", "Human Resources", "human resource"], ["CAD", "Computer-Aided Design"],
                      ["SEO", "search engine optimisation"], ["MSSQL", "MS SQL Server", "SQL Server"]):
            self.assertEqual(len({canonicalize(t) for t in group}), 1, group)

    def test_generic_acronym_derivation_for_skills_outside_the_taxonomy(self):
        # Neither skill is in the taxonomy: the acronym is derived from the job's own wording.
        self.assertEqual(tier_of("Skills: SOP, Excel, SAP", "standard operating procedures"), ALIAS)
        self.assertEqual(tier_of("Tools: FMEA, PPAP, Control Plans", "production part approval process"), ALIAS)
        self.assertEqual(initialism("computer-aided design"), "cad")
        self.assertEqual(initialism("bill of materials", keep_stopwords=True), "bom")

    def test_ordinary_phrases_are_not_spelled_out_acronyms(self):
        # Free-text acronym expansion was evaluated on real resumes and removed: nearly every hit was an
        # ordinary phrase with the right initials. Spelled-out forms must be taxonomy aliases.
        for skill, text in [("sap", "Set work schedules and procedures for the team"),
                            ("plc", "Followed the project life cycle methodology"),
                            ("css", "Delivered client-specific solutions"),
                            ("cad", "Overcame complex challenges and delivered results"),
                            ("crm", "Compliance and risk management"),
                            ("hmi", "Heavy metal inorganic analysis"),
                            ("plc", "Worked at Roots Cast Pvt Ltd Coimbatore"),
                            ("bom", "Prepared bill of materials reports")]:
            with self.subTest(skill=skill, text=text):
                self.assertIsNone(tier_of(text, skill))

    def test_raw_mixed_case_text_is_handled(self):
        self.assertEqual(find_skills("Skills: Python 3.8, MS SQL Server", ["python", "sql"]), ["python", "sql"])


class CategoryAndRelatedTests(unittest.TestCase):
    """Specific tool -> broader skill earns partial credit; the reverse, and siblings, earn none."""

    def test_specific_tool_supports_broader_skill_with_partial_credit(self):
        cases = [("sql", "Skills: MySQL, Java", "mysql"), ("sql", "Databases: PostgreSQL, Redis", "postgresql"),
                 ("sql", "Skills: PL/SQL, Oracle Forms", None), ("javascript", "Skills: React, Redux", "react"),
                 ("python", "Skills: Django, Celery", "django"), ("aws", "Deployed on EC2 and S3", "ec2"),
                 ("cad", "Skills: AutoCAD, SolidWorks", "autocad"), ("human resources", "Skills: recruitment, payroll", "recruitment"),
                 ("accounting", "Tools: Tally ERP 9, GST filing", "tally"), ("taxation", "Tools: Tally, GST returns", "gst"),
                 ("ms office", "Skills: Excel, PowerPoint", "excel"), ("agile", "Worked in Scrum teams; Skills: Scrum, Jira", "scrum"),
                 ("machine learning", "Hands-on with TensorFlow and Keras", "tensorflow"),  # two levels: ML <- DL <- TF
                 ("cloud computing", "Skills: EC2, Lambda", "ec2"), ("digital marketing", "Skills: SEO, Google Ads", "seo"),
                 ("hdl", "Skills: Verilog, ModelSim", "verilog"), ("patient care", "Certifications: BLS", "basic life support")]
        for skill, text, member in cases:
            with self.subTest(skill=skill, text=text):
                tier, evidence = tiers(text, [skill])[canonicalize(skill)]
                if member is None:  # 'pl/sql' literally contains the token 'sql' (unchanged behaviour)
                    self.assertEqual(tier, EXACT)
                    continue
                self.assertEqual((tier, evidence), (CATEGORY, member))
                job = build_job_profile("Role", "", [skill], [], 0, "any")
                m = match_job_skills(normalize_text(text)[0], job)
                self.assertEqual(m.required_credit, config.CATEGORY_MATCH_CREDIT)
                self.assertLess(m.required_credit, 1.0)

    def test_broader_or_sibling_skill_is_related_with_no_credit(self):
        cases = [("mysql", "Skills: SQL, Python", "sql"), ("mysql", "Skills: PostgreSQL", "postgresql"),
                 ("react", "Skills: JavaScript, HTML, CSS", "javascript"), ("react", "Skills: Angular, TypeScript", "angular"),
                 ("tensorflow", "Skills: PyTorch, NumPy", "pytorch"), ("azure", "Cloud: AWS, GCP", "aws"),
                 ("catia", "Skills: AutoCAD, SolidWorks", "autocad"), ("kubernetes", "Skills: Docker, Jenkins", "docker"),
                 ("django", "Skills: Python, Flask", "flask"), ("payroll", "Skills: recruitment, onboarding", "recruitment"),
                 ("c", "Skills: C++, Python", "c++")]
        for skill, text, other in cases:
            with self.subTest(skill=skill, text=text):
                tier, evidence = tiers(text, [skill])[canonicalize(skill)]
                self.assertEqual(tier, RELATED)
                job = build_job_profile("Role", "", [skill], [], 0, "any")
                m = match_job_skills(normalize_text(text)[0], job)
                self.assertEqual((m.matched, m.missing, m.required_credit), ([], job.required_skills, 0.0))

    def test_specificity_is_preserved(self):
        self.assertNotIn(tier_of("Skills: JavaScript, jQuery", "react"), (EXACT, ALIAS, CATEGORY))
        self.assertNotIn(tier_of("Skills: SQL", "postgresql"), (EXACT, ALIAS, CATEGORY))
        self.assertNotIn(tier_of("Skills: Python", "django"), (EXACT, ALIAS, CATEGORY))
        self.assertNotIn(tier_of("Skills: Excel", "power bi"), (EXACT, ALIAS, CATEGORY))

    def test_unrelated_lookalikes_never_match(self):
        for skill, text in [("javascript", "Proficient in Java and Spring"), ("java", "Skills: JavaScript, HTML"),
                            ("c", "Skills: C#, Python"), ("c#", "Skills: C, C++"), ("r", "Skills: Rust, Ruby"),
                            ("scala", "Built a scalable platform"), ("sql", "Skills: NoSQL, MongoDB"),
                            ("sales", "Skills: Salesforce administration"), ("excel", "Skills: Excellent communication")]:
            with self.subTest(skill=skill, text=text):
                self.assertIsNone(tier_of(text, skill))


class FalsePositiveTests(unittest.TestCase):
    """Ordinary words and short acronyms are only skills in a technical/list context."""

    def test_ordinary_words_are_not_skills(self):
        for skill, text in [("go", "Ready to go the extra mile"), ("spring", "Joined in spring 2021"),
                            ("react", "I react quickly to customer issues"), ("excel", "I excel at teamwork"),
                            ("react", "Listen to feedback and react."),
                            ("r", "Skills: Python, R&D, SQL"), ("r", "Areas: Finance, P&L, R&D"),
                            ("outlook", "Positive outlook towards work"), ("sem", "6th sem student, CGPA 8.1"),
                            ("make", "Make decisions under pressure"), ("tally", "Kept a tally of daily orders"),
                            ("hr", "Worked 9 hr shifts"), ("ml", "Measured 250 ml samples"),
                            ("standard operating procedures", "The sop was followed by all"),
                            ("seo", "Worked in sales and marketing"), ("cnc", "Collected customer complaints")]:
            with self.subTest(skill=skill, text=text):
                self.assertIsNone(tier_of(text, skill))

    def test_name_used_in_another_sense_is_not_evidence(self):
        # conflicts in skill_relations.json: nearby words that signal a different meaning of the name
        self.assertIsNone(tier_of("AWS: S3, Glue, Lambda, EMR, Redshift", "electronic health records"))
        self.assertIsNone(tier_of("Big data processing with PySpark on EMR clusters", "electronic health records"))
        self.assertEqual(tier_of("Charted patient notes in EMR, Epic", "electronic health records"), ALIAS)
        self.assertIsNone(tier_of("Skills: Oracle ERP, Tally, MS Office", "sql"))
        self.assertIsNone(tier_of("Software: SAP, Oracle, BlackLine", "sql"))  # the ERP, not the database
        self.assertEqual(tier_of("Tools: Spring Boot, Hibernate, Oracle, Jenkins", "sql"), CATEGORY)
        self.assertIsNone(tier_of("Prepared BLS against exports and remittances", "basic life support"))
        self.assertEqual(tier_of("Certifications: BLS, ACLS", "basic life support"), ALIAS)
        self.assertIsNone(tier_of("Wrote business requirement specifications (BRS) for tools", "bank reconciliation"))
        self.assertEqual(tier_of("Prepared BRS and ledger scrutiny monthly", "bank reconciliation"), ALIAS)
        self.assertEqual(tier_of("Databases: Oracle, MySQL", "sql"), CATEGORY)
        self.assertIsNone(tier_of("Measured angular velocity of the shaft", "javascript"))
        self.assertEqual(tier_of("Led onboarding of third-party services", "human resources"), RELATED)  # no credit

    def test_common_job_titles_still_match(self):
        # "sales" stays an ordinary skill (as before): restricting it lost real sales roles.
        self.assertEqual(tier_of("Position: Sales Representative", "sales"), EXACT)

    def test_spaced_ampersand_joins_list_items(self):
        # "Tally & Focus" is a list; "R&D" / "P&L" (no spaces) are single terms and never yield "r"/"p".
        self.assertEqual(tier_of("Accounting packages: SAP FI/CO, Tally & Focus", "tally"), EXACT)
        self.assertEqual(tier_of("Skills: Python & SQL", "python"), EXACT)
        self.assertIsNone(tier_of("Skills: Python, R&D, SQL", "r"))
        self.assertIsNone(tier_of("Ratios: leverage, trend of sales, NP & net worth, Excel", "python"))  # NP: net profit

    def test_same_terms_count_in_technical_context(self):
        for skill, text in [("go", "Skills: Go, Rust, Python"), ("spring", "Built Spring Boot services"),
                            ("react", "Built UI using React and Redux"), ("react", "Frontend: React"),
                            ("react", "Worked with HTML, CSS, JavaScript, and React."),
                            ("react", "Utilized React to build a responsive front end"),
                            ("excel", "Skills: Excel, Power BI"), ("ml", "Skills: ML, NLP, Python"),
                            ("hr", "Skills: HR, payroll")]:
            with self.subTest(skill=skill, text=text):
                self.assertIn(tier_of(text, skill), (EXACT, ALIAS))  # not merely "related"


class BulletGlyphTests(unittest.TestCase):
    """A PDF bullet read as a letter must not join the next word into a short acronym."""

    def test_stray_letter_does_not_spell_an_acronym(self):
        for text in ["e hr policy development and implementation", "o hr operations and payroll",
                     "Responsibilities:\ne hr audits"]:
            with self.subTest(text=text):
                self.assertIsNone(tier_of(text, "electronic health records"))
                self.assertNotIn("electronic health records", scan_taxonomy(normalize_text(text)[0]).skills)

    def test_bullet_letter_before_a_list_item_still_counts(self):
        # "• HR" extracted as "e hr" (or "o hr"): the real word after the bullet still counts
        self.assertEqual(tier_of("Skills:\ne hr, payroll, recruitment", "human resources"), ALIAS)
        for text in ["Skills:\no HR, Payroll, Recruitment", "Core areas\nl HR, payroll", "Skills:\n• HR, payroll"]:
            with self.subTest(text=text):
                self.assertEqual(tier_of(text, "human resources"), ALIAS)
                self.assertIsNone(tier_of(text, "electronic health records"))

    def test_letter_that_is_not_a_bullet_does_not_open_a_list(self):
        for text in ["e hr policy development and implementation",  # prose after the bullet: no list item
                     "Worked on e hr, payroll systems",  # mid-line letter is not a bullet
                     "a hr, payroll"]:  # "a" is a word, not a bullet glyph
            with self.subTest(text=text):
                self.assertNotIn(tier_of(text, "human resources"), (EXACT, ALIAS))
                self.assertIsNone(tier_of(text, "electronic health records"))

    def test_stray_letters_do_not_spell_longer_names(self):
        # "a/p and a/r": the letters around "and" join into "panda" (a pandas alias)
        for skill, text in [("python", "Centralized A/P and A/R across 5 entities"),
                            ("pandas", "Centralized A/P and A/R across 5 entities"),
                            ("pandas", "Handled a p and a r reconciliations")]:
            with self.subTest(skill=skill, text=text):
                self.assertIsNone(tier_of(text, skill))
        self.assertNotIn("pandas", scan_taxonomy(normalize_text("Centralized A/P and A/R")[0]).skills)

    def test_spaced_letters_match_where_the_name_has_a_break(self):
        for skill, text, tier in [("pandas", "Libraries: Pandas, NumPy", EXACT),
                                  ("e-commerce", "Built e commerce storefronts", EXACT),
                                  ("a/b testing", "Ran A B testing on landing pages", EXACT),
                                  ("c#", "Languages: C Sharp, Java", ALIAS),
                                  ("ms office", "Good knowledge in M S Office tools", EXACT),
                                  ("ms office", "Skills: Typing, M S Office, Tally", EXACT)]:
            with self.subTest(skill=skill, text=text):
                self.assertEqual(tier_of(text, skill), tier)

    def test_legitimate_short_forms_still_match(self):
        for skill, text, tier in [("electronic health records", "Charted notes in EHR, Epic", ALIAS),
                                  ("electronic health records", "Skills: EHR / EMR", ALIAS),
                                  ("sql", "Skills: T-SQL, SSIS", ALIAS), ("plsql", "Skills: PL/SQL", EXACT),
                                  ("ci/cd", "Built CI/CD pipelines", EXACT), ("c++", "Languages: C++, C#", EXACT),
                                  ("c#", "Languages: C#, Java", EXACT), ("c#", "Languages: C Sharp, Java", ALIAS),
                                  ("a/b testing", "Ran A/B testing on landing pages", EXACT),
                                  ("kubernetes", "Skills: k8s, helm", ALIAS), ("hr", "Skills: HR, payroll", ALIAS)]:
            with self.subTest(skill=skill, text=text):
                self.assertEqual(tier_of(text, skill), tier)


class WeakEvidenceTests(unittest.TestCase):
    """Profile links, contact-line labels and organisation names are weak evidence: shown, not credited."""

    def evidence(self, text, skill):
        return match_skills(normalize_text(text)[0], normalize_skill_list([skill]))[canonicalize(skill)]

    def test_weak_contexts_earn_no_credit(self):
        for skill, text, note in [
                ("git", "Portfolio: https://github.com/someone", "web or profile link"),
                ("git", "github.com/someone/project-x", "web or profile link"),
                ("git", "someone@example.com | github | linkedin", "label on a contact line"),
                ("taxation", "Client: Income Tax Department, Government", "part of an organisation name"),
                ("sap", "Employer: SAP Labs India Pvt Ltd", "part of an organisation name")]:
            with self.subTest(text=text):
                ev = self.evidence(text, skill)
                self.assertEqual((ev.credit, ev.weak), (config.WEAK_EVIDENCE_CREDIT, note))

    def test_explicit_mentions_are_not_weak(self):
        for skill, text, credit in [("git", "Tools: Git, GitHub, Jira", 1.0),
                                    ("git", "Hosted code on GitHub with pull-request reviews", config.CATEGORY_MATCH_CREDIT),
                                    ("taxation", "Filed income tax returns for 40 clients", config.CATEGORY_MATCH_CREDIT),
                                    ("python", "Python developer | someone@example.com | +91 98765 43210", 1.0),
                                    ("data analysis", "Data Analyst at ABC Analytics Pvt Ltd", 1.0),
                                    ("java", "Java Developer - ABC Pvt Ltd", 1.0),
                                    ("human resources", "HR Executive, PeopleFirst Services Pvt Ltd", 1.0)]:
            with self.subTest(text=text):
                ev = self.evidence(text, skill)
                self.assertEqual((ev.credit, ev.weak), (credit, ""))

    def test_explicit_mention_elsewhere_wins_over_weak(self):
        ev = self.evidence("github.com/someone\nSkills: Git, Docker", "git")
        self.assertEqual((ev.tier, ev.credit, ev.weak), (EXACT, 1.0, ""))
        ev = self.evidence("github.com/someone\nSkills: GitLab, Docker", "git")
        self.assertEqual((ev.tier, ev.credit, ev.weak), (CATEGORY, config.CATEGORY_MATCH_CREDIT, ""))

    def test_weak_only_is_filtered_and_explained(self):
        job = build_job_profile("Frontend", "", ["react"], ["git"], 0, "any")
        m = match_job_skills(normalize_text("someone@example.com | github | linkedin\nSkills: Cooking")[0], job)
        self.assertEqual((m.matched, m.matched_preferred, m.bonus), ([], [], 0.0))
        self.assertEqual(m.evidence[0]["strength"], "weak")
        self.assertFalse(passes_prefilter(m.matched, m.matched_preferred, job))
        self.assertIn("Weak evidence, not counted: github for git (label on a contact line)", related_line(m.evidence))

    def test_configured_weak_credit_is_partial_and_labelled(self):
        with mock.patch.object(config, "WEAK_EVIDENCE_CREDIT", 0.25):
            job = build_job_profile("Tax", "", ["taxation"], [], 0, "any")
            m = match_job_skills(normalize_text("Client: Income Tax Department")[0], job)
            self.assertEqual((m.matched, m.required_credit), (["taxation"], 0.25))
            self.assertIn("weak: income tax as part of an organisation name, 25% credit",
                          describe_match("taxation", m.evidence[0]))


class ScoringAndFilteringTests(unittest.TestCase):
    def job(self, req, pref=(), years=0):
        return build_job_profile("Backend Engineer", "Backend services", list(req), list(pref), years, "any")

    def test_skills_score_uses_evidence_credit(self):
        job = self.job(["sql", "python"])
        m = match_job_skills(normalize_text("Skills: MySQL, Python")[0], job)
        self.assertEqual(m.matched, ["sql", "python"])
        self.assertAlmostEqual(m.required_credit, 1.0 + config.CATEGORY_MATCH_CREDIT)
        s = compute_scores(m.matched, m.missing, m.bonus, 0, None, "unknown", job, False, m.required_credit)
        self.assertAlmostEqual(s.skills, (1.0 + config.CATEGORY_MATCH_CREDIT) / 2 * 100)
        exact = compute_scores(["sql", "python"], [], 0, 0, None, "unknown", job, False, 2.0)
        self.assertGreater(exact.skills, s.skills)
        # weights unchanged: 40/30/20/10
        self.assertEqual((config.WEIGHT_SKILLS, config.WEIGHT_EXPERIENCE, config.WEIGHT_SEMANTIC,
                          config.WEIGHT_EDUCATION), (0.40, 0.30, 0.20, 0.10))

    def test_preferred_bonus_uses_credit(self):
        job = self.job(["java"], ["sql", "docker"])
        m = match_job_skills(normalize_text("Skills: Java, MySQL, Docker")[0], job)
        self.assertEqual(m.matched_preferred, ["sql", "docker"])
        self.assertAlmostEqual(m.bonus, (config.CATEGORY_MATCH_CREDIT + 1.0) * 2.0)

    def test_prefilter_uses_improved_matching(self):
        job = self.job(["python", "sql", "docker"])
        # Previously filtered: MySQL is not the literal skill "sql" (find_skills = exact/alias only).
        text = normalize_text("Java developer. Skills: Java, Spring Boot, MySQL, REST APIs")[0]
        self.assertEqual(find_skills(text, job.required_skills), [])
        m = match_job_skills(text, job)
        self.assertTrue(passes_prefilter(m.matched, m.matched_preferred, job))
        # Related-only evidence (no credit) does not pass; irrelevant resumes still filtered.
        job2 = self.job(["mysql"])
        m2 = match_job_skills(normalize_text("Skills: PostgreSQL, Python")[0], job2)
        self.assertFalse(passes_prefilter(m2.matched, m2.matched_preferred, job2))
        m3 = match_job_skills(normalize_text("Chef. Menu planning, food safety")[0], job)
        self.assertFalse(passes_prefilter(m3.matched, m3.matched_preferred, job))

    def test_experience_relevance_counts_category_evidence(self):
        job = self.job(["sql"], years=2)
        m = match_job_skills(normalize_text("Skills: MySQL")[0], job)
        s = compute_scores(m.matched, m.missing, m.bonus, 4, None, "unknown", job, False, m.required_credit)
        self.assertEqual(s.experience, 100.0)


class PipelineTests(unittest.TestCase):
    """End to end through run_bulk: statuses, ranking, explanation transparency."""

    def test_ranking_exact_above_category_above_related_and_filtering(self):
        with tempfile.TemporaryDirectory() as d:
            texts = {
                "exact.txt": "Skills: SQL, Python, Docker\nWork Experience\nData Engineer, Acme Pvt Ltd  2019 - 2023",
                "category.txt": "Skills: MySQL, Python, Docker\nWork Experience\nData Engineer, Acme Pvt Ltd  2019 - 2023",
                "related.txt": "Skills: MongoDB, Java\nWork Experience\nDeveloper, Acme Pvt Ltd  2019 - 2023",
                "irrelevant.txt": "Skills: Cooking, driving\nWork Experience\nCook, Hotel Ltd  2019 - 2023",
                "recovered.txt": "Skills: Java, Spring Boot, MySQL, REST APIs\nWork Experience\nDeveloper, Acme Pvt Ltd  2019 - 2023",
            }
            for n, t in texts.items():
                (Path(d) / n).write_text(t, encoding="utf-8")
            job = build_job_profile("Data Engineer", "Build data pipelines", ["sql", "python", "docker"], [], 2, "any")
            out = asyncio.run(run_bulk([str(Path(d) / n) for n in texts], job, top_n=5, semantic=False))
            res = {Path(r.file_path).name: r for r in out.all_results}
            self.assertEqual(res["irrelevant.txt"].status, "filtered")
            self.assertEqual(res["related.txt"].status, "filtered")  # MongoDB/Java: no credit for sql/python/docker
            self.assertEqual(res["recovered.txt"].status, "processed")
            self.assertGreater(res["exact.txt"].scores.final, res["category.txt"].scores.final)
            self.assertGreater(res["category.txt"].scores.final, res["recovered.txt"].scores.final)
            self.assertIn("via mysql", res["category.txt"].explanation)
            self.assertIn(f"{config.CATEGORY_MATCH_CREDIT:.0%} credit", res["category.txt"].explanation)
            ev = {e["skill"]: e for e in res["category.txt"].skill_evidence}
            self.assertEqual((ev["sql"]["tier"], ev["sql"]["evidence"], ev["python"]["tier"]), (CATEGORY, "mysql", EXACT))

    def test_filtered_explanation_names_related_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.txt").write_text("Skills: PostgreSQL, Python", encoding="utf-8")
            job = build_job_profile("DBA", "", ["mysql"], [], 0, "any")
            out = asyncio.run(run_bulk([str(Path(d) / "a.txt")], job, top_n=1, semantic=False))
            r = out.all_results[0]
            self.assertEqual(r.status, "filtered")
            self.assertIn("postgresql (for mysql)", r.explanation)
            self.assertEqual(r.skill_evidence[0]["tier"], RELATED)

    def test_weak_only_resume_is_filtered_with_reason(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.txt").write_text("someone@example.com | github | linkedin\nSkills: Cooking", encoding="utf-8")
            (Path(d) / "b.txt").write_text("Skills: Git, Cooking", encoding="utf-8")
            job = build_job_profile("Dev", "", ["git"], [], 0, "any")
            out = asyncio.run(run_bulk([str(Path(d) / n) for n in ("a.txt", "b.txt")], job, top_n=2, semantic=False))
            res = {Path(r.file_path).name: r for r in out.all_results}
            self.assertEqual((res["a.txt"].status, res["b.txt"].status), ("filtered", "processed"))
            self.assertIn("Weak evidence, not counted", res["a.txt"].explanation)


class RelationsDataTests(unittest.TestCase):
    """The relations file stays consistent with the taxonomy as both grow."""

    def test_every_relation_name_resolves_to_a_taxonomy_skill(self):
        keys = set(load_taxonomy_index().key_to_canonical)
        raw = json.loads(config.SKILL_RELATIONS_PATH.read_text(encoding="utf-8"))
        names = set(raw["categories"]) | {m for ms in raw["categories"].values() for m in ms} | \
            {x for g in raw["related"] for x in g}
        unknown = sorted(n for n in names if skill_key(n) not in keys)
        self.assertEqual(unknown, [], "add these to skill_taxonomy.json")
        unknown_forms = sorted(n for n in raw["conflicts"] if skill_key(n) not in keys)
        self.assertEqual(unknown_forms, [], "conflict keys must be taxonomy surface forms")

    def test_no_skill_is_its_own_category_member(self):
        rel = load_relations()
        for broad, members in rel.members.items():
            self.assertNotIn(broad, members)
            for m in members:  # no 2-cycles (A supports B and B supports A)
                self.assertNotIn(broad, rel.members.get(m, []), (broad, m))


if __name__ == "__main__":
    unittest.main()
