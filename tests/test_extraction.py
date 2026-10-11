"""Skills, experience, education and name extraction (incl. regressions from the audit)."""
from tests import helpers  # noqa: F401  (isolated data dir)

import unittest

from ats.extractor import extract_education, extract_experience, extract_name, name_from_filename
from ats.parser import normalize_text
from ats.skills import canonicalize, extract_taxonomy_skills, find_skills, normalize_skill_list


def norm(text: str) -> str:
    return normalize_text(text)[0]


class SkillMatchingTests(unittest.TestCase):
    def skills(self, text):
        return extract_taxonomy_skills(norm(text))

    def test_exact_alias_case_and_punctuation(self):
        self.assertEqual(self.skills("Python, SQL, Docker"), ["docker", "python", "sql"])
        self.assertIn("react", self.skills("Built UIs in React.js"))
        self.assertIn("react", self.skills("ReactJS"))
        self.assertIn("react", self.skills("react js"))
        self.assertIn("kubernetes", self.skills("deployed on K8s"))
        self.assertIn("nodejs", self.skills("Node.js developer"))
        self.assertIn("postgresql", self.skills("Postgres"))
        self.assertIn("javascript", self.skills("JS and TypeScript, Java"))

    def test_c_family_boundaries(self):
        self.assertEqual(self.skills("Languages: C, C++, C#, Java"), ["c", "c#", "c++", "java"])
        self.assertEqual(self.skills("ASP.NET and .NET core"), [".net", "asp.net"])
        self.assertNotIn(".net", self.skills("net profit grew 20%"))

    def test_multi_word_skills(self):
        found = self.skills("Machine Learning, Deep Learning, NLP, System Design, CI/CD")
        for s in ("machine learning", "deep learning", "nlp", "system design", "ci/cd"):
            self.assertIn(s, found)

    def test_regression_previously_unmatchable_skills(self):
        # Audit: HTML, CSS, C, Excel, ML, DL, NLP, Node.js, MATLAB, AutoCAD, Tally could never match.
        found = self.skills("Skills: HTML, CSS, C, Excel, Machine Learning, Deep Learning, NLP, Node.js, "
                            "MATLAB, AutoCAD, Tally ERP")
        for s in ("html", "css", "c", "excel", "machine learning", "deep learning", "nlp", "nodejs", "matlab",
                  "autocad", "tally"):
            self.assertIn(s, found)

    def test_regression_false_positive_words(self):
        # Audit: "go the extra mile" -> go, "R&D" -> r, "Spring semester" -> spring, "make tea" -> make
        for text in ("Ready to go the extra mile", "Head of R&D department", "Spring 2020 semester",
                     "make tea for the team", "x-ray machine operator", "excel at teamwork",
                     "washed the lab flask", "a ray of hope", "unity in the team"):
            self.assertEqual(self.skills(text), [], text)

    def test_regression_prose_and_spaced_letters(self):
        # Real resumes: "improved data organization, accessibility, and overall system" is prose, and a
        # letter-spaced name "h e m a n t h" spelled the skill "ant".
        self.assertEqual(self.skills("Greatly improved data organization, accessibility, and overall system"), [])
        self.assertEqual(self.skills("H E M A N T H R A M R"), [])
        self.assertEqual(self.skills("Technical Skills\nPython\nGo\nRust"), ["go", "python", "rust"])

    def test_ambiguous_skills_accepted_in_technical_context(self):
        self.assertEqual(self.skills("Skills: Python, Go, Rust, R"), ["go", "python", "r", "rust"])
        self.assertIn("spring", self.skills("Expert in Spring Boot microservices"))
        self.assertIn("go", self.skills("Golang backend"))
        self.assertIn("excel", self.skills("Advanced Excel and pivot tables"))
        self.assertIn("spark", self.skills("Apache Spark pipelines"))

    def test_canonical_normalization(self):
        self.assertEqual({canonicalize(x) for x in ("JS", "Javascript", "JavaScript", "javascript")}, {"javascript"})
        self.assertEqual({canonicalize(x) for x in ("React", "React.js", "ReactJS")}, {"react"})
        self.assertEqual({canonicalize(x) for x in ("Postgres", "PostgreSQL")}, {"postgresql"})
        self.assertEqual({canonicalize(x) for x in ("K8s", "Kubernetes")}, {"kubernetes"})
        self.assertEqual(normalize_skill_list(["JS", "javascript", " ", "Communication"]), ["javascript", "communication"])

    def test_unknown_job_skills_are_matched_from_text(self):
        text = norm("Strong communication skills, stakeholder management and SAP MM")
        self.assertEqual(find_skills(text, normalize_skill_list(["Communication", "stakeholder management", "sap mm",
                                                                  "negotiation"])),
                         ["communication", "stakeholder management", "sap mm"])

    def test_job_alias_matches_resume_canonical(self):
        text = norm("Experienced with JavaScript and Kubernetes")
        self.assertEqual(find_skills(text, normalize_skill_list(["js", "k8s"])), ["javascript", "kubernetes"])


class ExperienceTests(unittest.TestCase):
    def exp(self, text):
        return extract_experience(norm(text))

    def test_regression_non_experience_numbers(self):
        # Audit: these were read as years of experience.
        self.assertEqual(self.exp("Aged 25 years"), 0.0)
        self.assertEqual(self.exp("Completed a 4 year degree"), 0.0)
        self.assertEqual(self.exp("Built a 10 year forecasting model"), 0.0)
        self.assertEqual(self.exp("CGPA 7.77/10"), 0.0)
        self.assertEqual(self.exp("Phone: 9876 2020, PIN 600001"), 0.0)

    def test_degree_years_next_to_cgpa_are_not_a_job(self):
        # A two-column PDF put the degree ("2016 - 2020" + "CGPA - 7.60 | 76.0") inside the experience section;
        # removing the GPA value must not remove the word that marks those years as education.
        text = ("Work Experience\nNetwork Engineer, Demo Infra Pvt Ltd\nMarch 2021 - Aug 2021\n"
                "Configured routers and switches for branch offices\n2016 - 2020\nCGPA - 7.60 | 76.0\n"
                "System Engineer, Sample Data Solutions\nOctober 2021 - Aug 2025\n")
        self.assertAlmostEqual(self.exp(text), 4.3, delta=0.1)  # 0.4 + 3.9 years; not + 4 degree years
        # a real year-only job range without any grade nearby still counts
        self.assertAlmostEqual(self.exp("Work Experience\nSupport Engineer, Demo Pvt Ltd\n2016 - 2020\n"), 4.0, delta=0.1)

    def test_bare_course_years_near_a_degree_are_not_a_job(self):
        # degree two lines above the bare years, no CGPA: still the course, not a job
        text = ("Work Experience\nB.E. Electronics\nSample Engineering College\n2015 - 2019\n"
                "Field Engineer, Demo Telecom Pvt Ltd\nJune 2019 - June 2023\n")
        self.assertAlmostEqual(self.exp(text), 4.0, delta=0.1)
        # a job title or employer on the date line keeps a year-only range even near education words
        text = "Work Experience\nB.Com, Sample College\nAccounts Assistant, Demo Traders Pvt Ltd 2016 - 2020\n"
        self.assertAlmostEqual(self.exp(text), 4.0, delta=0.1)
        self.assertEqual(self.exp("Teacher, St. Marys School  2010 - 2015"), 5.0)

    def test_job_misplaced_under_education_heading_counts(self):
        # two-column PDFs put the job after the Education heading; title/employer is on the line above
        text = ("Education\nB.Tech Mechanical, Sample Institute of Technology, 2012\n\n"
                "Production Engineer\nDemo Motors Ltd\nJan 2013 - Dec 2018\n")
        self.assertAlmostEqual(self.exp(text), 6.0, delta=0.1)
        # the degree itself under Education is still not a job
        self.assertEqual(self.exp("Education\nM.Tech, Sample University\nJuly 2014 - June 2016\n"), 0.0)

    def test_explicit_statements(self):
        self.assertEqual(self.exp("3 years of experience in Java"), 3.0)
        self.assertEqual(self.exp("3+ years experience"), 3.0)
        self.assertEqual(self.exp("Experience: 2 yrs 6 months"), 2.5)
        self.assertEqual(self.exp("Over 10 years of experience"), 10.0)
        self.assertAlmostEqual(self.exp("8 months of experience in sales"), 0.67, places=2)

    def test_third_party_statements_ignored(self):
        self.assertEqual(self.exp("Our firm has over 40 years of experience in audit"), 0.0)
        self.assertEqual(self.exp("we bring our 25+ years of experience"), 0.0)

    def test_date_ranges(self):
        self.assertEqual(self.exp("Software Engineer, Infosys Ltd\n2019 - 2021"), 2.0)
        self.assertAlmostEqual(self.exp("Experience\nEngineer, X Pvt Ltd  Jan 2020 - Mar 2024"), 4.16, places=1)
        self.assertGreater(self.exp("Developer at Acme Ltd 2018-present"), 7.5)
        self.assertGreater(self.exp("worked as analyst from 2019 to 2023 at Acme Ltd"), 3.9)
        self.assertAlmostEqual(self.exp("Manager, ICICI Bank  jan17 to dec19"), 2.92, places=1)
        self.assertAlmostEqual(self.exp("Executive, ABC Ltd  20th june 2011 to 25th june 2014"), 3.0, places=1)

    def test_overlapping_jobs_not_double_counted(self):
        text = "Engineer, X Pvt Ltd  Jan 2018 - Dec 2021\nLead, Y Ltd  Jan 2020 - Dec 2022"
        self.assertAlmostEqual(self.exp(text), 4.92, places=1)

    def test_internships_education_projects_excluded(self):
        self.assertEqual(self.exp("Intern, ABC Ltd  Jun 2023 - Aug 2023"), 0.0)
        self.assertEqual(self.exp("Education\nB.Tech, Anna University 2016 - 2020"), 0.0)
        self.assertEqual(self.exp("Projects\nChatbot  Jan 2022 - May 2022"), 0.0)
        self.assertEqual(self.exp("Technical Skills\nSQL Server 2008-2019"), 0.0)

    def test_teaching_jobs_at_schools_count(self):
        self.assertEqual(self.exp("Teacher, St. Marys School  2010 - 2015"), 5.0)

    def test_table_layout_split_ranges(self):
        text = "Work Experience\nSupervisor, Alpha Ltd\n4yrs (2004\nto 2008)"
        self.assertEqual(self.exp(text), 4.0)

    def test_regression_real_resume_formats(self):
        # Found in randomized real-resume validation (held-out folder).
        self.assertAlmostEqual(self.exp("Professional Experience\nAcme Ltd, SQL Developer\nfeb - 2015\ndec - 2024"),
                               9.83, places=1)
        self.assertEqual(self.exp("Highly motivated SQL Server developer with 10 years in database development"), 10.0)
        self.assertEqual(self.exp("Summary\nPassed Class 10 in Mathematics; Grade: 97.2 June 2017 - March 2018"), 0.0)
        self.assertEqual(self.exp("Experience\nStudent Intern\nGlobex Software Technologies\nFebruary 24 - June 24"), 0.0)
        self.assertEqual(self.exp("Experience\nJunior Research Fellow, DRDO  2023 - 2024"), 1.0)
        self.assertEqual(self.exp("Education\nJunior Research Fellowship (JRF), DRDO. 2023-2024."), 1.0)  # trailing period

    def test_regression_professional_qualification_not_a_job(self):
        text = ("Experience\nSenior Accountant, Acme Pvt Ltd  October 2017 - October 2020\n"
                "Education\nChartered Accountant (CA) - ICAI, New Delhi | June 2015 - May 2022")
        self.assertAlmostEqual(self.exp(text), 3.0, places=1)

    def test_regression_date_columns_and_misdetected_sections(self):
        # Start and end dates alone on consecutive lines (column layouts).
        self.assertAlmostEqual(self.exp("Experience\nHR Executive, Alpha Pvt Ltd\n06/08/2021\n13/01/2023"), 1.42, places=1)
        # Employer three lines above dates that landed under a mis-detected "Skills" heading.
        self.assertAlmostEqual(self.exp("Technical Skills\nWorking as Associate at Delta Information Systems Private Limited\n"
                                        "Oracle PL/SQL developer\nDuration : Sep/2021 to Sep/2024"), 3.0, places=1)
        # Year-first months, a common misspelling, apostrophe day-month, "M/s." employers.
        self.assertAlmostEqual(self.exp("Professional Experience\nPurchase Officer, Alpha Ltd\nDuration : 2017 march - "
                                        "2020 febuary"), 2.92, places=1)
        self.assertAlmostEqual(self.exp("Academic chronicle\nM/s.Acme HR Solutions Pvt.Ltd (3PL) from "
                                        "21'sep 2015 to 05'oct 2016."), 1.08, places=1)
        # College activities with role words are not employment.
        self.assertEqual(self.exp("Positions of Responsibility\nAlumni Committee Coordinator, Regional Institute "
                                  "(Dec 2021 - May 2023)\nClass Representative, City College (Aug 2017 - Jan 2018)"), 0.0)
        # Year-only pairs on separate lines are lists of years, not employment.
        self.assertEqual(self.exp("Experience\n2018\n2020"), 0.0)

    def test_per_job_period_lines(self):
        text = "Work Experience\nAlpha Ltd, Accountant\nPeriod: 6 years\nBeta Ltd, Accountant\nPeriod: 4 years"
        self.assertEqual(self.exp(text), 10.0)


class EducationTests(unittest.TestCase):
    def edu(self, text):
        return extract_education(norm(text))

    def test_regression_ordinary_words_are_not_degrees(self):
        # Audit: "contact me" / "MS Excel" -> master, "to be" -> bachelor.
        for text in ("Contact me at 98765", "Proficient in MS Office and MS Excel", "I want to be a leader",
                     "to be computer literate", "Associate Software Engineer at TCS", "Marital Status: Bachelor",
                     "Scrum Master certified", "maintained material master data"):
            self.assertEqual(self.edu(text), "unknown", text)

    def test_bachelor_variants(self):
        for text in ("B.Tech in CSE, Anna University", "B.E. Mechanical", "BE Mechanical Engineering",
                     "Bachelor of Engineering (ECE)", "Bachelor's degree in Commerce", "BSc (Chemistry)",
                     "B.Com from Delhi University", "BCA, Madras University"):
            self.assertEqual(self.edu(text), "bachelor", text)

    def test_master_variants(self):
        for text in ("M.Tech, IIT Madras", "M.E. (Communication Systems)", "ME Structural Engineering",
                     "Master of Technology", "Master's degree", "MS in Computer Science", "MBA Finance",
                     "MCA from Anna University", "M.Sc Physics"):
            self.assertEqual(self.edu(text), "master", text)

    def test_other_levels(self):
        self.assertEqual(self.edu("Ph.D. in Chemistry"), "phd")
        self.assertEqual(self.edu("Diploma in Mechanical Engineering"), "diploma")
        self.assertEqual(self.edu("Associate degree in Nursing"), "associate")
        self.assertEqual(self.edu("HSC 2015 82%"), "school")
        self.assertEqual(self.edu("Post graduate certificate in finance, B.Com"), "bachelor")

    def test_regression_degree_named_only_by_course(self):
        self.assertEqual(self.edu("2020 - Sample Engineering College.\nCompleted Computer Science and Engineering "
                                  "with 7.10 CGPA."), "bachelor")
        self.assertEqual(self.edu("Completed Diploma in Mechanical Engineering"), "diploma")
        self.assertEqual(self.edu("Completed a course in computer science basics"), "unknown")

    def test_regression_be_with_broad_field_needs_context(self):
        self.assertEqual(self.edu("Scored 78.0% in college (BE Computer at Sample State University)"), "bachelor")
        self.assertEqual(self.edu("Eager to be computer literate and productive"), "unknown")

    def test_highest_level_wins(self):
        self.assertEqual(self.edu("Education\nMBA Finance 2012\nB.Com 2010\nHSC 2007"), "master")

    def test_bare_abbreviation_needs_education_context(self):
        self.assertEqual(self.edu("Education\nME\nAnna University"), "master")
        self.assertEqual(self.edu("call me | thanks"), "unknown")


class NameTests(unittest.TestCase):
    def name(self, text, file_name="x.pdf"):
        return extract_name(normalize_text(text)[1], file_name)

    def test_header_name(self):
        self.assertEqual(self.name("Rohan Mehra\nSoftware Engineer\nrohan@x.com"), ("Rohan Mehra", "header"))
        self.assertEqual(self.name("RESUME\nR.ANAND RAJ B.E (MECH)\nSalem"), ("R. Anand Raj", "header"))
        self.assertEqual(self.name("Priya Sharma | priya@x.com | +91 98765 43210"), ("Priya Sharma", "header"))

    def test_label_and_email(self):
        self.assertEqual(self.name("Name: Priya Sharma | Email: p@x.com"), ("Priya Sharma", "label"))
        self.assertEqual(self.name("Curriculum Vitae\nContact: 98765\npriya.sharma@gmail.com"), ("Priya Sharma", "email"))

    def test_not_headings_companies_or_contacts(self):
        self.assertEqual(self.name("SUMMARY\nexperienced engineer", "Naukri_ImranQadri[2y_6m].pdf"),
                         ("Imran Qadri", "filename"))
        self.assertEqual(self.name("Tata Consultancy Services\n9876543210", "john_doe_resume.docx"),
                         ("John Doe", "filename"))

    def test_regression_name_split_over_two_header_lines(self):
        self.assertEqual(self.name("KAVERI\nMALAR JOSEPH\nCONTACT"), ("Kaveri Malar Joseph", "header"))
        self.assertEqual(self.name("ROHAN\nSoftware Engineer\nrohan@x.com"), ("Rohan", "header"))

    def test_regression_lowercase_prose_is_not_a_name(self):
        self.assertEqual(self.name("Profile\nFinal-year student with industry experience\nbuilding\ninnovative products from",
                                   "Naukri_ArvindK[Fresher].pdf"), ("Arvind K", "filename"))

    def test_professional_prefix_removed(self):
        self.assertEqual(self.name("CA. VIKRAM RAO PATIL\nChartered Accountant"), ("Vikram Rao Patil", "header"))
        self.assertEqual(self.name("Dr. Meena Iyer\nPhysician"), ("Meena Iyer", "header"))

    def test_headings_and_addresses_are_not_names(self):
        self.assertEqual(self.name("Educational Qualification\nB.Tech", "Naukri_TarunBose[10y].docx"),
                         ("Tarun Bose", "filename"))
        self.assertEqual(self.name("Gandhi Nagar\nSalem 600001", "Sundaralingam[8yrs].docx"),
                         ("Sundaralingam", "filename"))
        self.assertEqual(self.name("Careerobjective\nTo work in", "Naukri_KiranDev[2y].pdf"), ("Kiran Dev", "filename"))

    def test_filename_fallback_cleanup(self):
        self.assertEqual(name_from_filename("Naukri_RohanMehra[6y_0m].pdf"), "Rohan Mehra")
        self.assertEqual(name_from_filename("resume_final (1).docx"), "resume_final (1).docx")


if __name__ == "__main__":
    unittest.main()
