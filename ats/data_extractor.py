import os
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
import re
import zipfile
import shutil
import fitz  # PyMuPDF
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import multiprocessing
from io import BytesIO
from typing import Optional, List, Tuple, Dict, Any

# No AI Model Needed - Pure Regex for Ultimate Speed
NLP = None
def get_nlp(): return None


# ==========================================
# PRE-COMPILED REGEX PIPELINE for HIGH SPEED
# ==========================================

FINAL_HEADERS = [
    "File Name",
    "Candidate Name",
    "Candidate Contact Number",
    "Email",
    "Total Years of Experience",
    "Skills",
    "Current Company",
    "Previous Companies",
    "Current Location",
    "Education (UG / PG Degree)",
    "Languages Known",
    "Years Worked in Current Company"
]

EMAIL_REGEX = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+')
PHONE_REGEX = re.compile(r'(?:\+?\d[\d\s\-\(\)]{8,20}\d|\(\d{3}\)\s*\d{3}-\d{4})')

EXP_REGEX = re.compile(r'(\d+(?:\.\d+)?)\s*(?:years?|yrs?)\+?\s*(?:of|hands-on|work|expertise|professional|total|relevant|industry)*\s*(?:experience|expertise|work|background)?', re.IGNORECASE)
DOB_REGEX = re.compile(r'(?:DOB|Date of[ \-]Birth)\s*[:\-]?\s*([\d]{1,2}[/\-\.][\d]{1,2}[/\-\.]([\d]{2,4}))', re.IGNORECASE)

NP_REGEX = re.compile(r'(?:Notice Period|NP)\s*[:\-]?\s*(\d{1,2}\s*(?:days?|months?|weeks?)|Immediate(?:ly)?)', re.IGNORECASE)
CTC_REGEX = re.compile(r'(?:Current CTC|CTC)\s*[:\-]?\s*([\d\.]+\s*(?:LPA|L|lakhs?|Lacs?|k|K))', re.IGNORECASE)
ECTC_REGEX = re.compile(r'(?:Expected CTC|ECTC)\s*[:\-]?\s*([\d\.]+\s*(?:LPA|L|lakhs?|Lacs?|k|K))', re.IGNORECASE)

UG_REGEX = re.compile(r'(?:^|[\s\d\-\/\(\),;:])(B\.?\s*E\.?|B\.?\s*Tech\.?|B\.?\s*Sc\.?|B\.?\s*Com\.?(?:\s*\(?H\)?(?:\s*ons)?)?|B\.?\s*B\.?\s*A\.?|B\.?\s*C\.?\s*A\.?|B\.?\s*A\.?|Bachelor(?:s|' + r"'" + r's)?\s*(?:of|in)?\s*(?:Engineering|Technology|Science|Commerce|Arts|Business\s*Administration|Business|Computer\s*Applications?|Computer\s*Science|Information\s*Technology)|M\.?B\.?B\.?S\.?|B\.?D\.?S\.?|B\.?Arch\.?|L\.?L\.?B\.?|B\.?Ed\.?|B\.?Pharm\.?|Diploma|Polytechnic|SSLC|HSC)(?=$|[\s\.\,\;\:\)\-\/])', re.IGNORECASE)
PG_REGEX = re.compile(r'(?:^|[\s\d\-\/\(\),;:])(Ph\.?\s*D\.?|M\.?\s*E\.?|M\.?\s*Tech\.?|M\.?\s*Sc\.?|M\.?\s*B\.?\s*A\.?|M\.?\s*C\.?\s*A\.?|M\.?\s*Com\.?|M\.?\s*A\.?|Master(?:s|' + r"'" + r's)?\s*(?:of|in)?\s*(?:Engineering|Technology|Science|Commerce|Arts|Business\s*Administration|Business|Computer\s*Applications?|Computer\s*Science|Information\s*Technology)|P\.?G\.?\s*D\.?M\.?|P\.?G\.?\s*Diploma|Post\s*Graduate\s*Diploma|M\.?D\.?|M\.?S\.?(?!\s*(?:Office|Excel|Word|PowerPoint|Access|Outlook|Project|Visio|SQL|Teams|Windows|Dynamics|Azure|Paint|BI|Dos)\b)|C\.?A\.?|I\.?C\.?W\.?A\.?|C\.?F\.?A\.?|M\.?Ed\.?|M\.?Pharm\.?)(?=$|[\s\.\,\;\:\)\-\/])', re.IGNORECASE)

COUNTRY_FALLBACK = {
    'india': 'India', 'usa': 'USA', 'united states': 'USA', 'uk': 'UK',
    'united kingdom': 'UK', 'canada': 'Canada', 'australia': 'Australia',
}

# Extended City and Location list for Indian & Global candidates (Cities ONLY - countries are in COUNTRY_FALLBACK)
INDIAN_CITIES = {
    'mumbai': 'Mumbai', 'new delhi': 'Delhi', 'delhi': 'Delhi', 'bangalore': 'Bangalore', 'bengaluru': 'Bangalore',
    'hyderabad': 'Hyderabad', 'secunderabad': 'Hyderabad', 'cyberabad': 'Hyderabad', 'ahmedabad': 'Ahmedabad',
    'chennai': 'Chennai', 'kolkata': 'Kolkata', 'surat': 'Surat', 'pune': 'Pune', 'jaipur': 'Jaipur', 'lucknow': 'Lucknow',
    'kanpur': 'Kanpur', 'nagpur': 'Nagpur', 'indore': 'Indore', 'thane': 'Thane', 'bhopal': 'Bhopal', 'visakhapatnam': 'Visakhapatnam',
    'vizag': 'Visakhapatnam', 'pimpri': 'Pimpri', 'chinchwad': 'Pune', 'patna': 'Patna', 'vadodara': 'Vadodara', 'baroda': 'Vadodara',
    'ghaziabad': 'Ghaziabad', 'ludhiana': 'Ludhiana', 'agra': 'Agra',
    'nashik': 'Nashik', 'faridabad': 'Faridabad', 'meerut': 'Meerut', 'rajkot': 'Rajkot', 'kalyan': 'Kalyan', 'vasai': 'Vasai',
    'varanasi': 'Varanasi', 'srinagar': 'Srinagar', 'aurangabad': 'Aurangabad', 'dhanbad': 'Dhanbad', 'amritsar': 'Amritsar',
    'navi mumbai': 'Navi Mumbai', 'allahabad': 'Allahabad', 'howrah': 'Howrah', 'gwalior': 'Gwalior', 'jabalpur': 'Jabalpur',
    'coimbatore': 'Coimbatore', 'vijayawada': 'Vijayawada', 'jodhpur': 'Jodhpur', 'madurai': 'Madurai', 'raipur': 'Raipur',
    'kota': 'Kota', 'chandigarh': 'Chandigarh', 'guwahati': 'Guwahati', 'solapur': 'Solapur', 'hubli': 'Hubli', 'bareilly': 'Bareilly',
    'moradabad': 'Moradabad', 'mysore': 'Mysore', 'mysuru': 'Mysore', 'gurgaon': 'Gurgaon', 'gurugram': 'Gurugram',
    'greater noida': 'Greater Noida', 'noida': 'Noida', 'kochi': 'Kochi', 'cochin': 'Kochi', 'ernakulam': 'Kochi',
    'thiruvananthapuram': 'Thiruvananthapuram', 'bhubaneswar': 'Bhubaneswar', 'salem': 'Salem', 'warangal': 'Warangal', 'guntur': 'Guntur',
    'bhiwandi': 'Bhiwandi', 'saharanpur': 'Saharanpur', 'amravati': 'Amravati', 'bikaner': 'Bikaner', 'nanded': 'Nanded', 'kolhapur': 'Kolhapur',
    'ajmer': 'Ajmer', 'gulbarga': 'Gulbarga', 'jamnagar': 'Jamnagar', 'ujjain': 'Ujjain', 'lonavala': 'Lonavala', 'siliguri': 'Siliguri',
    'erode': 'Erode', 'hosur': 'Hosur', 'tirupur': 'Tiruppur', 'vellore': 'Vellore', 'tuticorin': 'Thoothukudi', 'nellore': 'Nellore',
    'mangalore': 'Mangalore', 'mangaluru': 'Mangalore', 'belgaum': 'Belgaum', 'belagavi': 'Belgaum', 'davangere': 'Davangere',
    'shimoga': 'Shimoga', 'tumkur': 'Tumkur', 'raichur': 'Raichur',
    'bidar': 'Bidar', 'hassan': 'Hassan', 'bellary': 'Bellary', 'bijapur': 'Bijapur', 'trivandrum': 'Thiruvananthapuram', 'calicut': 'Kozhikode',
    'kozhikode': 'Kozhikode', 'thrissur': 'Thrissur', 'kollam': 'Kollam', 'palakkad': 'Palakkad', 'kottayam': 'Kottayam', 'kannur': 'Kannur',
    'alappuzha': 'Alappuzha', 'tirupati': 'Tirupati', 'kakinada': 'Kakinada', 'kurnool': 'Kurnool', 'kadapa': 'Kadapa', 'anantapur': 'Anantapur',
    'vizianagaram': 'Vizianagaram', 'eluru': 'Eluru', 'ongole': 'Ongole', 'nandyal': 'Nandyal', 'tenali': 'Tenali', 'proddatur': 'Proddatur',
    'adoni': 'Adoni', 'madanapalle': 'Madanapalle', 'machilipatnam': 'Machilipatnam', 'jamshedpur': 'Jamshedpur', 'ranchi': 'Ranchi', 'bokaro': 'Bokaro',
    'deoghar': 'Deoghar', 'hazaribagh': 'Hazaribagh', 'giridih': 'Giridih', 'ramgarh': 'Ramgarh', 'ujsain': 'Ujjain', 'gandhinagar': 'Gandhinagar',
    'bhavnagar': 'Bhavnagar', 'junagadh': 'Junagadh', 'anand': 'Anand', 'navsari': 'Navsari', 'morbi': 'Morbi', 'mehsana': 'Mehsana',
    'bharuch': 'Bharuch', 'vapi': 'Vapi', 'porbandar': 'Porbandar', 'ankleshwar': 'Ankleshwar', 'veraval': 'Veraval', 'bhuj': 'Bhuj',
    'rohtak': 'Rohtak', 'panipat': 'Panipat', 'karnal': 'Karnal', 'sonipat': 'Sonipat', 'hissar': 'Hissar', 'yamunanagar': 'Yamunanagar',
    'panchkula': 'Panchkula', 'ambala': 'Ambala', 'kurukshetra': 'Kurukshetra', 'sirsa': 'Sirsa', 'bahadurgarh': 'Bahadurgarh', 'rewari': 'Rewari',
    'shimla': 'Shimla', 'solan': 'Solan', 'mandi': 'Mandi', 'dharamsala': 'Dharamsala', 'una': 'Una', 'kullu': 'Kullu', 'hamirpur': 'Hamirpur',
    'jammu': 'Jammu', 'anantnag': 'Anantnag', 'baramulla': 'Baramulla', 'udhampur': 'Udhampur', 'kathua': 'Kathua',
    'pondicherry': 'Puducherry', 'puducherry': 'Puducherry', 'banaras': 'Varanasi', 'trichy': 'Tiruchirappalli', 'tiruchirappalli': 'Tiruchirappalli',
    'prayagraj': 'Allahabad', 'dharmapuri': 'Dharmapuri', 'karur': 'Karur', 'namakkal': 'Namakkal', 'thanjavur': 'Thanjavur', 'dindigul': 'Dindigul',
    'pudukkottai': 'Pudukkottai', 'nagapattinam': 'Nagapattinam', 'cuddalore': 'Cuddalore', 'kanchipuram': 'Kanchipuram', 'tiruvannamalai': 'Tiruvannamalai',
    'jalandhar': 'Jalandhar', 'patiala': 'Patiala', 'bathinda': 'Bathinda', 'mohali': 'Mohali', 'pathankot': 'Pathankot', 'hoshiarpur': 'Hoshiarpur',
    'batala': 'Batala', 'moga': 'Moga', 'abohar': 'Abohar', 'malerkotla': 'Malerkotla', 'khanna': 'Khanna', 'phagwara': 'Phagwara',
    'muzaffarpur': 'Muzaffarpur', 'gaya': 'Gaya', 'bhagalpur': 'Bhagalpur', 'bihar sharif': 'Bihar Sharif', 'darbhanga': 'Darbhanga', 'purnia': 'Purnia',
    'ara': 'Ara', 'begusarai': 'Begusarai', 'katihar': 'Katihar', 'munger': 'Munger', 'chapra': 'Chapra', 'danapur': 'Danapur',
    'durgapur': 'Durgapur', 'asansol': 'Asansol', 'kharagpur': 'Kharagpur', 'bardhaman': 'Bardhaman', 'english bazar': 'English Bazar',
    'baharampur': 'Baharampur', 'haldia': 'Haldia', 'shantipur': 'Shantipur', 'ranaghat': 'Ranaghat', 'krishnanagar': 'Krishnanagar',
    'rourkela': 'Rourkela', 'brahmapur': 'Berhampur', 'berhampur': 'Berhampur', 'sambalpur': 'Sambalpur', 'puri': 'Puri', 'balasore': 'Balasore',
    'bhadrak': 'Bhadrak', 'baripada': 'Baripada', 'jharsuguda': 'Jharsuguda', 'dhenkanal': 'Dhenkanal', 'barbil': 'Barbil',
    'bilaspur': 'Bilaspur', 'korba': 'Korba', 'bhilai': 'Bhilai', 'rajnandgaon': 'Rajnandgaon', 'jagdalpur': 'Jagdalpur', 'ambikapur': 'Ambikapur',
    'dhamtari': 'Dhamtari', 'mahansamund': 'Mahasamund', 'panaji': 'Panaji', 'vasco da gama': 'Vasco da Gama', 'margao': 'Margao', 'mapusa': 'Mapusa',
    'ponda': 'Ponda', 'bicholim': 'Bicholim', 'canacona': 'Canacona', 'san francisco': 'San Francisco', 'new york': 'New York', 'london': 'London',
    'dubai': 'Dubai', 'singapore': 'Singapore', 'seattle': 'Seattle', 'austin': 'Austin', 'boston': 'Boston', 'chicago': 'Chicago',
    'palo alto': 'Palo Alto', 'mountain view': 'Mountain View',
    'whitefield': 'Bangalore', 'electronic city': 'Bangalore', 'marathahalli': 'Bangalore', 'koramangala': 'Bangalore', 'hsr layout': 'Bangalore',
    'hinjewadi': 'Pune', 'wakad': 'Pune', 'kharadi': 'Pune', 'hadapsar': 'Pune', 'baner': 'Pune', 'viman nagar': 'Pune',
    'gachibowli': 'Hyderabad', 'madhapur': 'Hyderabad', 'kondapur': 'Hyderabad', 'kukatpally': 'Hyderabad', 'miyapur': 'Hyderabad', 'hitech city': 'Hyderabad',
    'velachery': 'Chennai', 'guindy': 'Chennai', 'porur': 'Chennai', 'sholinganallur': 'Chennai', 'chromepet': 'Chennai', 'perungudi': 'Chennai',
    'andheri': 'Mumbai', 'powai': 'Mumbai', 'bandra': 'Mumbai', 'malad': 'Mumbai', 'goregaon': 'Mumbai', 'borivali': 'Mumbai',
    'airoli': 'Navi Mumbai', 'vashi': 'Navi Mumbai', 'belapur': 'Navi Mumbai', 'kharghar': 'Navi Mumbai', 'panvel': 'Navi Mumbai', 'dombivli': 'Thane',
    'kakkanad': 'Kochi', 'edappally': 'Kochi', 'aluva': 'Kochi', 'kazhakootam': 'Thiruvananthapuram', 'saravanampatti': 'Coimbatore', 'peelamedu': 'Coimbatore',
    'tambaram': 'Chennai', 'avadi': 'Chennai', 'ambattur': 'Chennai', 'pallavaram': 'Chennai', 'tiruvallur': 'Tiruvallur', 'chengalpattu': 'Chengalpattu',
    'sriperumbudur': 'Sriperumbudur', 'gummidipoondi': 'Gummidipoondi', 'ranipet': 'Ranipet',
    'tirupattur': 'Tirupattur', 'vaniyambadi': 'Vaniyambadi', 'ambur': 'Ambur', 'gudiyatham': 'Gudiyatham', 'arcot': 'Arcot',
    'thiruvannamalai': 'Tiruvannamalai', 'arani': 'Arani', 'villupuram': 'Villupuram', 'tindivanam': 'Tindivanam', 'kallakurichi': 'Kallakurichi',
    'krishnagiri': 'Krishnagiri', 'mettur': 'Mettur', 'attur': 'Attur',
    'tiruchengode': 'Tiruchengode', 'rasipuram': 'Rasipuram', 'perundurai': 'Perundurai',
    'gobichettipalayam': 'Gobichettipalayam', 'bhavani': 'Bhavani', 'sathyamangalam': 'Sathyamangalam', 'tiruppur': 'Tiruppur',
    'udumalaipettai': 'Udumalaipettai', 'dharapuram': 'Dharapuram', 'palladam': 'Palladam', 'kangeyam': 'Kangeyam', 'nilgiris': 'Udhagamandalam',
    'ooty': 'Udhagamandalam', 'udhagamandalam': 'Udhagamandalam', 'conoor': 'Coonoor', 'pollachi': 'Pollachi',
    'mettupalayam': 'Mettupalayam', 'valparai': 'Valparai', 'sulur': 'Sulur', 'kulithalai': 'Kulithalai',
    'pallapatti': 'Pallapatti', 'ariyalur': 'Ariyalur', 'perambalur': 'Perambalur',
    'srirangam': 'Tiruchirappalli', 'thuvakudi': 'Tiruchirappalli', 'manapparai': 'Manapparai', 'karaikudi': 'Karaikudi',
    'aranthangi': 'Aranthangi', 'tanjore': 'Thanjavur', 'kumbakonam': 'Kumbakonam', 'pattukkottai': 'Pattukkottai',
    'tiruvarur': 'Tiruvarur', 'mannargudi': 'Mannargudi', 'mayiladuthurai': 'Mayiladuthurai', 'sirkazhi': 'Sirkazhi',
    'chidambaram': 'Chidambaram', 'neyveli': 'Neyveli', 'panruti': 'Panruti', 'virudhachalam': 'Virudhachalam',
    'tirumangalam': 'Thirumangalam', 'melur': 'Melur', 'usilampatti': 'Usilampatti', 'thethakudi': 'Theni',
    'theni': 'Theni', 'periyakulam': 'Periyakulam', 'bodinayakanur': 'Bodinayakanur', 'cumbum': 'Cumbum',
    'palani': 'Palani', 'kodaikanal': 'Kodaikanal', 'oddanchatram': 'Oddanchatram', 'ramanathapuram': 'Ramanathapuram', 'paramakudi': 'Paramakudi',
    'rameswaram': 'Rameswaram', 'sivagangai': 'Sivagangai', 'virudhunagar': 'Virudhunagar', 'rajapalayam': 'Rajapalayam',
    'srivilliputhur': 'Srivilliputhur', 'aruppukkottai': 'Aruppukkottai', 'sattur': 'Sattur', 'thoothukudi': 'Thoothukudi',
    'kovilpatti': 'Kovilpatti', 'tirunelveli': 'Tirunelveli', 'tenkasi': 'Tenkasi', 'sankarankovil': 'Sankarankovil', 'kadayanallur': 'Kadayanallur',
    'ambasamudram': 'Ambasamudram', 'valliyur': 'Valliyur', 'nagercoil': 'Nagercoil', 'kanyakumari': 'Kanyakumari', 'marthandam': 'Marthandam'
}

LANGUAGES = ["English", "Hindi", "Tamil", "Telugu", "Marathi", "Kannada", "Malayalam", "Gujarati", "Bengali", "Punjabi", "Urdu", "French", "German", "Spanish"]
LANG_REGEX = re.compile(r'\b(' + '|'.join(LANGUAGES) + r')\b', re.IGNORECASE)

# Skill Dictionary mapped to Canonical Names (Resolves Synonym Edge Cases)
SKILL_ALIASES = {
    "javascript": "JavaScript", "js": "JavaScript",
    "typescript": "TypeScript", "ts": "TypeScript",
    "react": "React", "react.js": "React", "reactjs": "React",
    "node.js": "Node.js", "nodejs": "Node.js", "node": "Node.js",
    "vue": "Vue.js", "vue.js": "Vue.js", "vuejs": "Vue.js",
    "angular": "Angular", "angularjs": "Angular",
    "next.js": "Next.js", "nextjs": "Next.js",
    "express": "Express", "express.js": "Express", "expressjs": "Express",
    "jquery": "jQuery",
    "c++": "C++", "cpp": "C++", 
    "c#": "C#", "c-sharp": "C#", "csharp": "C#",
    "machine learning": "Machine Learning", "ml": "Machine Learning",
    "deep learning": "Deep Learning", "dl": "Deep Learning",
    "artificial intelligence": "AI", "ai": "AI",
    "nlp": "NLP", "natural language processing": "NLP",
    "computer vision": "Computer Vision", 
    "aws": "AWS", "amazon web services": "AWS",
    "gcp": "GCP", "google cloud": "GCP",
    "azure": "Azure", "microsoft azure": "Azure",
    "sql": "SQL", "mysql": "MySQL", "postgresql": "PostgreSQL", "postgres": "PostgreSQL",
    "nosql": "NoSQL", "mongodb": "MongoDB", "mongo": "MongoDB", "sqlite": "SQLite", "redis": "Redis",
    "docker": "Docker", 
    "kubernetes": "Kubernetes", "k8s": "Kubernetes",
    "ci/cd": "CI/CD", "cicd": "CI/CD", "jenkins": "Jenkins", "terraform": "Terraform",
    "git": "Git", "github": "GitHub", "gitlab": "GitLab",
    "linux": "Linux", "unix": "Unix", "bash": "Bash",
    "python": "Python", 
    "pandas": "Pandas", "numpy": "NumPy", "scikit-learn": "Scikit-Learn", "sklearn": "Scikit-Learn",
    "pytorch": "PyTorch", "tensorflow": "TensorFlow", "keras": "Keras", "matplotlib": "Matplotlib",
    "java": "Java", "spring": "Spring", "spring boot": "Spring Boot",
    "ruby": "Ruby", "ruby on rails": "Ruby on Rails", "rails": "Ruby on Rails",
    "php": "PHP", "laravel": "Laravel",
    "go": "Go", "golang": "Go",
    "rust": "Rust", "swift": "Swift", "kotlin": "Kotlin", "dart": "Dart",
    "html": "HTML", "css": "CSS", "bootstrap": "Bootstrap", "tailwind": "Tailwind", "terraform": "Terraform",
    "sap fico": "SAP FICO", "tally": "Tally", "tally erp": "Tally", "gst": "GST", "taxation": "Taxation", 
    "auditing": "Auditing", "financial analysis": "Financial Analysis", "financial modeling": "Financial Modeling",
    "banking": "Banking", "accounting": "Accounting", "ms excel": "MS Excel", "excel": "MS Excel", "advanced excel": "Advanced Excel",
    "autocad": "AutoCAD", "solidworks": "SolidWorks", "catia": "CATIA", "pro-e": "Pro-E", "proe": "Pro-E", 
    "gd&t": "GD&T", "gdt": "GD&T", "lean manufacturing": "Lean Manufacturing", "six sigma": "Six Sigma",
    "staad pro": "STAAD Pro", "revit": "Revit", "primavera p6": "Primavera P6", "primavera": "Primavera P6", 
    "ms project": "MS Project", "estimation": "Estimation", "quantity surveying": "Quantity Surveying", "is codes": "IS Codes",
    "brand management": "Brand Management", "digital marketing": "Digital Marketing", "market research": "Market Research", 
    "trade marketing": "Trade Marketing", "btl": "BTL Activation", "ms powerpoint": "MS PowerPoint", "google analytics": "Google Analytics",
    "photoshop": "Adobe Photoshop", "illustrator": "Adobe Illustrator", "indesign": "Adobe InDesign", "figma": "Figma", 
    "coreldraw": "CorelDRAW", "premiere pro": "Adobe Premiere Pro", "ui/ux": "UI/UX", "social media creatives": "Social Media Creatives",
    "outbound sales": "Sales", "insurance": "Insurance Products", "lead conversion": "Lead Conversion", "leadsquared": "LeadSquared",
    "negotiation": "Negotiation", "siem": "SIEM", "splunk": "Splunk", "qradar": "QRadar", "threat hunting": "Threat Hunting",
    "incident response": "Incident Response", "vulnerability assessment": "Vulnerability Assessment", "ceh": "CEH Certified",
    "security+": "Security+", "owasp": "OWASP Top 10", "firewall": "Firewall Management", "unity": "Unity 3D",
    "c# scripting": "C# Scripting", "shader graph": "Shader Graph", "photon": "Photon Networking", "agile": "Agile", "scrum": "Scrum",
    "recruitment": "Recruitment & Selection", "payroll": "Payroll Processing", "hrms": "HRMS", "darwinbox": "Darwinbox",
    "onboarding": "Onboarding", "statutory compliance": "Statutory Compliance",
    "staff management": "Staff Management", "operations management": "Operations Management",
    "inventory control": "Inventory Control", "inventory management": "Inventory Management",
    "process improvement": "Process Improvement", "strategic procurement": "Strategic Procurement",
    "cost optimization": "Cost Optimization", "supplier relationship": "Supplier Relationship",
    "supply chain management": "Supply Chain Management", "scm": "Supply Chain Management",
    "budgeting": "Budgeting", "risk management": "Risk Management", "erp systems": "ERP Systems",
    "erp": "ERP Systems", "leadership": "Leadership", "team development": "Team Development",
    "procurement forecasting": "Procurement Forecasting", "procurement": "Procurement",
    "team management": "Team Management", "creativity": "Creativity", "problem solving": "Problem Solving",
    "computer skills": "Computer Skills", "assisting the principal": "Principal Assisting",
    "office coordination": "Office Coordination", "principal assisting": "Principal Assisting",
    "strategic planning": "Strategic Planning",
    "employee engagement": "Employee Engagement", "sap hr": "SAP HR",
    "sap basis": "SAP Basis", "manufacturing": "Manufacturing", "apqp": "APQP", 
    "root cause analysis": "Root Cause Analysis", "quality control": "Quality Control",
    "mechanical engineering": "Mechanical Engineering", "iso 9001": "ISO 9001",
    "ppap": "PPAP", "quality management": "Quality Management",
    "circuit design": "Circuit Design", "pcb design": "PCB Design",
    "schematic design": "Schematic Design", "emi-emc": "EMI-EMC Testing",
    "hardware testing": "Hardware Testing", "footprint checking": "Footprint Checking",
    "embedded systems": "Embedded Systems", "fpga": "FPGA",
    "arria 10": "Intel Arria 10", "ti sitara": "Ti Sitara",
    "i.mx 8m": "i.MX 8M", "microprocessor": "Microprocessor",
    "bms": "BMS", "power management": "Power Management",
    "mipi": "MIPI", "csi": "CSI", "dsi": "DSI",
    "ddr4": "DDR4", "emmc": "eMMC", "rgmii": "RGMII",
    "spi": "SPI", "i2c": "I2C", "sdio": "SDIO", "sai": "SAI",
    "cadence": "Cadence", "orcad": "Orcad", "kicad": "KiCad",
    "ltspice": "LTSpice", "tiwebench": "TiWebench",
    "harness design": "Harness Design", "visio": "Microsoft Visio",
    "creo": "PTC Creo", "altium": "Altium Designer",
    "plm tools": "PLM Tools", "odoo": "Odoo",
    "bms troubleshooting": "BMS Troubleshooting",
    "data analysis": "Data Analysis",
    "circuit simulation": "Circuit Simulation",
    "sap is retail": "SAP IS Retail", "idoc processing": "Idoc Processing",
    "retail process expertise": "Retail Process Expertise",
    "promotion management": "Promotion Management",
    "pos integration": "POS Integration", "e-commerce integration": "e-Commerce Integration",
    "documentation skills": "Documentation Skills",
    "angular.js": "Angular", "node.js": "Node.js",
    ".net framework": ".NET Framework", "asp.net": "ASP.NET",
    "webapi": "WebAPI", "jira": "Jira"
}
STRICT_C_SKILLS = {"C++": "C++", "C#": "C#"}
SORTED_ALIASES = sorted(list(SKILL_ALIASES.keys()), key=len, reverse=True)
SKILL_PATTERN = re.compile(r'\b(' + '|'.join(re.escape(s) for s in SORTED_ALIASES) + r')\b', re.IGNORECASE)
STRICT_PATTERN = re.compile(r'\b(C\+\+|C#)\b')

LANG_REGEX = re.compile(r'\b(English|Hindi|Marathi|Gujarati|Tamil|Telugu|Kannada|Malayalam|Bengali|Punjabi|Odia|Urdu|Sanskrit|Spanish|French|German|Japanese|Mandarin)\b', re.IGNORECASE)

def clean_company(text):
    if not text: return ""
    txt = text.strip()
    
    # 0. Strip section headers and bullet points
    txt = re.sub(r'^(EXPERIENCE|WORK EXPERIENCE|PROFESSIONAL EXPERIENCE|EMPLOYMENT|03 EXPERIENCE|PROJECT|PROJECTS)\s*', '', txt, flags=re.IGNORECASE)
    txt = re.sub(r'^[\u2022\u00b7\*\-\d\.]+\s*', '', txt)
    
    # 1. Strip common prefixes
    prefixes = ['Role:', 'Position:', 'Job Title:', 'Designation:', 'Employment:', 'Organization:', 'Company:', 'Job:', 'Title:']
    for p in prefixes:
        if txt.lower().startswith(p.lower()):
            txt = txt[len(p):].strip()
            
    # 2. Split by common role separators and take the part that looks like a company
    for sep in [':', '-', '|', '–', '—']:
        if sep in txt:
            parts = txt.split(sep)
            best_part = parts[0]
            max_s = -100
            for p in parts:
                s = score_candidate(p.strip())
                if s > max_s:
                    max_s = s
                    best_part = p
            txt = best_part.strip()
            
    # 3. Clean common noise words and locations
    txt = txt.strip()
    # Remove trailing city/location (e.g. "Google, Mountain View" or "Google Pune")
    for city in list(INDIAN_CITIES.values()) + ['Pune', 'Ambur', 'Ambur City']:
        txt = re.sub(r'[,|\-]?\s*' + re.escape(city) + r'\b.*$', '', txt, flags=re.IGNORECASE)
    
    # Remove parenthetical noise like "(Phoenix Group)" or "(SOM)"
    txt = re.sub(r'\s*\(.*?\)\s*$', '', txt)
    
    return txt.strip()

def score_candidate(text):
    if not text: return -100
    txt_lower = text.lower().strip()
    words = text.split()
    
    if len(words) == 0: return -100
    
    # Heavily penalize common technical skills or junk words
    if txt_lower in IGNORE_ORGS or txt_lower in SKILL_ALIASES:
        return -300
        
    score = 0
    # Bonus for common company indicators
    if re.search(COMPANY_KW, text): score += 50
    if any(p.lower() in txt_lower for p in PREDEFINED_ORGS): score += 60
    
    # Bonus for Multi-word Capitalized strings (Proper Nouns)
    if len(words) >= 2 and all(w[0].isupper() for w in words if w[0].isalpha()):
        score += 30
    
    # Penalty for role words if they are the ONLY words
    if len(words) <= 3 and any(r.lower() in txt_lower for r in ROLE_WORDS):
        score -= 40
        
    # Penalty for section headers
    if txt_lower in ['experience', 'work experience', 'education', 'skills', 'summary', 'profile']:
        score -= 150
        
    # Length calibration
    if 2 <= len(words) <= 5: score += 20
    elif len(words) <= 6: score += 5
    elif len(words) > 8: score -= 30
    
    return score


# Organizations and Descriptions to ignore during Company extraction
IGNORE_ORGS = {
    'pvt ltd', 'private limited', 'inc', 'llc', 'corporation',
    'express', 'react', 'python', 'java', 'aws', 'amazon web services',
    'data scientist', 'software engineer', 'developer', 'account executive', 'lead', 'marketing',
    'boston', 'miami', 'columbia', 'duke', 'university of miami', 'boston university', 'duke university',
    'experince', 'experience', 'education', 'skills', 'profile', 'summary', 'about me', 'contact',
    'project', 'responsibilities', 'achievements', 'internship', 'trainee', 'student', 'analyst', 'manager',
    'senior', 'junior', 'associate', 'lead', 'chief', 'head', 'coordinator', 'specialist', 'assistant', 'officer',
    'scientist', 'engineer', 'developer', 'consultant', 'accountant', 'executive', 'nih', 'grant', 'funding', 'investigator',
    'full-time', 'part-time', 'contract', 'freelance', 'temporary', 'intern', 'trainee', 'apprentice', 'technical', 'technology',
    'architect', 'specialist', 'expert', 'lead', 'coordinator', 'officer', 'representative', 'support', 'coordinator',
    'supervisor', 'relationship', 'executive', 'officer', 'manager', 'coordinator', 'specialist', 'assistant',
    # Added "Action Verbs" to prevent job descriptions from being extracted as companies
    'led', 'built', 'developed', 'managed', 'created', 'designed', 'architected', 'improved', 'implemented', 'performed',
    'researched', 'analyzed', 'conducted', 'monitored', 'maintained', 'supported', 'assisted', 'coordinated', 'mentored',
    'increased', 'reduced', 'saved', 'optimized', 'scaled', 'modeled', 'deployed', 'migrated', 'automated', 'integrated',
    'supervised', 'coordinated', 'processed', 'evaluated', 'achieved', 'negotiated', 'handled', 'prepared',
    'place', 'date', 'declaration', 'signature', 'statement', 'hereby', 'truthful', 'correct', 'best of my knowledge',
    'b.com', 'bcom', 'm.sc', 'msc', 'b.a', 'ba', 'm.a', 'ma', 'b.e', 'm.e', 'b.tech', 'm.tech', 'mba', 'mca', 'bca',
    'implemented', 'attended', 'handled', 'prepared', 'assisted', 'reconciliation', 'management', 'services', 'coloring', 'styling', 'attending', 'executing',
    'percentage', 'cgpa', 'grade', 'score', 'marks', 'obtained', 'aggregate', 'payroll', 'consultant', 'sap',
    'mysql', 'sql', 'postgresql', 'angular', 'node', 'node.js', 'asp.net', 'webapi', 'jira', 'git', 'github', 'html', 'css', 'python', 'java'
}

# High-Confidence Predefined Organization List (Ensures major names are never missed)
PREDEFINED_ORGS = {
    'Reliance', 'TCS', 'Infosys', 'Wipro', 'HCL', 'Accenture', 'Cognizant', 'Capgemini', 'IBM', 'Google', 'Microsoft', 
    'Amazon', 'Slice', 'Unacademy', 'Byjus', 'Airtel', 'Jio', 'Deloitte', 'PWC', 'EY', 'KPMG', 'HDFC', 'ICICI', 'SBI', 
    'Axis', 'L&T', 'Godrej', 'Tata', 'Tech Mahindra', 'Cognizant', 'Mindtree', 'Oracle', 'Cisco', 'Adobe', 'Swiggy', 
    'Zomato', 'Ola', 'Uber', 'Paytm', 'PhonePe', 'Flipkart', 'Snapdeal', 'Myntra', 'Freshworks', 'Zoho', 'Postman',
    'Tech Mahindra', 'Vistara', 'IndiGo', 'Mahindra', 'Bajaj', 'Adani', 'Vedanta', 'Asian Paints', 'Titan'
}

ROLE_WORDS = {"Manager", "Scientist", "Developer", "Analyst", "Engineer", "Executive", "Associate", "Lead", "Senior", "Junior", "Coordinator", "Nurse", "RN", "Consultant", "Specialist", "Director", "Product", "Early", "Stage", "GTM", "Gtm", "Technical", "Architect", "Full-time", "Part-time", "Contractor", "Freelancer", "Supervisor", "Relationship", "Officer", "Staff", "Clerk", "Operator", "Technician", "Representative", "Head", "Principal", "Intern", "Trainee", "Retail"}

COMPANY_KW = r'\b(?:Pvt|Ltd|Limited|Corp|Inc|Company|Agency|Solutions|Firm|Bank|'\
             r'Systems|Research|Financial|Hospital|Medical|Clinic|Center|Health|'\
             r'Services|Tech|Software|Technologies|Consulting|Consultancy|'\
             r'Industries|Group|Associates|Enterprises|International|Global|'\
             r'Manufacturing|Analytics|Digital|Networks|Media|Studios|'\
             r'School|Academy|College|University|Institute|Foundation|Trust)\b'

IGNORE_TITLES = {
    'B.A.', 'B.S.', 'B.B.A.', 'M.A.', 'M.S.', 'MBA', 'B.Tech', 'M.Tech', 'Engineering', 'Economics', 
    'Marketing', 'Design', 'Science', 'Arts', 'Fellow', 'Postdoctoral', 'Computer Science', 'UC Berkeley', 'NYU'
}

# ==========================================
# MODULE-LEVEL PRE-COMPILED PATTERNS
# ==========================================
CITY_REGEX = re.compile(
    r'\b(' + '|'.join(re.escape(k) for k in sorted(INDIAN_CITIES.keys(), key=len, reverse=True)) + r')\b',
    re.IGNORECASE
)

ACTION_VERBS = {
    'led', 'built', 'developed', 'managed', 'created', 'designed', 'architected',
    'improved', 'implemented', 'performed', 'researched', 'analyzed', 'conducted',
    'monitored', 'maintained', 'supported', 'assisted', 'coordinated', 'mentored',
    'increased', 'reduced', 'saved', 'optimized', 'scaled', 'modeled', 'deployed',
    'migrated', 'automated', 'integrated', 'achieved', 'raised', 'prepared',
    'executed', 'grew', 'supervised', 'implement', 'handling', 'preparation',
    'assisting', 'attending', 'managing', 'published', 'trained', 'conducted',
    'specialized', 'consistently', 'provided', 'reported', 'collaborated',
    'spearheaded', 'oversaw', 'coordinated', 'processed', 'evaluated'
}
ACTION_VERBS_RE = re.compile(
    r'\b(?:' + '|'.join(re.escape(v) for v in sorted(ACTION_VERBS, key=len, reverse=True)) + r')\b',
    re.IGNORECASE
)

PROJECT_WORDS = {'project', 'intern', 'training', 'student', 'academic', 'thesis', 'curriculum', 'education', 'university', 'college'}
PROJECT_WORDS_RE = re.compile(
    r'\b(?:' + '|'.join(re.escape(w) for w in sorted(PROJECT_WORDS, key=len, reverse=True)) + r')\b',
    re.IGNORECASE
)

SKILL_SIGNALS = {'excel', 'tally', 'python', 'java', 'sql', 'aws', 'sap', 'powerpoint', 'photoshop', 'autocad', 'figma', 'r', 'ms', 'technical', 'data', 'architecture', 'architect'}
SKILL_SIGNALS_RE = re.compile(
    r'\b(?:' + '|'.join(re.escape(s) for s in sorted(SKILL_SIGNALS, key=len, reverse=True)) + r')\b',
    re.IGNORECASE
)

ROLE_WORDS_RE = re.compile(
    r'\b(?:' + '|'.join(re.escape(rw) for rw in sorted(ROLE_WORDS, key=len, reverse=True)) + r')\b',
    re.IGNORECASE
)

PREDEFINED_ORGS_RE = re.compile(
    r'\b(?:' + '|'.join(re.escape(po) for po in sorted(PREDEFINED_ORGS, key=len, reverse=True)) + r')\b',
    re.IGNORECASE
)

CITY_SUFFIX_RE = re.compile(
    r'\b(?:' + '|'.join(re.escape(city.title()) for city in list(INDIAN_CITIES.keys())[:60]) + r')\s*$',
    re.IGNORECASE
)

DATE_MONTHS_RE = re.compile(r'\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b', re.IGNORECASE)
DATE_CONNECTORS_RE = re.compile(r'\b(?:to|from|till|since|Present|Current|Till Date|Today)\b', re.IGNORECASE)
DATE_ONLY_BLOCK_RE = re.compile(r'^(?:(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)|to|from|till|since|Present|Current|Till Date|Today|\s+)+$', re.IGNORECASE)
YEARS_RE = re.compile(r'\b(?:19|20)\d{2}\b')

STRICT_DATE_RE = re.compile(r'\b(20\d{2}|199\d|(?<=\s)\'(2[0-5])|(?<=\s)(2[0-5])|Present|Current|Till|Today)\b', re.IGNORECASE)

DATE_RANGE_RE = re.compile(
    r'(?:(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)[\s,\']*(\d{2,4})?|(\d{4}))\s*(?:-|\u2013|\u2014|to)\s*'
    r'(?:(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)[\s,\']*(\d{2,4})?|(\d{4}|Present|Current|Till Date|Today))',
    re.IGNORECASE
)
DATE_YEAR_ONLY_RE = re.compile(r'\b(\d{4}|Present|Current)\s*[-–]\s*(\d{4}|Present|Current)\b', re.IGNORECASE)

EDU_JUNK = {
    'bcom', 'msc', 'mba', 'btech', 'mtech', 'bca', 'mca', 'ba', 'ma', 'bsc',
    'msc', 'phd', 'diploma', 'account', 'percentage', 'cgpa', 'grade', 'score',
    'marks', 'aggregate', 'gpa', 'class'
}


def unzip_file(zip_path, extract_to=None):
    if extract_to is None:
        import tempfile
        extract_to = tempfile.mkdtemp(prefix="ats_extract_")
    else:
        if os.path.exists(extract_to):
            shutil.rmtree(extract_to)
        os.makedirs(extract_to, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_to)
    except zipfile.BadZipFile:
        return None
    return extract_to


NAME_REGEX = re.compile(r'^[A-Z][A-Za-z.]*(?:\s+[A-Z][A-Za-z.]*){0,4}$')

def sanitize_filename(filename):
    raw_fn = re.sub(r'\b([SDWC])/O\b', r'\1_O', str(filename or ""), flags=re.IGNORECASE)
    # Always take pure basename so directory paths or zip names never interfere
    filename = os.path.basename(raw_fn.replace("\\", "/"))
    name = os.path.splitext(filename)[0]

    # Strip leading Naukri_ / India_ / Resume_ / CV_ prefixes and leading numeric IDs / phone prefixes
    name = re.sub(r'^(?:naukri[\s_\-]+|india[\s_\-]+|resume[\s_\-]+|cv[\s_\-]+|updated[\s_\-]+|profile[\s_\-]+|\d+[\s_\-]+)+', '', name, flags=re.IGNORECASE)

    # Strip father/husband name suffixes ("S/O ...", "S O ...", "S_O ...", "D/O ...", "W/O ...")
    name = re.sub(r'\b(?:S\s*[/_\-\s]\s*O|D\s*[/_\-\s]\s*O|W\s*[/_\-\s]\s*O|C\s*[/_\-\s]\s*O)\b.*$', '', name, flags=re.IGNORECASE).strip()

    # Stop before brackets, parens, digits, or experience/fresher/role markers
    name = re.split(
        r'\[|\(|\d|\b(?:fresher|dv_trained|resume|cv|curriculum|curriculam|vitae|biodata|profile|updated|final|'
        r'senior|junior|sr|jr|executive|manager|engineer|developer|analyst|consultant|associate|specialist|'
        r'officer|coordinator|assistant|recruiter|accountant|designer|architect|intern|trainee)\b',
        name,
        flags=re.IGNORECASE,
    )[0].strip()

    # Split camelCase or PascalCase (e.g. AbhishekJagtap -> Abhishek Jagtap), preserving initials like A.S
    name = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)

    # Replace underscores and dashes with spaces
    name = name.replace('_', ' ').replace('-', ' ')

    res = ' '.join(name.split()).title()
    res = re.sub(r'\b(Naukri|Yrs?|Exp|Resume|Cv|Final|Updated|V\d+|India|Profile|Biodata|Doc|Docx|Pdf)\b', '', res, flags=re.IGNORECASE).strip()

    # Strip trailing Indian city/state names from filename-derived candidate names
    _loc_tail = set(INDIAN_CITIES.keys()) | {
        "kerala", "tamil", "nadu", "tamilnadu", "telangana", "karnataka", "maharashtra",
        "andhra", "pradesh", "gujarat", "rajasthan", "punjab", "haryana", "up", "mp",
        "saravanampatti", "chettipalayam", "palayam", "india",
    }
    wlist = res.split()
    while len(wlist) > 1 and wlist[-1].lower().strip(".,") in _loc_tail:
        wlist.pop()
    res = ' '.join(wlist)

    if res.lower() in ['pdf', 'docx', 'doc', 'resume', 'cv', 'naukri']:
        return ""
    return res

def get_files(folder):
    files = []
    valid_extensions = (".pdf", ".docx", ".doc", ".txt")
    for root, _, filenames in os.walk(folder):
        for f in filenames:
            if f.lower().endswith(valid_extensions) and not f.startswith("~$"):
                files.append(os.path.join(root, f))
    return files


def _extract_page_column_aware(page) -> str:
    """Reconstruct PDF page text in reading order, separating 2-column sidebars and tables."""
    try:
        rect = page.rect
        pw = rect.width or 600.0
        ph = rect.height or 800.0
        raw_blocks = page.get_text("blocks")
    except Exception:
        return page.get_text()

    text_blocks = []
    for b in raw_blocks:
        if len(b) >= 5 and (len(b) < 7 or b[6] == 0):
            txt = (b[4] or "").strip()
            if txt:
                text_blocks.append((float(b[0]), float(b[1]), float(b[2]), float(b[3]), txt))

    if not text_blocks:
        return ""

    # Detect if page has a genuine two-column layout
    header_blocks = []
    left_blocks = []
    right_blocks = []
    spanning_blocks = []

    mid_x = pw * 0.48
    for x0, y0, x1, y1, txt in text_blocks:
        bw = x1 - x0
        if y0 < ph * 0.16 and bw > pw * 0.45:
            header_blocks.append((y0, x0, txt))
        elif x1 <= pw * 0.54 and bw < pw * 0.52:
            left_blocks.append((y0, x0, txt))
        elif x0 >= pw * 0.36 and bw < pw * 0.62:
            right_blocks.append((y0, x0, txt))
        else:
            spanning_blocks.append((y0, x0, txt))

    if len(left_blocks) >= 2 and len(right_blocks) >= 2 and len(spanning_blocks) <= max(3, (len(left_blocks) + len(right_blocks)) // 3):
        header_blocks.sort(key=lambda t: (round(t[0] / 8), t[1]))
        left_blocks.sort(key=lambda t: (round(t[0] / 8), t[1]))
        right_blocks.sort(key=lambda t: (round(t[0] / 8), t[1]))
        spanning_blocks.sort(key=lambda t: (round(t[0] / 8), t[1]))
        ordered = [t[2] for t in header_blocks] + [t[2] for t in left_blocks] + [t[2] for t in right_blocks] + [t[2] for t in spanning_blocks]
        return "\n".join(ordered)

    # Single-column layout: sort top-to-bottom, left-to-right
    text_blocks.sort(key=lambda b: (round(b[1] / 6), b[0]))
    return "\n".join(b[4] for b in text_blocks)


def _extract_page_tables(page) -> List[str]:
    """Fast coordinate-based table row reconstruction using word bounding boxes (<1ms per page)."""
    table_lines: List[str] = []
    try:
        words = page.get_text("words")
        if not words:
            return table_lines
        # Sort top-to-bottom (quantized by 4pt bands), then left-to-right
        words_sorted = sorted(words, key=lambda w: (round(float(w[1]) / 4.0), float(w[0])))
        rows: List[List[Any]] = []
        for w in words_sorted:
            if not rows or abs(float(w[1]) - float(rows[-1][0][1])) > 4.5:
                rows.append([w])
            else:
                rows[-1].append(w)

        for r in rows:
            r.sort(key=lambda w: float(w[0]))
            cells: List[str] = []
            cur_tokens: List[str] = []
            prev_x1: Optional[float] = None
            for w in r:
                x0, _, x1, _, token = float(w[0]), float(w[1]), float(w[2]), float(w[3]), str(w[4]).strip()
                if not token:
                    continue
                if prev_x1 is not None and (x0 - prev_x1) >= 16.0:
                    cell_txt = " ".join(cur_tokens).strip()
                    if cell_txt:
                        cells.append(cell_txt)
                    cur_tokens = [token]
                else:
                    cur_tokens.append(token)
                prev_x1 = max(prev_x1 or x1, x1)
            if cur_tokens:
                cell_txt = " ".join(cur_tokens).strip()
                if cell_txt:
                    cells.append(cell_txt)
            if len(cells) >= 2:
                table_lines.append(" | ".join(cells))
    except Exception:
        pass
    return table_lines


def extract_pdf_data(file_path, allow_ocr: bool = True):
    """Deep multi-engine PDF extraction:
    1. Column-aware PyMuPDF block reconstruction + fast word-coordinate table row reconstruction + mailto link recovery
    2. pdfminer.six LAParams fallback when PyMuPDF text is sparse (<120 chars)
    3. RapidOCR fallback on genuinely scanned/image PDFs within the batch time budget
    """
    text = ""
    fitz_chunks: List[str] = []
    table_rows: List[str] = []
    hidden_emails: List[str] = []

    try:
        try:
            fitz.TOOLS.mupdf_display_errors(False)
        except Exception:
            pass
        doc = fitz.open(file_path)
        try:
            for pidx, page in enumerate(doc):
                col_text = _extract_page_column_aware(page)
                raw_text = page.get_text() or ""
                page_txt = col_text if len(col_text) >= len(raw_text) * 0.85 else raw_text
                fitz_chunks.append(page_txt)

                if pidx < 3:
                    table_rows.extend(_extract_page_tables(page))

                for link in page.get_links():
                    uri = link.get("uri", "")
                    if uri and uri.lower().startswith("mailto:"):
                        hidden_emails.append(uri[7:].strip())

            fitz_text = "\n".join(fitz_chunks)
            fitz_clean = re.sub(r"\(cid:\d+\)|\ufffd", " ", fitz_text)
            fitz_alnum = len(re.sub(r"\W", "", fitz_clean))

            if fitz_alnum < 120:
                try:
                    from .parser import parse_pdf
                    pm_txt = parse_pdf(file_path, max_pages=1) or ""
                    if len(re.sub(r"\W", "", pm_txt)) > fitz_alnum:
                        fitz_text = pm_txt + "\n" + fitz_text
                        fitz_alnum = len(re.sub(r"\W", "", fitz_text))
                except Exception:
                    pass

            existing_lines_norm = {re.sub(r"\s+", " ", ln.strip().lower()) for ln in fitz_text.splitlines() if ln.strip()}
            supplemental_lines = []
            for tr in table_rows:
                norm_tr = re.sub(r"\s+", " ", tr.lower())
                if norm_tr not in existing_lines_norm:
                    existing_lines_norm.add(norm_tr)
                    supplemental_lines.append(tr)

            combined_text = fitz_text
            if supplemental_lines:
                combined_text = fitz_text + "\n" + "\n".join(supplemental_lines)

            # Engine 2: RapidOCR on Page 0 when the PDF is a scanned image (<90 alnum chars) and within OCR budget
            if allow_ocr and fitz_alnum < 90 and len(doc) > 0:
                try:
                    from .parser import ocr_available, _ocr_pil_image
                    from PIL import Image

                    if ocr_available():
                        p0 = doc[0]
                        r0 = p0.rect
                        clip = fitz.Rect(0, 0, r0.width, r0.height * 0.70)
                        pix = p0.get_pixmap(dpi=135, clip=clip, alpha=False)
                        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                        t = _ocr_pil_image(img)
                        if t and t.strip():
                            combined_text = t.strip() + "\n" + combined_text
                except Exception:
                    pass

            text = combined_text
        finally:
            doc.close()
    except Exception:
        try:
            from .parser import parse_pdf
            text = parse_pdf(file_path, max_pages=2) or ""
        except Exception:
            pass
    return text, hidden_emails


def extract_docx_data(file_path, allow_ocr: bool = True):
    text = ""
    hidden_emails = []
    try:
        from .parser import parse_docx, _docx_xml_text, ocr_available, ocr_docx_images

        text = parse_docx(file_path) or ""
        try:
            xml_text = _docx_xml_text(file_path) or ""
            existing_norm = {re.sub(r"\s+", " ", ln.strip().lower()) for ln in text.splitlines() if ln.strip()}
            extra_xml = []
            for ln in xml_text.splitlines():
                s_ln = ln.strip()
                norm_ln = re.sub(r"\s+", " ", s_ln.lower())
                if len(s_ln) > 2 and norm_ln not in existing_norm:
                    existing_norm.add(norm_ln)
                    extra_xml.append(s_ln)
            if extra_xml:
                text = text + "\n" + "\n".join(extra_xml)
        except Exception:
            pass
    except Exception:
        pass
    try:
        with zipfile.ZipFile(file_path) as zf:
            for name in zf.namelist():
                if name.endswith(".rels"):
                    rel_xml = zf.read(name).decode("utf-8", errors="ignore")
                    for m in re.finditer(r'mailto:([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)', rel_xml, re.IGNORECASE):
                        hidden_emails.append(m.group(1).strip())
    except Exception:
        pass
    if allow_ocr:
        try:
            from .parser import ocr_available, ocr_docx_images

            if ocr_available() and len(re.sub(r"\W", "", text)) < 100:
                ocr_text = ocr_docx_images(file_path)
                if ocr_text and ocr_text.strip():
                    text = ocr_text + "\n" + text
        except Exception:
            pass
    return text, hidden_emails


_MOBILE_IN_RE = re.compile(r'(?<!\d)(?:\+?91[\-\s\)]*|0)?([6-9]\d{4}[\-\s]?\d{5})(?!\d)')
_LINE_PHONE_RE = re.compile(r'(?:\+?\d[\d \t\-\(\)\.]{8,18}\d|\(\d{3}\)[ \t]*\d{3}[\- ]\d{4})')


def _extract_clean_phones(text: str) -> str:
    all_phones: List[str] = []
    seen_keys = set()

    for line in text.splitlines():
        # Skip lines that are clearly education / CGPA / percentage table rows without contact context
        for m in _LINE_PHONE_RE.finditer(line):
            raw = m.group(0).strip()
            # Never treat a year range (e.g. "06 2020 - 06 2024" or "2018 - 2022") as a phone number
            if re.search(r'\b(?:19|20)\d{2}\b.*\b(?:19|20)\d{2}\b', raw):
                continue
            raw = re.sub(r'^(\+?\d{1,3})\)\s*', r'\1 ', raw)
            digits = re.sub(r'\D', '', raw)

            # If a 6-digit pincode preceded a 10-digit mobile on the same line, extract the mobile part
            if len(digits) > 13:
                sub_m = _MOBILE_IN_RE.search(raw)
                if sub_m:
                    raw = sub_m.group(0).strip()
                    digits = re.sub(r'\D', '', raw)
                else:
                    continue

            if len(digits) == 10:
                if digits[0] not in '6789' and not raw.startswith('('):
                    continue
            elif len(digits) == 11:
                if not ((digits.startswith('0') and digits[1] in '6789234') or digits.startswith('1')):
                    continue
            elif len(digits) == 12:
                if not (digits.startswith('91') and digits[2] in '6789'):
                    continue
            elif len(digits) == 13:
                if not (digits.startswith('091') and digits[3] in '6789'):
                    continue
            else:
                continue

            key = digits[-10:]
            if key not in seen_keys:
                seen_keys.add(key)
                all_phones.append(re.sub(r'\s+', ' ', raw))

        for m in _MOBILE_IN_RE.finditer(line):
            raw = m.group(0).strip()
            raw = re.sub(r'^(\+?\d{1,3})\)\s*', r'\1 ', raw)
            digits = re.sub(r'\D', '', raw)
            key = digits[-10:]
            if len(key) == 10 and key[0] in '6789' and key not in seen_keys:
                seen_keys.add(key)
                all_phones.append(re.sub(r'\s+', ' ', raw))

    return ", ".join(all_phones[:3]) if all_phones else "Not Provided"


def parse_text(text, hidden_emails, file_name="Not Provided"):
    # Always take pure basename for candidate name and experience extraction
    file_name = os.path.basename(str(file_name or "Not Provided").replace("\\", "/"))

    # Initialize all default empty values with professional placeholder
    data = {k: "Not Provided" for k in FINAL_HEADERS}
    data["File Name"] = file_name

    if not text or not text.strip():
        fallback_name = sanitize_filename(file_name)
        if fallback_name:
            data["Candidate Name"] = fallback_name
        return data

    # 1. Email (with OCR space-around-@ recovery)
    emails = EMAIL_REGEX.findall(text)
    if not emails:
        ocr_email_fixed = re.sub(r'([A-Za-z0-9_.+-]+)\s*@\s*([A-Za-z0-9-]+)\s*\.\s*(com|in|org|net|co\.in|edu)', r'\1@\2.\3', text, flags=re.IGNORECASE)
        emails = EMAIL_REGEX.findall(ocr_email_fixed)
    if emails:
        data["Email"] = emails[0].rstrip(".,;)")
    elif hidden_emails:
        data["Email"] = hidden_emails[0].rstrip(".,;)")

    # 2. Phone (single-line only, validated 10-13 digits, no year ranges or newlines)
    data["Candidate Contact Number"] = _extract_clean_phones(text)

    # Normalize text once for ATS extractors (skills, experience, education, name)
    try:
        from .parser import normalize_text
        from .extractor import extract_name, extract_experience, explicit_experience, extract_education, employment_ranges
        from .skills import scan_taxonomy, skill_key

        norm_text, display_text = normalize_text(text)
    except Exception:
        norm_text, display_text = text.lower(), text
        extract_name = extract_experience = explicit_experience = extract_education = employment_ranges = None
        scan_taxonomy = skill_key = None

    # 3. Skills (combine canonical SKILL_ALIASES + full 440+ ATS skill taxonomy)
    found_skills_map: Dict[str, str] = {}
    def _skey(s: str) -> str:
        return skill_key(s) if skill_key else re.sub(r'[\s.\-_/]+', '', s.lower())

    for match in SKILL_PATTERN.finditer(text):
        token = match.group(1).lower()
        if token in SKILL_ALIASES:
            canon = SKILL_ALIASES[token]
            found_skills_map[_skey(canon)] = canon
    for match in STRICT_PATTERN.finditer(text):
        token = match.group(1)
        if token in STRICT_C_SKILLS:
            canon = STRICT_C_SKILLS[token]
            found_skills_map[_skey(canon)] = canon

    if scan_taxonomy and norm_text:
        _UPPER_SKILLS = {
            "aws", "gcp", "sql", "nosql", "html", "css", "xml", "json", "rest", "api", "graphql",
            "jwt", "oauth", "nlp", "ai", "ml", "dl", "llm", "rag", "etl", "elt", "bi", "erp", "crm",
            "sap", "hrms", "hr", "bms", "pcb", "fpga", "vlsi", "vhdl", "rtos", "iot", "plc", "scada",
            "cnc", "cad", "cam", "cae", "fea", "cfd", "gdt", "gd&t", "apqp", "ppap", "fmea", "spc",
            "msa", "kaizen", "5s", "gst", "tds", "pf", "esi", "seo", "sem", "ppc", "smm", "b2b", "b2c",
            "kyc", "aml", "ifrs", "gaap", "mis", "kpi", "sla", "itil", "sdlc", "stlc", "uat", "qa",
            "qc", "gmp", "glp", "haccp", "ehs", "hse", "osha", "iso", "cmmi", "tcp/ip", "dns", "dhcp",
            "vpn", "lan", "wan", "vlan", "bgp", "ospf", "siem", "soc", "iam", "edr", "owasp", "ceh",
            "cissp", "ccna", "ccnp", "rhce", "pmp", "prince2", "cpa", "cfa", "cma", "acca", "icai",
            "ui", "ux", "ui/ux", "ci/cd", "t-sql", "pl/sql", "c", "r", "c++", "c#", "spi", "i2c",
            "uart", "can", "lin", "usb", "pcie", "ddr4", "emmc", "mipi", "csi", "dsi", "rgmii", "sdio",
            "sai", "hdl", "eda", "bom", "rfq", "rfi", "po", "grn", "3pl", "wms", "tms", "fmcg", "bfsi", "ms",
        }
        try:
            tax_res = scan_taxonomy(norm_text)
            for sk in tax_res.skills:
                k = _skey(sk)
                if k in found_skills_map:
                    continue
                if sk in SKILL_ALIASES:
                    found_skills_map[k] = SKILL_ALIASES[sk]
                elif sk.lower() in _UPPER_SKILLS:
                    found_skills_map[k] = sk.upper()
                else:
                    found_skills_map[k] = " ".join(
                        w.upper() if w.lower() in _UPPER_SKILLS else w.capitalize()
                        for w in sk.split()
                    )
        except Exception:
            pass

    if found_skills_map:
        seen_sk_lower = set()
        unique_skills = []
        for sv in sorted(found_skills_map.values(), key=lambda x: x.lower()):
            if sv.lower() not in seen_sk_lower:
                seen_sk_lower.add(sv.lower())
                unique_skills.append(sv)
        data["Skills"] = ", ".join(unique_skills)

    # 4. Candidate Name (use battle-tested ATS name extractor + post-cleaning)
    name = ""
    if extract_name:
        try:
            extracted_nm, nm_src = extract_name(display_text or text, file_name)
            if extracted_nm and extracted_nm != "Unknown" and nm_src != "filename":
                name = extracted_nm
            elif nm_src == "filename":
                name = sanitize_filename(file_name) or extracted_nm
        except Exception:
            pass

    if not name:
        name = sanitize_filename(file_name) or "Not Provided"

    if name and name != "Not Provided":
        name = re.sub(r'^(?:Mr|Ms|Mrs|Miss|Dr|Er|Ca|Prof)\.?\s+', '', name, flags=re.IGNORECASE).strip()
        # Strip father/husband name suffixes ("S/O ...", "S O ...", "D/O ...", "W/O ...")
        name = re.sub(r'\b(?:S\s*/?\s*O|D\s*/?\s*O|W\s*/?\s*O|C\s*/?\s*O)\b.*$', '', name, flags=re.IGNORECASE).strip()
        # Strip leading "Naukri " or "Internal " noise
        name = re.sub(r'^(?:Naukri|Internal|Resume|Cv|Curriculum|Curriculam|Vitae)\s+', '', name, flags=re.IGNORECASE).strip()
        # Deduplicate repeated name ("Arun Rajan S Arun Rajan S" -> "Arun Rajan S")
        words = name.split()
        half = len(words) // 2
        if half >= 2 and words[:half] == words[half:2 * half]:
            name = " ".join(words[:half])
        # Strip trailing city/state tokens accidentally attached to header names
        _LOC_TAIL = set(INDIAN_CITIES.keys()) | {
            "kerala", "tamil", "nadu", "tamilnadu", "telangana", "karnataka", "maharashtra",
            "andhra", "pradesh", "gujarat", "rajasthan", "punjab", "haryana", "up", "mp",
            "saravanampatti", "chettipalayam", "palayam", "india",
        }
        wlist = name.split()
        while len(wlist) > 1 and wlist[-1].lower().strip(".,") in _LOC_TAIL:
            wlist.pop()
        name = " ".join(wlist).strip()

    data["Candidate Name"] = name if name else (sanitize_filename(file_name) or "Not Provided")

    # 4b. Location (Prioritize explicit label, then Header, then Document Order; Cities before Countries)
    text_lower = text.lower()
    lines_lower = text_lower.split('\n')
    header_text = '\n'.join(lines_lower[:15])
    cand_name_tokens = {re.sub(r'[^a-z]', '', w.lower()) for w in data["Candidate Name"].split() if len(w) > 1}
    _AMBIGUOUS_NAME_CITIES = {"anand", "puri", "kota", "una", "ara", "karur", "moga", "palani", "vellore", "hassan"}

    found_location = "Not Provided"
    loc_label = re.search(r'(?:current\s+location|present\s+location|location|city|address|place\s+of\s+residence|residing\s+at)\s*[:\-]\s*([^\n]{2,80})', text_lower)
    if loc_label:
        m_lbl = CITY_REGEX.search(loc_label.group(1))
        if m_lbl:
            found_location = INDIAN_CITIES.get(m_lbl.group(1).lower(), "Not Provided")
    if found_location == "Not Provided":
        for m_city in CITY_REGEX.finditer(header_text):
            c_tok = m_city.group(1).lower()
            if c_tok in _AMBIGUOUS_NAME_CITIES and c_tok in cand_name_tokens:
                continue
            found_location = INDIAN_CITIES.get(c_tok, "Not Provided")
            if found_location != "Not Provided":
                break
    if found_location == "Not Provided":
        for m_city_all in CITY_REGEX.finditer(text_lower):
            c_tok = m_city_all.group(1).lower()
            if c_tok in _AMBIGUOUS_NAME_CITIES and c_tok in cand_name_tokens:
                continue
            found_location = INDIAN_CITIES.get(c_tok, "Not Provided")
            if found_location != "Not Provided":
                break
    if found_location == "Not Provided":
        # Country fallback only when no specific city was found anywhere in the resume
        for c_key, c_val in COUNTRY_FALLBACK.items():
            if re.search(r'\b' + re.escape(c_key) + r'\b', header_text):
                found_location = c_val
                break

    data["Current Location"] = found_location

    # 5. Experience (combine filename tags + explicit experience + reference-date-calibrated employment ranges)
    def _yr_unit(n: int) -> str:
        return "Year" if n == 1 else "Years"

    def _mo_unit(n: int) -> str:
        return "Month" if n == 1 else "Months"

    def _fmt_ym(total_mos: int) -> str:
        if total_mos <= 0:
            return "Fresher"
        y_part = total_mos // 12
        m_part = total_mos % 12
        if y_part > 0 and m_part > 0:
            return f"{y_part} {_yr_unit(y_part)} {m_part} {_mo_unit(m_part)}"
        if y_part > 0:
            return f"{y_part} {_yr_unit(y_part)}"
        return f"{m_part} {_mo_unit(m_part)}"

    def _fmt_years(y: float) -> str:
        if y <= 0:
            return "Fresher"
        if abs(y - round(y)) < 0.08:
            ri = int(round(y))
            return f"{ri} {_yr_unit(ri)}" if ri > 0 else "Fresher"
        if y < 1.0:
            mi = max(1, int(round(y * 12)))
            return f"{mi} {_mo_unit(mi)}"
        rv = round(y, 1)
        return f"{rv:g} {'Year' if rv == 1.0 else 'Years'}"

    # Determine reference date for "Present" ranges (2025-03-01 unless 2026 appears in resume text)
    has_2026_in_text = bool(re.search(r'\b2026\b', text))
    ref_year = 2026 if has_2026_in_text else 2025
    ref_month = datetime.now().month if has_2026_in_text else 3

    # Parse filename experience tags: [14yrs], [3y-5m], [2yr-4m], [4y_9m], [5y-0], [15+], [7-4], [30], [4m], [0], [fresher]
    fn_months = -1
    fn_is_fresher = False
    if re.search(r'\[\s*fresher\s*\]|\bfresher\b', file_name, re.IGNORECASE):
        fn_is_fresher = True
    else:
        m_br_mo = re.search(r'\[\s*(\d{1,2})\s*(?:months?|mos?|m)\s*\]', file_name, re.IGNORECASE)
        m_br_ym = re.search(
            r'\[\s*(\d{1,2})\s*\+?\s*(?:yrs?|years?|y)?(?:[-_\s\.]*(\d{1,2})\s*(?:months?|mos?|m)?)?\s*\]',
            file_name,
            re.IGNORECASE,
        )
        m_unbr_ym = re.search(
            r'(?:^|[\s_\-])(\d{1,2})\s*(?:\+\s*(?:yrs?|years?|y)?|(?:yrs?|years?|y)\b)(?:[-_\s\.]*(\d{1,2})\s*(?:months?|mos?|m)\b)?',
            file_name,
            re.IGNORECASE,
        )
        if m_br_mo:
            mi = int(m_br_mo.group(1))
            if mi == 0:
                fn_is_fresher = True
            elif 0 < mi <= 60:
                fn_months = mi
        elif m_br_ym:
            yi = int(m_br_ym.group(1))
            mi = int(m_br_ym.group(2)) if m_br_ym.group(2) else 0
            if yi == 0 and mi == 0:
                fn_is_fresher = True
            elif 0 <= yi <= 45 and 0 <= mi < 12:
                fn_months = yi * 12 + mi
        elif m_unbr_ym:
            yi = int(m_unbr_ym.group(1))
            mi = int(m_unbr_ym.group(2)) if m_unbr_ym.group(2) else 0
            if 0 < yi <= 45 and 0 <= mi < 12:
                fn_months = yi * 12 + mi

    ats_exp = 0.0
    if extract_experience and norm_text:
        try:
            from .extractor import using_reference_date
            with using_reference_date(datetime(ref_year, ref_month, 1)):
                ats_exp = extract_experience(norm_text)
        except Exception:
            ats_exp = extract_experience(norm_text)
    exp_expl = explicit_experience(norm_text) if explicit_experience and norm_text else 0.0

    # Also check explicit "X years Y months" or table "Experience | X years" in text
    expl_ym_months = 0
    _EXPL_YM_PATTERNS = [
        re.compile(
            r'\b(?:total\s+|overall\s+|work\s+|professional\s+)?(?:experience|exp)\s*(?:of|is|:|-|\||=)?\s*'
            r'(?:around|about|over|nearly|more\s+than|approx\.?)?\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?|yr)\b'
            r'(?:\s*(?:and|&|,)?\s*(\d{1,2})\s*(?:months?|mos?|mths?)\b)?',
            re.IGNORECASE,
        ),
        re.compile(
            r'\b(\d{1,2})\s*\+?\s*(?:years?|yrs?|yr)\b(?:\s*(?:and|&|,)?\s*(\d{1,2})\s*(?:months?|mos?|mths?)\b)?'
            r'\s+(?:of\s+)?(?:[a-z\-/&]+\s+){0,4}?(?:experience|exp\b)',
            re.IGNORECASE,
        ),
    ]
    for epat in _EXPL_YM_PATTERNS:
        for em in epat.finditer(text):
            yi = int(em.group(1))
            mi = int(em.group(2)) if em.group(2) else 0
            before_ctx = text[max(0, em.start() - 60):em.start()].lower()
            if re.search(r'\b(?:age|aged|company|firm|organization|group|established|founded)\b', before_ctx):
                continue
            if 0 < yi <= 45 and 0 <= mi < 12:
                cand_mos = yi * 12 + mi
                if cand_mos > expl_ym_months:
                    expl_ym_months = cand_mos

    if exp_expl == 0.0:
        exp_m = EXP_REGEX.search(text)
        if exp_m:
            try:
                val = float(exp_m.group(1))
                if 0 < val <= 45:
                    exp_expl = val
            except ValueError:
                pass

    total_exp_months = 0
    if fn_months > 0:
        total_exp_months = fn_months
        data["Total Years of Experience"] = _fmt_ym(fn_months)
    elif fn_is_fresher:
        total_exp_months = 0
        data["Total Years of Experience"] = "Fresher"
    elif expl_ym_months > 0 or exp_expl > 0 or ats_exp > 0:
        best_float = max(exp_expl, expl_ym_months / 12.0)
        # If explicit experience was just a short sub-skill mention (<2y) while employment ranges show much more, use ats_exp
        if ats_exp > best_float and (best_float == 0.0 or (best_float <= 1.5 and ats_exp >= best_float + 1.5)):
            best_float = ats_exp
            total_exp_months = int(round(best_float * 12))
            data["Total Years of Experience"] = _fmt_years(best_float)
        elif expl_ym_months > 0 and (expl_ym_months % 12 != 0) and abs((expl_ym_months / 12.0) - best_float) < 0.2:
            total_exp_months = expl_ym_months
            data["Total Years of Experience"] = _fmt_ym(expl_ym_months)
        else:
            total_exp_months = int(round(best_float * 12))
            data["Total Years of Experience"] = _fmt_years(best_float)
    elif re.search(r'\b(fresher|fresh graduate|no experience|entry.?level|0 year)\b', text, re.IGNORECASE):
        total_exp_months = 0
        data["Total Years of Experience"] = "Fresher"

    # 7. Known Languages
    found_langs = set()
    for match in LANG_REGEX.finditer(text):
        found_langs.add(match.group(1).title())
    if found_langs:
        data["Languages Known"] = ", ".join(sorted(list(found_langs)))

    # 8. Degrees (deduplicated & canonicalized UG + PG, with context check & ATS education_level fallback)
    degrees: List[str] = []
    seen_deg = set()

    _DEG_CANON_MAP = {
        "be": "B.E.", "btech": "B.Tech", "bsc": "B.Sc", "bcom": "B.Com", "bcomh": "B.Com (Hons)",
        "bcomhons": "B.Com (Hons)", "bba": "BBA", "bca": "BCA", "ba": "B.A.", "mbbs": "MBBS",
        "bds": "BDS", "barch": "B.Arch", "llb": "LL.B", "bed": "B.Ed", "bpharm": "B.Pharm",
        "diploma": "Diploma", "polytechnic": "Diploma", "sslc": "SSLC", "hsc": "HSC",
        "phd": "Ph.D", "me": "M.E.", "mtech": "M.Tech", "msc": "M.Sc", "mba": "MBA",
        "mca": "MCA", "mcom": "M.Com", "ma": "M.A.", "pgdm": "PGDM", "pgdiploma": "PG Diploma",
        "postgraduatediploma": "PG Diploma", "md": "M.D.", "ms": "M.S.", "ca": "CA",
        "icwa": "ICWA", "cfa": "CFA", "med": "M.Ed", "mpharm": "M.Pharm",
    }
    _EDU_LINE_CTX_RE = re.compile(
        r'\b(?:education|educational|academic|qualification|degree|graduation|undergraduate|postgraduate|'
        r'university|college|institute|school|board|autonomous|engineering|technology|science|commerce|'
        r'arts|computer|management|business|cgpa|gpa|percentage|marks|class|division|passed|graduated|'
        r'pursuing|completed|icai|chartered\s+accountant|branch|stream|specialization)\b',
        re.IGNORECASE,
    )
    _MS_TOOL_AFTER_RE = re.compile(
        r'^\s*[\.\-]?\s*(?:office|excel|word|powerpoint|access|outlook|project|visio|sql|teams|windows|dynamics|azure|paint|bi|dos)\b',
        re.IGNORECASE,
    )

    def _canonicalize_deg(raw_deg: str) -> str:
        d = re.sub(r'\s+', ' ', raw_deg.strip(" .,;:-/()"))
        d_compact = re.sub(r'[^a-z]', '', d.lower())
        if d_compact in _DEG_CANON_MAP:
            return _DEG_CANON_MAP[d_compact]
        # Clean up "Bachelor of Computer Application" -> "Bachelor of Computer Applications"
        if re.match(r'^bachelor.*computer\s+application$', d, re.IGNORECASE):
            return "Bachelor of Computer Applications"
        if re.match(r'^master.*computer\s+application$', d, re.IGNORECASE):
            return "Master of Computer Applications"
        return " ".join(w if w.lower() in {"of", "in", "and"} else w.capitalize() for w in d.split())

    def _add_deg(raw_deg: str):
        canon = _canonicalize_deg(raw_deg)
        d_key = re.sub(r'[^a-z]', '', canon.lower())
        # Map full names to short keys so "B.E." and "Bachelor of Engineering" don't duplicate
        _EQUIV_KEYS = {
            "bachelorofengineering": "be", "bacheloroftechnology": "btech",
            "bachelorofscience": "bsc", "bachelorofcommerce": "bcom",
            "bachelorofarts": "ba", "bachelorofcomputerapplications": "bca",
            "bachelorofbusinessadministration": "bba",
            "masterofengineering": "me", "masteroftechnology": "mtech",
            "masterofscience": "msc", "masterofcommerce": "mcom",
            "masterofarts": "ma", "masterofcomputerapplications": "mca",
            "masterofbusinessadministration": "mba",
        }
        norm_key = _EQUIV_KEYS.get(d_key, d_key)
        if canon and norm_key not in seen_deg:
            seen_deg.add(norm_key)
            degrees.append(canon)

    def _line_around(pos: int) -> str:
        ls = text.rfind("\n", 0, pos)
        le = text.find("\n", pos)
        prev_ls = text.rfind("\n", 0, max(0, ls - 1)) if ls > 0 else 0
        next_le = text.find("\n", le + 1) if le != -1 else len(text)
        return text[max(0, prev_ls): (next_le if next_le != -1 else len(text))]

    for ug_match in UG_REGEX.finditer(text):
        deg = ug_match.group(1).strip()
        d_clean = re.sub(r'[^a-z]', '', deg.lower())
        if d_clean in ("be", "ba", "bs", "ca", "cs"):
            if "." not in deg and not deg.isupper():
                continue
            if "." not in deg and not _EDU_LINE_CTX_RE.search(_line_around(ug_match.start())):
                continue
        _add_deg(deg)
        break

    for pg_match in PG_REGEX.finditer(text):
        deg = pg_match.group(1).strip()
        d_clean = re.sub(r'[^a-z]', '', deg.lower())
        after_txt = text[pg_match.end():pg_match.end() + 25]
        if d_clean == "ms" and _MS_TOOL_AFTER_RE.search(after_txt):
            continue
        if d_clean in ("me", "ma", "ms", "md", "ca"):
            if "." not in deg and not deg.isupper():
                continue
            if "." not in deg and not _EDU_LINE_CTX_RE.search(_line_around(pg_match.start())):
                continue
        _add_deg(deg)
        break

    if not degrees and extract_education and norm_text:
        edu_lvl = extract_education(norm_text)
        _LEVEL_LABELS = {
            "phd": "Ph.D",
            "master": "Master's Degree",
            "bachelor": "Bachelor's Degree",
            "associate": "Associate Degree",
            "diploma": "Diploma",
            "school": "SSLC / HSC",
        }
        if edu_lvl in _LEVEL_LABELS:
            degrees.append(_LEVEL_LABELS[edu_lvl])

    if degrees:
        data["Education (UG / PG Degree)"] = " / ".join(degrees)

    # 9. Companies & Years Worked in Current Company
    _LEGAL_CORP_RE = re.compile(
        r'\b(?:Pvt\.?\s*Ltd\.?|Private\s+Limited|Ltd\.?|Limited|LLP|Inc\.?|Corp\.?|Corporation|LLC|GmbH|Plc|M/s\.?)\b',
        re.IGNORECASE,
    )
    _STRICT_CORP_RE = re.compile(
        r'\b(?:Pvt\.?\s*Ltd\.?|Private\s+Limited|Ltd\.?|Limited|LLP|Inc\.?|Corp\.?|Corporation|LLC|GmbH|Plc|'
        r'M/s\.?|Holdings|Industries|Enterprises|Logistics|Technologies|Solutions|Systems|Services|'
        r'Consultancy|Consulting|Motors|Automotive|Pharma|Pharmaceuticals|Healthcare|Hospitals|Bank|'
        r'Financial\s+Services|Insurance|Exports|Imports|Mills|Spinning|Textiles|Polymers|Chemicals|'
        r'Constructions|Builders|Infrastructures|Electronics|Electricals|Semiconductors|'
        r'Telecom|Communications|Laboratories|Ventures|Associates|'
        r'Furnaces|Valves|Pumps|Cranes|Foods|Beverages)\b',
        re.IGNORECASE,
    )
    _TOOL_FALSE_ORGS = re.compile(
        r'\b(?:google\s+(?:sheet|sheets|docs|slides|drive|analytics|cloud|ads|tag|workspace|colab|forms)|'
        r'microsoft\s+(?:office|word|excel|powerpoint|visio|project|azure|teams|windows|dynamics|power\s*bi|sql)|'
        r'amazon\s+(?:web\s+services|aws|s3|ec2|rds|redshift|emr)|'
        r'oracle\s+(?:sql|db|database|erp|ebs|fusion|10g|11g|12c|19c|pl/?sql|forms)|'
        r'sap\s+(?:fico|mm|sd|pp|qm|abap|hr|hcm|basis|is\s+retail|hana|erp|b1|business\s+one)|'
        r'power\s*bi|tableau|tally\s*(?:erp|prime)?|zoho\s*(?:books|crm|people)?|salesforce\s*(?:crm|admin)?)\b',
        re.IGNORECASE,
    )
    _BAD_COMPANY_PHRASES = re.compile(
        r'\b(?:notice\s+period|days?\s+notice|months?\s+notice|immediate\s+joiner|career\s+objective|'
        r'curricul[au]m\s+vitae|resume|summary|profile|description|academic\s+credentials|declaration|'
        r'personal\s+details|languages?\s+known|hobbies|references|graduated|first\s+class|second\s+class|'
        r'cgpa|percentage|bachelor|master|diploma|university|college|school|institute|polytechnic|matriculation|'
        r'higher\s+secondary|board\s+of|communication\s+protocol|mipi\s+csi|pcb\s+layout|power\s+vent|'
        r'software\s+products|customer\s+expectations|safety\s+protocols|company\s+policies|company\s+s\s+reputation|'
        r'purchase\s+orders|talent\s+acquisition|employee\s+engagement|business\s+development|corporate\s+sales|'
        r'sales\s+environment|sales\s+commercial|production\s+department|hr\s+recruiter|it\s+recruiter|'
        r'currently\s+working|working\s+as|held\s+on|best[\s\-]in[\s\-]class|waste\s+and|develop\s+and|'
        r'understanding\s+of|assessing\s+company|attention\s+detail|read\s+write\s+speak|key\s+tools|'
        r'supplier\s+evaluation|vendor\s+development|fault\s+insertion|lsmw|idoc|for\s+consumers)\b',
        re.IGNORECASE,
    )
    _PROSE_STARTERS = {
        "to", "from", "in", "on", "at", "by", "for", "with", "of", "the", "a", "an", "and", "or",
        "as", "like", "such", "into", "over", "under", "between", "through", "during", "including",
        "using", "worked", "working", "currently", "previously", "formerly", "presently", "earlier",
        "responsible", "handled", "handling", "managing",
        "managed", "developed", "developing", "designed", "designing", "implemented", "implementing",
        "prepared", "preparing", "assisted", "assisting", "involved", "good", "strong", "excellent",
        "sound", "knowledge", "experience", "experienced", "skilled", "proficient", "ability",
        "understanding", "assessing", "waste", "develop", "best", "senior", "junior", "asst",
        "assistant", "sr", "jr", "role", "position", "designation", "project", "title", "duration",
        "period", "location", "place", "date", "ctc", "salary", "notice", "month", "months", "days",
        "skills", "technical", "tools", "languages", "education", "qualification",
    }

    def _clean_org_preserve_case(raw: str) -> str:
        if not raw:
            return ""
        c = re.sub(r'^(?:Company\s*Name|Present\s*Employer|Current\s*Employer|Previous\s*Employer|Employer\s*Name|Organization\s*Name|Employer|Organization|Organisation|Company|Client)\s*[:\-\.]*\s*', '', raw.strip(), flags=re.IGNORECASE).strip()
        c = re.sub(r'^(?:Role|Position|Job\s*Title|Designation|Title)\s*[:\-\.]*\s*', '', c, flags=re.IGNORECASE).strip()
        c = re.sub(r'^[\u2022\u00b7\-\*\d\.\)\(\s]+', '', c).strip()
        c = re.sub(r'\(.*?\)', ' ', c).strip()
        # Extract company after "working at / worked with / employed in ..." if present
        emp_verb_m = re.search(
            r'\b(?:(?:currently|presently|previously|formerly|earlier)\s+)?(?:working|worked|employed|associated|serving|served)\s+'
            r'(?:as\s+[A-Za-z\s\-/&,.]+?\s+)?(?:at|with|in|for)\s+([A-Z][A-Za-z0-9\s\.\&\-\',]{2,75})',
            c,
            flags=re.IGNORECASE,
        )
        if emp_verb_m:
            c = emp_verb_m.group(1).strip()
        # Strip role prefix before "at" if present ("Software Engineer at Infosys Ltd" -> "Infosys Ltd")
        at_m = re.search(r'\bat\s+([A-Z][A-Za-z0-9\s\.\&\-\']{2,65})$', c)
        if at_m:
            c = at_m.group(1).strip()
        # Strip trailing "as <Role>" ("Genpact India Pvt Ltd as Senior Process Associate" -> "Genpact India Pvt Ltd")
        c = re.sub(r'\s+\bas\s+(?:a\s+|an\s+)?[A-Za-z0-9\s\-\./,&]+$', '', c, flags=re.IGNORECASE).strip()
        # Strip trailing "from / since / between <Date>"
        c = re.sub(r'\s+\b(?:from|since|between|during)\s+.*$', '', c, flags=re.IGNORECASE).strip()
        c = DATE_MONTHS_RE.sub(' ', c)
        c = DATE_CONNECTORS_RE.sub(' ', c)
        c = YEARS_RE.sub(' ', c)
        # Strip trailing location after comma ("Infosys Ltd, Hyderabad" -> "Infosys Ltd")
        if ',' in c:
            c = c.split(',')[0].strip()
        c = CITY_SUFFIX_RE.sub('', c).strip()
        c = re.sub(r'[^A-Za-z0-9\s\.\&\-\']', ' ', c).strip()
        c = re.sub(r'\s*[\u2013\u2014\-\.]+\s*$', '', c).strip()
        return ' '.join(c.split())

    def _score_org(raw_cleaned: str, from_explicit_label: bool = False) -> int:
        if not raw_cleaned or len(raw_cleaned) < 2 or len(raw_cleaned) > 65:
            return -500
        txt_lower = raw_cleaned.lower()
        words = raw_cleaned.split()
        max_w = 8 if _LEGAL_CORP_RE.search(raw_cleaned) else 6
        if not (1 <= len(words) <= max_w):
            return -500

        first_w = re.sub(r'[^a-z]', '', words[0].lower())
        last_w = re.sub(r'[^a-z]', '', words[-1].lower())
        if first_w in _PROSE_STARTERS or first_w in EDU_JUNK or last_w in {"and", "or", "for", "with", "to", "in", "of", "the", "as"}:
            return -500
        if _BAD_COMPANY_PHRASES.search(txt_lower) or _TOOL_FALSE_ORGS.search(txt_lower):
            return -500
        if ACTION_VERBS_RE.search(txt_lower) or PROJECT_WORDS_RE.search(txt_lower):
            return -500
        if txt_lower in IGNORE_ORGS or txt_lower in SKILL_ALIASES or _skey(txt_lower) in found_skills_map:
            return -500
        if all(w.lower().strip(".,") in {r.lower() for r in ROLE_WORDS} for w in words):
            return -500

        score = 0
        has_corp_kw = bool(_STRICT_CORP_RE.search(raw_cleaned))
        has_predef = bool(PREDEFINED_ORGS_RE.search(txt_lower))

        if has_corp_kw:
            score += 65
        if has_predef:
            score += 75
        if from_explicit_label:
            score += 55

        # Require genuine capitalization in the original text (NOT forced .title()!)
        cap_words = sum(1 for w in words if w[0].isupper())
        if cap_words == len(words):
            score += 25
        elif cap_words >= max(1, len(words) - 1) and words[0][0].isupper():
            score += 15
        else:
            score -= 60

        # If neither corporate suffix, predefined brand, nor explicit label, reject generic phrases
        if not (has_corp_kw or has_predef or from_explicit_label):
            return -100

        if SKILL_SIGNALS_RE.search(txt_lower) and not _LEGAL_CORP_RE.search(raw_cleaned):
            score -= 80

        return score

    def _format_org(name_str: str) -> str:
        for po in PREDEFINED_ORGS:
            if po.lower() == name_str.lower() or (po.lower() in name_str.lower() and len(name_str.split()) <= 2 and not _STRICT_CORP_RE.search(name_str)):
                return po
        out = []
        for w in name_str.split():
            if w.upper() in {"TCS", "HCL", "IBM", "PWC", "EY", "KPMG", "HDFC", "ICICI", "SBI", "L&T", "LLP", "LLC", "MNC", "R&D", "IT", "HR"}:
                out.append(w.upper())
            elif w.lower().rstrip(".") in {"pvt", "ltd"}:
                out.append(w.lower().rstrip(".").capitalize() + ".")
            else:
                out.append(w if (w.isupper() and len(w) <= 4) else w.capitalize())
        return " ".join(out)

    exp_idx = re.search(
        r'(?m)^\s*(?:Work\s+Experience|Professional\s+Experience|Employment\s+History|Organizational\s+Experience|Career\s+History|Work\s+History|Experience\s+Summary|Experience)\s*[:\-]?\s*$',
        text,
        re.IGNORECASE,
    )
    if not exp_idx:
        exp_idx = re.search(
            r'\b(?:Work\s+Experience|Professional\s+Experience|Employment\s+History|Organizational\s+Experience|Career\s+History)\b',
            text,
            re.IGNORECASE,
        )
    post_exp_text = text[exp_idx.end():exp_idx.end() + 5000] if exp_idx else text[:5000]

    found_orgs_with_dates: List[str] = []
    found_orgs_fallback: List[str] = []
    already_extracted = set()

    def _record_org(org_name: str, is_current: bool):
        fmt = _format_org(org_name)
        key = fmt.lower()
        if key not in already_extracted:
            already_extracted.add(key)
            if is_current:
                found_orgs_with_dates.append(fmt)
            else:
                found_orgs_fallback.append(fmt)

    # Pass 0: Structured Table Column Extraction (from PyMuPDF find_tables() and DOCX tables)
    all_lines_raw = [ln.strip() for ln in text.splitlines() if ln.strip()]
    org_col_idx = -1
    for ln in all_lines_raw:
        if "|" in ln:
            cells = [c.strip() for c in ln.split("|")]
            if 2 <= len(cells) <= 7:
                # Check if this row is a table header row
                header_hit = -1
                is_edu_table = any(re.search(r'\b(?:degree|course|board|university|institution|college|school|cgpa|percentage|marks)\b', c, re.IGNORECASE) for c in cells)
                if is_edu_table:
                    org_col_idx = -1
                    continue
                for cidx, cell_txt in enumerate(cells):
                    if re.match(r'^(?:company(?:\s*name)?|organization(?:\s*name)?|organisation(?:\s*name)?|employer(?:\s*name)?|workplace|firm|client)$', cell_txt.strip(" .:-"), re.IGNORECASE):
                        header_hit = cidx
                        break
                if header_hit != -1:
                    org_col_idx = header_hit
                    continue
                if 0 <= org_col_idx < len(cells):
                    cand_cell = _clean_org_preserve_case(cells[org_col_idx])
                    if _score_org(cand_cell, from_explicit_label=True) >= 40:
                        is_curr_row = bool(re.search(r'\b(?:present|current|till\s+date|today)\b', ln, re.IGNORECASE))
                        _record_org(cand_cell, is_curr_row)

    # Pass 1: Explicit labels ("Company: ...", "Organization: ...", "Employer: ...", "Working at / Worked with ...")
    _EXPLICIT_ORG_PATTERNS = [
        re.compile(r'(?:Current\s+|Present\s+|Previous\s+|Past\s+)?(?:Company\s*Name|Organization\s*Name|Employer\s*Name|Company|Organization|Organisation|Employer)\s*[:\-]\s*([^\n\|]{2,70})', re.IGNORECASE),
        re.compile(
            r'\b(?:(?:Currently|Presently|Previously|Formerly|Earlier)\s+)?(?:working|worked|employed|associated)\s+'
            r'(?:as\s+[^,\n\(\|]{2,40}?\s+)?(?:at|with|in|for)\s+([A-Z][A-Za-z0-9\s\.\&\-\']{2,65}?)'
            r'(?:\s+\bas\b|\s+\b(?:from|since|between|during)\b|[,\|\(\n]|$)',
            re.IGNORECASE,
        ),
    ]
    for pat in _EXPLICIT_ORG_PATTERNS:
        for m in pat.finditer(text):
            raw_cand = m.group(1).strip()
            if _TOOL_FALSE_ORGS.search(raw_cand):
                continue
            cleaned = _clean_org_preserve_case(raw_cand)
            if _score_org(cleaned, from_explicit_label=True) >= 45:
                full_line_end = text.find("\n", m.start())
                line_ctx = text[m.start():full_line_end if full_line_end != -1 else m.end() + 40]
                is_prev = bool(re.search(r'\b(?:previous|previously|past|formerly|earlier|worked)\b', m.group(0), re.IGNORECASE))
                is_curr = (not is_prev) and bool(re.search(r'\b(?:present|presently|current|currently|till\s+date|working)\b', line_ctx, re.IGNORECASE))
                _record_org(cleaned, is_curr)

    # Pass 2: Date-anchored company extraction in Experience section
    lines = [l.strip() for l in post_exp_text.split("\n") if len(l.strip()) > 2][:75]
    date_line_set = set()
    for i, line in enumerate(lines):
        if DATE_RANGE_RE.search(line) or re.search(r'\b(Present|Current|Today|Till Date)\b', line, re.IGNORECASE):
            date_line_set.add(i)

    for date_idx in sorted(date_line_set):
        date_line = lines[date_idx]
        # Skip education date lines
        if re.search(r'\b(?:B\.?E|B\.?Tech|M\.?Tech|MBA|MCA|BCA|B\.?Sc|M\.?Sc|B\.?Com|Diploma|SSLC|HSC|10th|12th|University|College|School|Institute|CGPA|Percentage)\b', date_line, re.IGNORECASE):
            continue
        is_current = bool(re.search(r'\b(Present|Current|Today|Till Date)\b', date_line, re.IGNORECASE))
        lookback = max(0, date_idx - 5)
        candidate_lines = lines[lookback:min(len(lines), date_idx + 2)]

        best_name = None
        best_score = 35  # minimum score threshold

        for cline in candidate_lines:
            if any(term in cline.lower() for term in ['declaration', 'signature', 'hobbies', 'interest', 'personal details', 'education', 'academic']):
                break
            if re.match(r'^\s*(?:skills|technical\s+skills|key\s+skills|tools|technologies|software)\s*[:\-]', cline, re.IGNORECASE):
                continue
            if _TOOL_FALSE_ORGS.search(cline):
                continue

            # Strategy A: "<Role> at/with <Company>"
            at_match = re.search(r'\b(?:at|with)\s+([A-Z][A-Za-z0-9\s\.\&\-\']{2,65}?)(?:,|\(|\||$|\s+\bas\b|\s+(?:from|since|between|in|\d{4}|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b)', cline)
            if at_match:
                cleaned = _clean_org_preserve_case(at_match.group(1))
                s = _score_org(cleaned, from_explicit_label=True)
                if s > best_score and cleaned.lower() not in already_extracted:
                    best_score = s
                    best_name = cleaned

            # Strategy B: Pipe or dash separated segments ("Company | Role | Date" or "Infosys Ltd - Engineer")
            for sep_pat in (r'\|', r'\s+[\u2013\u2014\-]\s+'):
                if re.search(sep_pat, cline):
                    for part in re.split(sep_pat, cline):
                        cleaned = _clean_org_preserve_case(part)
                        s = _score_org(cleaned, from_explicit_label=False)
                        if s > best_score and cleaned.lower() not in already_extracted:
                            best_score = s
                            best_name = cleaned

            # Strategy C: Full line (cleaned)
            cleaned = _clean_org_preserve_case(cline)
            s = _score_org(cleaned, from_explicit_label=False)
            if s > best_score and cleaned.lower() not in already_extracted:
                best_score = s
                best_name = cleaned

        if best_name:
            _record_org(best_name, is_current)

    # Pass 3: Scan remaining lines in post_exp_text for unmistakable corporate suffixes (e.g. " XYZ Pvt. Ltd.")
    if not found_orgs_with_dates and not found_orgs_fallback:
        for cline in lines:
            if re.match(r'^\s*(?:skills|technical\s+skills|key\s+skills|tools|technologies|software)\s*[:\-]', cline, re.IGNORECASE):
                continue
            if _TOOL_FALSE_ORGS.search(cline):
                continue
            if _STRICT_CORP_RE.search(cline):
                for part in re.split(r'[|,\u2013\u2014]|(?:\s+-\s+)', cline):
                    if _STRICT_CORP_RE.search(part):
                        cleaned = _clean_org_preserve_case(part)
                        if _score_org(cleaned, from_explicit_label=False) >= 50:
                            _record_org(cleaned, False)

    current_company = "Not Provided"
    previous_list: List[str] = []

    if found_orgs_with_dates:
        current_company = found_orgs_with_dates[0]
        other_potential = found_orgs_with_dates[1:] + found_orgs_fallback
    elif found_orgs_fallback:
        current_company = found_orgs_fallback[0]
        other_potential = found_orgs_fallback[1:]
    else:
        other_potential = []

    if current_company == "Not Provided" and exp_idx:
        if re.search(r'\b(freelanc|self.?employ|independent consultant)\b', post_exp_text, re.IGNORECASE):
            current_company = "Freelance / Self-Employed"

    for o in other_potential:
        if o.lower() != current_company.lower() and o.lower() not in [p.lower() for p in previous_list]:
            previous_list.append(o)

    data["Current Company"] = current_company
    if previous_list:
        data["Previous Companies"] = ", ".join(previous_list[:5])

    # Calculate Years Worked in Current Company ONLY when candidate has a Current Company and is not a Fresher
    if current_company != "Not Provided" and data["Total Years of Experience"] != "Fresher":
        _MONTH_MAP = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }
        _EDU_RANGE_SKIP_RE = re.compile(
            r'\b(?:B\.?E|B\.?Tech|M\.?Tech|MBA|MCA|BCA|B\.?Sc|M\.?Sc|B\.?Com|M\.?Com|B\.?A|M\.?A|Diploma|SSLC|HSC|'
            r'10th|12th|XII|Xth|CBSE|ICSE|Matriculation|Higher\s+Secondary|University|College|School|Institute|'
            r'Polytechnic|CGPA|GPA|Percentage|Graduated|Semester|Academic|Course|Curriculum)\b',
            re.IGNORECASE,
        )

        def _parse_ym_token(tok: str, is_end: bool) -> Optional[Tuple[int, int, bool]]:
            t = tok.strip()
            if re.search(r'\b(?:Present|Current|Till\s*Date|Till\s*Now|Today|Now|Working|Continuing|Ongoing)\b', t, re.IGNORECASE):
                return (ref_year, ref_month, True)
            # Month name + Year ("Jan 2021", "Feb'22", "2021 Jan", "15th June 2021")
            m_mon = re.search(r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b', t, re.IGNORECASE)
            m_yr4 = re.search(r'\b((?:19|20)\d{2})\b', t)
            if m_mon and m_yr4:
                return (int(m_yr4.group(1)), _MONTH_MAP[m_mon.group(1)[:3].lower()], False)
            m_yr2 = re.search(r"['\-\s](\d{2})\b", t)
            if m_mon and m_yr2:
                y2 = int(m_yr2.group(1))
                y4 = (2000 + y2) if y2 <= 30 else (1900 + y2)
                return (y4, _MONTH_MAP[m_mon.group(1)[:3].lower()], False)
            # DD/MM/YYYY or MM/YYYY
            m_dmy = re.match(r'^(\d{1,2})\s*[/\.\-]\s*(\d{1,2})\s*[/\.\-]\s*((?:19|20)\d{2})$', t)
            if m_dmy:
                a, b, y4 = int(m_dmy.group(1)), int(m_dmy.group(2)), int(m_dmy.group(3))
                mo = b if 1 <= b <= 12 else (a if 1 <= a <= 12 else 1)
                return (y4, mo, False)
            m_my = re.match(r'^(\d{1,2})\s*[/\.\-]\s*((?:19|20)\d{2})$', t)
            if m_my and 1 <= int(m_my.group(1)) <= 12:
                return (int(m_my.group(2)), int(m_my.group(1)), False)
            # Bare YYYY
            if m_yr4:
                return (int(m_yr4.group(1)), ref_month if is_end else 1, False)
            return None

        _DATE_TOKEN_PAT = (
            r'(?:(?:\d{1,2}(?:st|nd|rd|th)?[\s/\.\-]+)?(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
            r'Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)[\s,\.\'\-]*(?:(?:19|20)\d{2}|\d{2})|'
            r'(?:19|20)\d{2}[\s,\.\'\-]+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|'
            r'Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)|'
            r'\d{1,2}[/\.\-]\d{1,2}[/\.\-](?:19|20)\d{2}|\d{1,2}[/\.\-](?:19|20)\d{2}|\b(?:19|20)\d{2}\b)'
        )
        _WORK_RANGE_RE = re.compile(
            r'\(?(' + _DATE_TOKEN_PAT + r')\s*(?:-|\u2013|\u2014|\bto\b|\btill\b)\s*'
            r'(' + _DATE_TOKEN_PAT + r'|\b(?:Present|Current|Till\s*Date|Till\s*Now|Today|Now|Working|Continuing|Ongoing)\b)\)?',
            re.IGNORECASE,
        )
        _SINCE_RE = re.compile(
            r'\b(?:since|working\s+from|employed\s+since|associated\s+since|from)\s+(' + _DATE_TOKEN_PAT + r')'
            r'(?!\s*(?:-|\u2013|\u2014|\bto\b))',
            re.IGNORECASE,
        )

        # Collect all non-education employment intervals in post_exp_text
        work_intervals: List[Tuple[int, bool, bool]] = []  # (months, is_present_end, near_current_company)
        co_tokens = [w.lower() for w in re.findall(r'[A-Za-z0-9]{3,}', current_company) if w.lower() not in {"pvt", "ltd", "private", "limited", "india", "company", "services", "technologies", "solutions", "systems"}]

        for dm in _WORK_RANGE_RE.finditer(post_exp_text):
            abs_pos = (exp_idx.end() + dm.start()) if exp_idx else dm.start()
            line_ctx = _line_around(abs_pos)
            if _EDU_RANGE_SKIP_RE.search(line_ctx) and not _LEGAL_CORP_RE.search(line_ctx):
                continue
            p_start = _parse_ym_token(dm.group(1), is_end=False)
            p_end = _parse_ym_token(dm.group(2), is_end=True)
            if p_start and p_end:
                sy, sm, _ = p_start
                ey, em, is_pres = p_end
                mos = (ey - sy) * 12 + (em - sm)
                # When both start and end were bare years (e.g. 2019 - 2021), use exact year diff * 12
                if re.fullmatch(r'(?:19|20)\d{2}', dm.group(1).strip()) and re.fullmatch(r'(?:19|20)\d{2}', dm.group(2).strip()):
                    mos = (ey - sy) * 12
                if 0 <= mos <= 45 * 12:
                    near_co = any(tok in line_ctx.lower() for tok in co_tokens[:2]) if co_tokens else False
                    work_intervals.append((max(1, mos) if mos >= 0 else 0, is_pres, near_co))

        if not work_intervals:
            for sm_m in _SINCE_RE.finditer(post_exp_text):
                abs_pos = (exp_idx.end() + sm_m.start()) if exp_idx else sm_m.start()
                line_ctx = _line_around(abs_pos)
                if _EDU_RANGE_SKIP_RE.search(line_ctx):
                    continue
                p_start = _parse_ym_token(sm_m.group(1), is_end=False)
                if p_start:
                    sy, sm, _ = p_start
                    mos = (ref_year - sy) * 12 + (ref_month - sm)
                    if 0 <= mos <= 45 * 12:
                        work_intervals.append((max(1, mos), True, True))
                        break

        # Choose current company interval and sum of previous intervals
        curr_mos = 0
        curr_is_pres = False
        prev_mos_sum = 0
        if work_intervals:
            # Prefer interval near current_company, then Present interval, then first interval
            chosen_idx = 0
            for idx_w, (_, is_p, near_c) in enumerate(work_intervals):
                if near_c and is_p:
                    chosen_idx = idx_w
                    break
            else:
                for idx_w, (_, is_p, near_c) in enumerate(work_intervals):
                    if near_c or is_p:
                        chosen_idx = idx_w
                        break
            curr_mos, curr_is_pres, _ = work_intervals[chosen_idx]
            prev_mos_sum = sum(m for i, (m, _, _) in enumerate(work_intervals) if i != chosen_idx)

        has_prev_companies = bool(previous_list)

        if not has_prev_companies:
            # Single-company candidate: tenure in their only company cannot exceed Total Years of Experience
            if total_exp_months > 0:
                if curr_mos <= 0 or curr_mos > total_exp_months or (curr_is_pres and abs(curr_mos - total_exp_months) <= 14):
                    curr_mos = total_exp_months
            if curr_mos > 0:
                data["Years Worked in Current Company"] = _fmt_ym(curr_mos)
        else:
            # Multi-company candidate: reconcile against Total Years of Experience and previous company durations
            if total_exp_months > 0:
                if prev_mos_sum > 0 and (curr_mos <= 0 or (curr_is_pres and (curr_mos + prev_mos_sum) > total_exp_months)):
                    rem = total_exp_months - prev_mos_sum
                    if rem > 0:
                        curr_mos = rem
                if curr_mos > total_exp_months:
                    curr_mos = max(6, total_exp_months - prev_mos_sum) if (0 < prev_mos_sum < total_exp_months) else total_exp_months
            if curr_mos > 0:
                data["Years Worked in Current Company"] = _fmt_ym(curr_mos)
            elif total_exp_months > 0 and prev_mos_sum == 0 and total_exp_months <= 18:
                data["Years Worked in Current Company"] = _fmt_ym(total_exp_months)

        # If Total Years of Experience was still "Not Provided" but we found valid employment intervals, populate it!
        if data["Total Years of Experience"] == "Not Provided" and (curr_mos + prev_mos_sum) > 0:
            total_exp_months = curr_mos + prev_mos_sum
            data["Total Years of Experience"] = _fmt_ym(total_exp_months)

    return data


def process_file(file_info):
    """Process a single file. file_info can be str, (file_path, display_name), or (file_path, display_name, ocr_deadline)."""
    import time
    ocr_deadline = None
    if isinstance(file_info, (tuple, list)):
        file_path = str(file_info[0])
        display_name = os.path.basename(str(file_info[1]).replace("\\", "/"))
        if len(file_info) >= 3 and file_info[2] is not None:
            ocr_deadline = float(file_info[2])
    else:
        file_path = str(file_info)
        display_name = os.path.basename(file_path.replace("\\", "/"))

    allow_ocr = (ocr_deadline is None) or (time.time() < ocr_deadline)

    try:
        suffix = os.path.splitext(file_path)[1].lower()
        if suffix == ".docx":
            extracted_text, hidden_emails = extract_docx_data(file_path, allow_ocr=allow_ocr)
        elif suffix == ".txt":
            try:
                from .parser import parse_txt
                extracted_text, hidden_emails = parse_txt(file_path), []
            except Exception:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    extracted_text, hidden_emails = f.read(), []
        elif suffix == ".doc":
            try:
                from .parser import parse_doc
                extracted_text, hidden_emails = parse_doc(file_path), []
            except Exception:
                extracted_text, hidden_emails = "", []
        else:
            extracted_text, hidden_emails = extract_pdf_data(file_path, allow_ocr=allow_ocr)

        res = parse_text(extracted_text, hidden_emails, display_name)

        if not res.get('Candidate Name') or res.get('Candidate Name') == "Not Provided":
            clean_name = sanitize_filename(display_name)
            res['Candidate Name'] = clean_name if clean_name else "Candidate"

        return res
    except Exception as e:
        res = {k: "Not Provided" for k in FINAL_HEADERS}
        res['File Name'] = display_name
        res['Candidate Name'] = f"Error: {str(e)[:30]}"
        return res


_EXTRACTOR_POOL = None
_EXTRACTOR_POOL_WORKERS = 0


def _get_extractor_pool(max_workers: int):
    try:
        from .orchestrator import _get_pool
        return _get_pool(max_workers)
    except Exception:
        pass
    global _EXTRACTOR_POOL, _EXTRACTOR_POOL_WORKERS
    from concurrent.futures import ProcessPoolExecutor
    broken = _EXTRACTOR_POOL is not None and getattr(_EXTRACTOR_POOL, "_broken", False)
    if _EXTRACTOR_POOL is None or broken or _EXTRACTOR_POOL_WORKERS < max_workers:
        if _EXTRACTOR_POOL is not None:
            try:
                _EXTRACTOR_POOL.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
        try:
            from .worker import watch_parent
            _EXTRACTOR_POOL = ProcessPoolExecutor(
                max_workers=max_workers,
                initializer=watch_parent,
                initargs=(os.getpid(),),
            )
        except Exception:
            _EXTRACTOR_POOL = ProcessPoolExecutor(max_workers=max_workers)
        _EXTRACTOR_POOL_WORKERS = max_workers
    return _EXTRACTOR_POOL


def process_all(files: List[Any], progress_cb=None) -> List[Dict[str, Any]]:
    if not files:
        return []

    import time
    total = len(files)
    # Hard 120-second OCR budget across the batch so 1,000 resumes always finish within ~2 to 2.5 minutes
    ocr_deadline = (time.time() + 120.0) if total >= 20 else None
    normalized = []
    for f in files:
        if isinstance(f, (tuple, list)):
            normalized.append((str(f[0]), str(f[1]), ocr_deadline))
        else:
            normalized.append((str(f), str(f), ocr_deadline))

    results = [None] * total
    max_workers = min(8, max(4, (os.cpu_count() or 8) - 2))

    if total < 5:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            done_cnt = 0
            for idx, res in enumerate(executor.map(process_file, normalized)):
                results[idx] = res
                done_cnt += 1
                if progress_cb:
                    progress_cb(done_cnt, total)
    else:
        pool = _get_extractor_pool(max_workers)
        start_t = time.perf_counter()
        target_sec_per_file = 0.12 if total >= 20 else 0.0
        futures = [pool.submit(process_file, item) for item in normalized]
        done_cnt = 0
        # Iterate in submission order (not as_completed) so progress is uniform from 1..total with no tail stall
        for idx, fut in enumerate(futures):
            try:
                results[idx] = fut.result()
            except Exception as e:
                item = normalized[idx]
                dname = os.path.basename(item[1])
                res = {k: "Not Provided" for k in FINAL_HEADERS}
                res['File Name'] = dname
                res['Candidate Name'] = f"Error: {str(e)[:30]}"
                results[idx] = res
            done_cnt += 1
            if target_sec_per_file > 0 and (done_cnt % 4 == 0 or done_cnt == total):
                expected_elapsed = done_cnt * target_sec_per_file
                actual_elapsed = time.perf_counter() - start_t
                if actual_elapsed < expected_elapsed and actual_elapsed < 130.0:
                    time.sleep(min(0.20, expected_elapsed - actual_elapsed))
            if progress_cb and (done_cnt % 2 == 0 or done_cnt == total):
                progress_cb(done_cnt, total)

    return results


def extract_from_folder(folder_path: str, progress_cb=None) -> List[Dict[str, Any]]:
    """Extract candidate records from an entire directory path on disk."""
    files = get_files(folder_path)
    return process_all(files, progress_cb=progress_cb)


def build_excel_bytes(results: List[Dict[str, Any]]) -> bytes:
    """Generate a professionally styled Excel workbook and return raw bytes."""
    import pandas as pd
    from openpyxl import load_workbook
    from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    if results:
        df = pd.DataFrame(results)[FINAL_HEADERS]
    else:
        df = pd.DataFrame(columns=FINAL_HEADERS)

    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)

    bio.seek(0)
    wb = load_workbook(bio)
    ws = wb.active

    header_fill = PatternFill(start_color="171717", end_color="171717", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True, size=11)
    center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)

    thin_border = Border(
        left=Side(style='thin', color='D4D4D4'),
        right=Side(style='thin', color='D4D4D4'),
        top=Side(style='thin', color='D4D4D4'),
        bottom=Side(style='thin', color='D4D4D4')
    )

    # Format Headers
    for col_num, cell in enumerate(ws[1], 1):
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center_align
        cell.border = thin_border
        col_letter = get_column_letter(col_num)
        ws.column_dimensions[col_letter].width = 24

    # Format Data rows
    stripe_fill = PatternFill(start_color="F5F5F5", end_color="F5F5F5", fill_type="solid")
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, max_row=ws.max_row, max_col=ws.max_column), 2):
        for cell in row:
            cell.border = thin_border
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            if row_idx % 2 == 0:
                cell.fill = stripe_fill

    out = BytesIO()
    wb.save(out)
    return out.getvalue()


# Library module for ATS and Resume Extractor integration




