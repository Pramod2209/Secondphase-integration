"""Domain-agnostic validation on synthetic controlled resumes (11 professions).

For every domain: a realistic job, a strong match, a partial match and an irrelevant resume.
The production pipeline (run_bulk, prefilter off so all three are scored) must rank
strong > partial > irrelevant, with semantic scoring both on and off, and job-specific skills
that are NOT in the skill taxonomy must still be matched from the resume text.
Nothing here is special-cased in the scorer: the job definition alone drives matching.
"""
from tests import helpers  # noqa: F401

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ats import config
from ats.api import build_job_profile
from ats.orchestrator import run_bulk
from ats.skills import load_taxonomy_index, skill_key


def resume(name, title, skills, jobs, education, summary):
    return "\n".join([name, title, f"{name.lower().replace(' ', '.')}@example.com | +91 90000 00000", "",
                      "Summary", summary, "", "Skills", skills, "", "Work Experience", *jobs, "", "Education",
                      education])


IRRELEVANT_POOL = {
    "cook": resume("Ramesh Pillai", "Chef", "Menu planning, food safety, kitchen hygiene, inventory",
                   ["Head Cook, Spice Garden Restaurant Pvt Ltd  2012 - 2022"], "SSLC, State Board 2008",
                   "Cook with ten years of restaurant kitchen work."),
    "driver": resume("Suresh Babu", "Driver", "Heavy vehicle driving, route knowledge, vehicle upkeep",
                     ["Driver, City Transport Ltd  2014 - 2023"], "HSC, State Board 2010",
                     "Licensed heavy vehicle driver."),
}

DOMAINS = {
    "software": dict(
        job=dict(title="Software Engineer", description="Build and maintain backend services and REST APIs in Java "
                 "and Spring Boot with SQL databases, code reviews and CI/CD.",
                 required="java, spring boot, sql, rest api, git", preferred="docker, aws, microservices",
                 years=3, edu="bachelor"),
        strong=resume("Anil Kumar", "Senior Software Engineer", "Java, Spring Boot, SQL, REST API, Git, Docker, AWS, "
                      "Microservices", ["Senior Software Engineer, Infotech Solutions Pvt Ltd  Jan 2018 - Present"],
                      "B.Tech Computer Science, JNTU 2017",
                      "Backend engineer building Java Spring Boot microservices and REST APIs for 8 years."),
        partial=resume("Bindu Rao", "Junior Developer", "Java, HTML, CSS", ["Junior Developer, WebCraft Ltd  Jun 2024 - Present"],
                       "B.Sc Computer Science, Osmania University 2023", "Fresher developer learning Java."),
        irrelevant="cook", unseen=[]),  # IT skills are already in the taxonomy
    "data_analyst": dict(
        job=dict(title="Data Analyst", description="Analyse business data, build dashboards and reports, write SQL "
                 "queries and present insights to stakeholders.",
                 required="sql, excel, power bi, data visualization, statistics", preferred="python, tableau",
                 years=2, edu="bachelor"),
        strong=resume("Kavya Menon", "Data Analyst", "SQL, Excel, Power BI, Data Visualization, Statistics, Python, Tableau",
                      ["Data Analyst, Insight Analytics Pvt Ltd  Mar 2020 - Present"], "B.Sc Statistics, Madras University 2019",
                      "Data analyst building Power BI dashboards and SQL reports."),
        partial=resume("Rohit Das", "MIS Executive", "Excel, VLOOKUP, reporting", ["MIS Executive, Retail Mart Ltd  2021 - 2024"],
                       "B.Com, Calcutta University 2020", "MIS reporting in Excel."),
        irrelevant="driver", unseen=[]),  # data visualization is now a taxonomy category
    "marketing": dict(
        job=dict(title="Digital Marketing Manager", description="Plan and run digital marketing campaigns, manage SEO "
                 "and paid search, own content strategy and report campaign performance.",
                 required="seo, google ads, content strategy, campaign management, market research",
                 preferred="social media marketing, google analytics", years=4, edu="bachelor"),
        strong=resume("Neha Kapoor", "Digital Marketing Manager", "SEO, Google Ads, Content Strategy, Campaign Management, "
                      "Market Research, Social Media Marketing, Google Analytics",
                      ["Marketing Manager, BrandWorks Pvt Ltd  Jan 2017 - Present"], "MBA Marketing, Symbiosis 2016",
                      "Marketing manager running SEO and Google Ads campaigns with a content strategy focus."),
        partial=resume("Arjun Nair", "Marketing Executive", "Social media marketing, event coordination",
                       ["Marketing Executive, EventCo Ltd  2022 - 2024"], "BBA, Bangalore University 2021",
                       "Coordinated events and social media posts."),
        irrelevant="driver", unseen=["content strategy", "campaign management", "market research"]),
    "hr": dict(
        job=dict(title="HR Executive", description="Handle end-to-end recruitment, onboarding, payroll inputs, "
                 "employee relations and HR records in the HRIS.",
                 required="recruitment, onboarding, employee relations, payroll, hris", preferred="labour law, ms office",
                 years=2, edu="bachelor"),
        strong=resume("Priya Raman", "HR Executive", "Recruitment, Onboarding, Employee Relations, Payroll, HRIS, Labour Law, MS Office",
                      ["HR Executive, PeopleFirst Services Pvt Ltd  Jun 2019 - Present"], "MBA Human Resources, Anna University 2019",
                      "HR executive handling recruitment, onboarding and payroll."),
        partial=resume("Divya S", "Office Assistant", "MS Office, recruitment support, filing",
                       ["Office Assistant, Alpha Traders  2023 - 2024"], "B.Com, Madras University 2022",
                       "Supported the recruitment team with scheduling."),
        irrelevant="cook", unseen=[]),  # HR skills are now in the taxonomy (still asserted via missing_skills)
    "finance": dict(
        job=dict(title="Accounts Executive", description="Maintain books of accounts, prepare financial reporting, "
                 "file GST returns, perform bank reconciliation using Tally and Excel.",
                 required="accounting, financial reporting, gst, tally, excel, reconciliation", preferred="tds, sap fico",
                 years=3, edu="bachelor"),
        strong=resume("Lakshmi Devi", "Accounts Executive", "Accounting, Financial Reporting, GST, Tally ERP, Excel, "
                      "Bank Reconciliation, TDS", ["Accounts Executive, Delta Traders Pvt Ltd  Apr 2016 - Present"],
                      "B.Com, Madras University 2015", "Accountant handling GST filing, reconciliation and reporting."),
        partial=resume("Manoj K", "Billing Clerk", "Excel, billing, data entry", ["Billing Clerk, QuickMart  2022 - 2024"],
                       "B.Com, Kerala University 2021", "Billing and data entry in Excel."),
        irrelevant="driver", unseen=["financial reporting", "reconciliation"]),
    "mechanical": dict(
        job=dict(title="Mechanical Design Engineer", description="Design mechanical components, prepare drawings with "
                 "GD&T, support CNC manufacturing and production.",
                 required="autocad, solidworks, gd&t, cnc, manufacturing", preferred="ansys, catia", years=3, edu="bachelor"),
        strong=resume("Karthik Raja", "Mechanical Design Engineer", "AutoCAD, SolidWorks, GD&T, CNC programming, "
                      "Manufacturing processes, ANSYS", ["Design Engineer, Precision Engineering Pvt Ltd  Jul 2017 - Present"],
                      "B.E. Mechanical Engineering, PSG College 2017", "Design engineer creating SolidWorks models and GD&T drawings."),
        partial=resume("Vimal R", "Machine Operator", "Lathe operation, AutoCAD basics", ["Machine Operator, Sri Tools  2021 - 2024"],
                       "Diploma in Mechanical Engineering 2020", "Operated lathe machines."),
        irrelevant="cook", unseen=["manufacturing"]),
    "sales": dict(
        job=dict(title="Sales Executive", description="Generate leads, manage B2B accounts, negotiate deals and achieve "
                 "monthly sales targets using CRM.",
                 required="lead generation, b2b sales, negotiation, crm, target achievement", preferred="cold calling, salesforce",
                 years=2, edu="any"),
        strong=resume("Vikram Singh", "Sales Executive", "Lead Generation, B2B Sales, Negotiation, CRM, Target Achievement, "
                      "Cold Calling, Salesforce", ["Sales Executive, TradeLink Pvt Ltd  Feb 2019 - Present"],
                      "BBA, Delhi University 2018", "B2B sales executive consistently exceeding targets."),
        partial=resume("Ajay P", "Customer Support", "Customer service, cold calling", ["Customer Support, CallCenter Co  2022 - 2024"],
                       "B.A. English, Mumbai University 2021", "Handled customer calls."),
        irrelevant="cook", unseen=["b2b sales", "negotiation", "target achievement"]),
    "uiux": dict(
        job=dict(title="UI/UX Designer", description="Design user interfaces and experiences: user research, wireframing, "
                 "prototyping in Figma and usability testing.",
                 required="figma, user research, wireframing, prototyping, usability testing", preferred="adobe xd, design systems",
                 years=2, edu="any"),
        strong=resume("Sneha Iyer", "UI/UX Designer", "Figma, User Research, Wireframing, Prototyping, Usability Testing, "
                      "Adobe XD, Design Systems", ["Product Designer, PixelCraft Studio Pvt Ltd  Aug 2019 - Present"],
                      "B.Des Interaction Design, NID 2019", "Product designer running user research and usability tests."),
        partial=resume("Rahul M", "Graphic Designer", "Photoshop, Illustrator, Figma", ["Graphic Designer, AdHouse  2022 - 2024"],
                       "B.Sc Visual Communication, Loyola College 2021", "Designed print and social creatives."),
        irrelevant="driver", unseen=["wireframing", "prototyping", "usability testing"]),
    "healthcare": dict(
        job=dict(title="Staff Nurse", description="Provide patient care in a hospital ward, maintain clinical documentation "
                 "in the EMR, follow HIPAA and infection-control protocols.",
                 required="patient care, clinical documentation, emr, bls, hipaa", preferred="medical coding, iv therapy",
                 years=2, edu="bachelor"),
        strong=resume("Mary Thomas", "Staff Nurse", "Patient Care, Clinical Documentation, EMR, BLS certified, HIPAA, IV therapy",
                      ["Staff Nurse, City Care Hospital Pvt Ltd  May 2018 - Present"], "B.Sc Nursing, Manipal University 2018",
                      "Registered nurse providing ward patient care."),
        partial=resume("Joseph K", "Hospital Receptionist", "Patient registration, appointment scheduling, EMR data entry",
                       ["Front Desk Executive, Lifeline Clinic  2022 - 2024"], "B.A. History, Kerala University 2021",
                       "Front desk executive at a clinic."),
        irrelevant="cook", unseen=["clinical documentation", "hipaa"]),
    "electrical": dict(
        job=dict(title="Electrical Engineer", description="Design control circuits and embedded systems, program PLCs and "
                 "analyse power systems.",
                 required="plc, circuit design, embedded systems, matlab, power systems", preferred="scada, autocad electrical",
                 years=3, edu="bachelor"),
        strong=resume("Ganesh Murthy", "Electrical Engineer", "PLC, Circuit Design, Embedded Systems, MATLAB, Power Systems, SCADA",
                      ["Electrical Engineer, Volt Automation Pvt Ltd  Jun 2017 - Present"],
                      "B.E. Electrical and Electronics Engineering, Anna University 2017",
                      "Electrical engineer designing PLC control panels."),
        partial=resume("Sunil T", "Electrician", "Wiring, panel maintenance, circuit design basics",
                       ["Electrician, BuildRight  2021 - 2024"], "Diploma in Electrical Engineering 2020", "Maintained wiring and panels."),
        irrelevant="cook", unseen=["circuit design", "power systems"]),
    "civil": dict(
        job=dict(title="Civil Site Engineer", description="Manage construction sites, prepare quantity surveying estimates, "
                 "perform structural analysis in STAAD.Pro and drawings in AutoCAD.",
                 required="autocad, structural analysis, quantity surveying, site management, staad.pro", preferred="primavera, revit",
                 years=3, edu="bachelor"),
        strong=resume("Harish Gowda", "Civil Site Engineer", "AutoCAD, Structural Analysis, Quantity Surveying, Site Management, "
                      "STAAD.Pro, Primavera", ["Site Engineer, Skyline Constructions Pvt Ltd  Mar 2017 - Present"],
                      "B.E. Civil Engineering, VTU 2016", "Site engineer managing residential construction projects."),
        partial=resume("Prakash N", "Site Supervisor", "Site supervision, labour management", ["Site Supervisor, HomeBuild  2021 - 2024"],
                       "Diploma in Civil Engineering 2020", "Supervised site labour."),
        irrelevant="driver", unseen=["structural analysis", "quantity surveying", "site management", "staad.pro"]),
}


def run(coro):
    return asyncio.run(coro)


class DomainAgnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="ats_domains_"))
        cls.taxonomy_keys = {skill_key(f) for c, a in load_taxonomy_index().canonicals.items() for f in (c, *a)}

    def job(self, spec):
        j = spec["job"]
        return build_job_profile(j["title"], j["description"], j["required"], j["preferred"], j["years"], j["edu"])

    def rank(self, domain, semantic):
        spec = DOMAINS[domain]
        folder = self.dir / f"{domain}_{semantic}"
        folder.mkdir(exist_ok=True)
        files = {"strong.txt": spec["strong"], "partial.txt": spec["partial"],
                 "irrelevant.txt": IRRELEVANT_POOL[spec["irrelevant"]]}
        for name, text in files.items():
            (folder / name).write_text(text, encoding="utf-8")
        with mock.patch.object(config, "PREFILTER_ENABLED", False):
            out = run(run_bulk([str(folder / n) for n in files], self.job(spec), top_n=3, semantic=semantic))
        return {Path(r.file_path).name: r for r in out.all_results}, out

    def check_domain(self, domain):
        for semantic in (False, True):
            with self.subTest(domain=domain, semantic=semantic):
                res, out = self.rank(domain, semantic)
                s, p, i = (res[n].scores.final for n in ("strong.txt", "partial.txt", "irrelevant.txt"))
                self.assertGreater(s, p, f"{domain}: strong {s} <= partial {p}")
                self.assertGreater(p, i, f"{domain}: partial {p} <= irrelevant {i}")
                self.assertEqual(res["strong.txt"].missing_skills, [], f"{domain}: strong resume missing skills")
                self.assertEqual(res["irrelevant.txt"].matched_skills, [], f"{domain}: irrelevant matched skills")
                if semantic:
                    self.assertTrue(out.summary_stats["semantic_enabled"], "semantic model must be available")
        # Job skills that the taxonomy does not contain must still be matched from text.
        spec = DOMAINS[domain]
        job = self.job(spec)
        unseen = [s for s in spec["unseen"] if skill_key(s) not in self.taxonomy_keys]
        self.assertEqual(len(unseen), len(spec["unseen"]), f"{domain}: 'unseen' skills must be outside the taxonomy")
        res, _ = self.rank(domain, False)
        for skill in unseen:
            canon = next(c for c in job.required_skills if skill_key(c) == skill_key(skill))
            self.assertIn(canon, res["strong.txt"].matched_skills, f"{domain}: '{skill}' not matched")


def _make(domain):
    return lambda self: self.check_domain(domain)


for _domain in DOMAINS:
    setattr(DomainAgnosticTests, f"test_{_domain}_strong_beats_partial_beats_irrelevant", _make(_domain))


if __name__ == "__main__":
    unittest.main()
