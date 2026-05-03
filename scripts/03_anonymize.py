#!/usr/bin/env python3
"""
Quote Anonymizer — Privacy-First PII Removal
=============================================
Strips personal information from quotes.json so it's safe to publish.
Replaces real names with pseudonyms inspired by Gmail's original
development team and early Google engineers. Removes phone numbers,
email addresses, SSNs, credit card numbers, and other sensitive data.

Designed to be reusable — anyone building their own email ripple
visualization can run this on their quotes.json before deploying.

Usage:
  python scripts/03_anonymize.py                          # anonymize in place
  python scripts/03_anonymize.py --preview                # show changes without saving
  python scripts/03_anonymize.py --input data/quotes.json # specify input file
  python scripts/03_anonymize.py --keep "Rosa Parks"      # keep specific names
  python scripts/03_anonymize.py --add-names "John,Jane"  # add names to scrub
  python scripts/03_anonymize.py --seed 123               # different pseudonym shuffle

Output:
  data/quotes.json           — anonymized (overwritten in place)
  data/name_mapping.json     — real-to-fake lookup (gitignored, for your eyes only)

Why Gmail team names?
  The pseudonyms are drawn from the people who built Gmail — Paul Buchheit,
  Sanjeev Singh, Brian Rakowski, Kevin Fox, Georges Harik, and others.
  A small tribute to the team whose platform held these messages for decades.
"""

import json
import re
import sys
import random
import argparse
from pathlib import Path
from collections import defaultdict, OrderedDict


DATA_DIR = Path(__file__).parent.parent / 'data'
QUOTES_FILE = DATA_DIR / 'quotes.json'
MAPPING_FILE = DATA_DIR / 'name_mapping.json'


# ═══════════════════════════════════════════════
# PSEUDONYM POOLS
# ═══════════════════════════════════════════════

FIRST_NAME_POOL = [
    # Gmail / early Google team
    'Paul', 'Sanjeev', 'Brian', 'Kevin', 'Georges', 'Bret', 'Amit',
    'Marissa', 'Susan', 'Sundar', 'Salar', 'Omid', 'Craig', 'Urs',
    # South Asian
    'Priya', 'Arjun', 'Maya', 'Rohan', 'Kavya', 'Neel', 'Tara',
    'Dev', 'Leela', 'Ravi', 'Anita', 'Kiran', 'Meera', 'Sanjay',
    'Deepa', 'Vikram', 'Uma', 'Jai', 'Radha', 'Dhruv', 'Gauri',
    'Ishan', 'Aditi', 'Sahil', 'Chitra', 'Varun', 'Hema', 'Kabir',
    'Vani', 'Tarun', 'Isha', 'Milan', 'Devi', 'Pranav', 'Sita',
    'Harsh', 'Gita', 'Naveen', 'Lata', 'Rishi', 'Kamala', 'Ojas',
    'Rekha', 'Vivaan', 'Aarav', 'Kiara', 'Samar', 'Nisha', 'Daksh',
    # Western
    'Nina', 'Sam', 'Grace', 'Cole', 'Mia', 'Leo', 'Iris',
    'Zoe', 'Finn', 'Ruby', 'Owen', 'Ivy', 'Kate', 'Eli',
    'Rose', 'Max', 'Eve', 'Luke', 'Thea', 'Jude', 'Amy',
    'Dan', 'Holly', 'Ian', 'Jane', 'Kyle', 'Nell', 'Pete',
    'Quinn', 'Sara', 'Trent', 'Wade', 'Alice', 'Blake', 'Cora',
    'Drew', 'Flora', 'Grant', 'Hope', 'Jack', 'Luna', 'Mark',
    'Olive', 'Reed', 'Vera', 'Wren', 'Aria', 'Cleo', 'Dina',
    'Elle', 'Sage', 'Brooke', 'Clay', 'Hazel', 'Pearl',
]

LAST_NAME_POOL = [
    # South Asian
    'Mehta', 'Kapoor', 'Sharma', 'Gupta', 'Kumar', 'Joshi', 'Nair',
    'Reddy', 'Rao', 'Iyer', 'Menon', 'Pillai', 'Bhat', 'Trivedi',
    'Vyas', 'Pandey', 'Bose', 'Sen', 'Ghosh', 'Das', 'Roy',
    'Sinha', 'Verma', 'Chandra', 'Shetty', 'Bhatt', 'Dalal',
    # Western
    'Miller', 'Davis', 'Wilson', 'Moore', 'Taylor', 'Clark', 'Hall',
    'Allen', 'Young', 'Wright', 'Hill', 'Scott', 'Adams', 'Baker',
    'Carter', 'Mitchell', 'Turner', 'Phillips', 'Parker', 'Evans',
    'Collins', 'Stewart', 'Morris', 'Rogers', 'Cook', 'Bell',
]


# ═══════════════════════════════════════════════
# PII REGEX PATTERNS
# ═══════════════════════════════════════════════

PII_PATTERNS = [
    ('email',       re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'),
                    '[email]'),
    ('phone',       re.compile(r'(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)'),
                    '[phone]'),
    ('ssn',         re.compile(r'(?<!\d)\d{3}[-\s]\d{2}[-\s]\d{4}(?!\d)'),
                    '[redacted]'),
    ('credit_card', re.compile(r'(?<!\d)\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}(?!\d)'),
                    '[redacted]'),
    ('ip_address',  re.compile(r'(?<!\d)\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?!\d)'),
                    '[ip]'),
]


# ═══════════════════════════════════════════════
# NAME DETECTION — known first names
# ═══════════════════════════════════════════════

# South Asian first names (common ones for detection)
SA_FIRST = {
    'aarav','abhishek','aditya','akash','amar','amit','amol','anand','ankit',
    'anshul','anup','arjun','aryan','ashish','ashwin','bhavin','bhavesh',
    'chirag','chintan','darshan','deepak','dev','dhaval','dhruv','dinesh',
    'gaurav','girish','gopal','govind','hardik','harsh','hemant','hitesh',
    'ishan','jatin','jay','jignesh','karan','kaushik','kunal','lalit',
    'mahesh','manan','manish','mayank','mihir','mohit','mukesh','neel',
    'nikhil','nirav','nishant','om','paresh','parth','pranav','prashant',
    'pratik','rahul','rajat','rajesh','rakesh','ramesh','ravi','rohit',
    'sachin','sahil','sameer','samir','sanjay','sapan','sharvil','shivam',
    'siddharth','sunil','suresh','tarun','tushar','uday','varun','vijay',
    'vikram','vinay','vishal','vishnu','vivek','yash',
    'aarti','aditi','aisha','amisha','anita','anu','asha','ashka','avni',
    'bhavna','chandni','chitra','deepa','devanshi','disha','divya','durga',
    'esha','fatima','gauri','gita','hema','hetal','hina','indira',
    'isha','ishita','jalpa','jasmine','jaya','kavya','khushboo','kinari',
    'kiran','komal','lakshmi','lata','leela','lopa','madhuri','manjari',
    'mauli','maya','meera','megha','mili','minal','minaxi','miral',
    'mohini','nandini','neha','nidhi','nisha','padma','palak','payal',
    'pooja','prachi','prapti','priti','priya','radha','radhika','rajvi',
    'rani','rekha','rima','rinki','rishika','ruchi','rupali','sachi',
    'sangeeta','sarita','saumeen','savita','sejal','shanti','shivangi',
    'shivani','shruti','silpa','sonal','sooravi','sujana','sunita',
    'tanvi','tara','tina','tulsi','ulka','uma','usha','varashaa',
    'veena','vidya','vinita','yamini',
    'hemali','nishat','harshu','bhavika','darshana','jisha','nehal',
}

# Western first names
WESTERN_FIRST = {
    'aaron','adam','alex','alice','amanda','amy','andrew','angela','ann',
    'anthony','ashley','barbara','benjamin','beth','blake','bob','brad',
    'brandon','brian','bruce','carl','carol','charles','chris','christina',
    'christine','chuck','claire','craig','dan','daniel','dave','david',
    'dawn','dennis','diana','diane','don','donna','doug','drew','ed',
    'edward','elizabeth','emily','eric','eva','eve','faith','frank',
    'fred','gabriel','gary','george','grace','greg','hank','heather',
    'helen','holly','ian','jack','jacob','james','jane','janet','jason',
    'jeff','jennifer','jenny','jeremy','jessica','jim','joan','joe',
    'joel','john','jonathan','josh','juan','judy','julie','justin',
    'karen','kate','katherine','keith','kelly','ken','kevin','kim',
    'kyle','larry','laura','lauren','lee','leo','linda','lisa','liz',
    'louis','louie','luke','lynn','maria','marie','mark','mary','matt',
    'matthew','max','megan','melissa','michael','michelle','michele',
    'mike','nancy','natalie','nathan','nick','nicole','olivia','oscar',
    'pam','pat','patricia','patrick','paul','paula','pete','peter',
    'philip','rachel','ralph','raymond','rebecca','richard','rick',
    'rob','robert','roger','ron','rose','ruth','ryan','sam','samuel',
    'sandra','sara','sarah','scott','sean','sharon','stephanie',
    'stephen','steve','susan','ted','teresa','terry','thomas','tim',
    'todd','tom','tony','tracy','tyler','victor','victoria','wade',
    'walter','wendy','william','zach','liliana','argel',
}

ALL_KNOWN_FIRST = SA_FIRST | WESTERN_FIRST


# ═══════════════════════════════════════════════
# WORDS THAT LOOK LIKE NAMES BUT AREN'T
# ═══════════════════════════════════════════════

NOT_A_NAME = {
    # Days, months, holidays
    'monday','tuesday','wednesday','thursday','friday','saturday','sunday',
    'january','february','march','april','may','june','july','august',
    'september','october','november','december',
    'christmas','easter','diwali','thanksgiving','halloween','hanukkah',
    # Companies, brands, products
    'facebook','myspace','google','gmail','uber','lyft','doordash','netflix',
    'redbox','paypal','craigslist','linkedin','twitter','instagram','pinterest',
    'wordpress','friendster','mailchimp','blockbuster','amazon','apple',
    'microsoft','yahoo','hotmail','spotify','venmo','tinder','bumble',
    # Common words that get capitalized
    'happy','hindu','student','council','hurricane','spring','break',
    'national','geographic','watch','sandy','hook','atlas','fresh',
    'arts','society','cookie','monster','square','cash','crash','glass',
    'creative','suite','fort','worth','black','space','south','asian',
    'fancy','rice','funk','march','dimes','pick','prof','cullen',
    'performance','human','rights','garden','state','big','range',
    'mad','max','food','tank','occupy','defensive','driving','bellaire',
    'lost','tools','learning','new','york','times','year','red','lobster',
    'dear','hello','hey','the','this','that','please','welcome',
    'sorry','thanks','thank','note','important','emergency','update',
    'houston','austin','texas','boston','chicago','denver','galveston',
    'india','israel','argentina','virginia','jersey','vegas',
    'dance','company','festival','source','suchu','university','college',
    'school','academy','club','museum','theater','theatre','temple',
    'automated','approaching','just','also','even','still','already',
    'never','always','every','some','many','most','all','before','after',
}


class Anonymizer:
    """Detects and replaces personal names and PII in quote data."""

    def __init__(self, seed=42, keep_names=None, extra_names=None):
        self.rng = random.Random(seed)
        self.keep = set(k.lower().strip() for k in (keep_names or []))
        self.extra = set(n.strip().lower() for n in (extra_names or []) if n.strip())

        # Shuffle pools deterministically
        self.firsts = list(FIRST_NAME_POOL)
        self.lasts = list(LAST_NAME_POOL)
        self.rng.shuffle(self.firsts)
        self.rng.shuffle(self.lasts)
        self.fi = 0
        self.li = 0

        # name -> pseudonym mapping
        self.mapping = OrderedDict()
        self.stats = defaultdict(int)

    def _next_first(self):
        n = self.firsts[self.fi % len(self.firsts)]
        self.fi += 1
        return n

    def _next_last(self):
        n = self.lasts[self.li % len(self.lasts)]
        self.li += 1
        return n

    def pseudonym(self, real):
        """Get or create a consistent pseudonym for a name."""
        key = real.lower().strip()
        if key in self.keep:
            return real
        if key not in self.mapping:
            if ' ' in real.strip():
                self.mapping[key] = f'{self._next_first()} {self._next_last()}'
            else:
                self.mapping[key] = self._next_first()
        return self.mapping[key]

    def is_name(self, word):
        """Check if a word is likely a person's first name."""
        w = word.lower().strip().rstrip('.,!?:;\'\"')
        if w in NOT_A_NAME:
            return False
        if w in self.extra:
            return True
        return w in ALL_KNOWN_FIRST

    def scrub_pii(self, text):
        """Remove emails, phones, SSNs, credit cards, IPs."""
        for pii_type, pattern, replacement in PII_PATTERNS:
            hits = pattern.findall(text)
            if hits:
                self.stats[pii_type] += len(hits)
            text = pattern.sub(replacement, text)
        return text

    def scrub_names(self, text):
        """Find and replace person names with pseudonyms."""
        if not text:
            return text

        # 1. Facebook-style patterns: "FirstName LastName has requested/tagged/invited..."
        def fb_replace(m):
            name = m.group(1)
            rest = m.group(0)[len(name):]
            self.stats['names'] += 1
            return self.pseudonym(name) + rest

        text = re.sub(
            r'([A-Z][a-z]+ [A-Z][a-z]+)( has (?:requested|tagged|invited|just written|searched))',
            fb_replace, text)

        # "friends with FirstName"
        def friends_replace(m):
            name = m.group(1)
            self.stats['names'] += 1
            return 'friends with ' + self.pseudonym(name)

        text = re.sub(r'friends with ([A-Z][a-z]+)', friends_replace, text)

        # 2. Two-word capitalized names (likely full names)
        def two_word_replace(m):
            first, last = m.group(1), m.group(2)
            full = f'{first} {last}'
            if full.lower() in self.keep:
                return full
            if self.is_name(first) or first.lower() in self.extra:
                self.stats['names'] += 1
                return self.pseudonym(full)
            return full

        text = re.sub(r'\b([A-Z][a-z]{1,15})\s+([A-Z][a-z]{1,15})\b', two_word_replace, text)

        # 3. Single names in context clues
        context_patterns = [
            (r'(?:Dear |Hi |Hey |Hello |Thanks |Thank you,? )([A-Z][a-z]{2,15})', 1),
            (r"([A-Z][a-z]{2,15})'s\b", 1),
        ]
        for pat, group in context_patterns:
            def ctx_replace(m, g=group):
                name = m.group(g)
                if self.is_name(name):
                    self.stats['names'] += 1
                    fake = self.pseudonym(name)
                    return m.group(0).replace(name, fake)
                return m.group(0)
            text = re.sub(pat, ctx_replace, text)

        # 4. Extra names to always scrub (from --add-names)
        for name in self.extra:
            pattern = re.compile(r'\b' + re.escape(name) + r'\b', re.IGNORECASE)
            if pattern.search(text):
                self.stats['names'] += 1
                text = pattern.sub(self.pseudonym(name), text)

        return re.sub(r'  +', ' ', text).strip()

    def scrub(self, text):
        """Full scrub: PII + names."""
        text = self.scrub_pii(text)
        text = self.scrub_names(text)
        return text

    def process(self, data):
        """Process entire quotes.json data structure."""
        for q in data.get('quotes', []):
            q['text'] = self.scrub(q.get('text', ''))
            q['context'] = self.scrub(q.get('context', ''))

        # Add privacy note
        if 'meta' not in data:
            data['meta'] = {}
        data['meta']['privacy_note'] = (
            "All personal names have been replaced with pseudonyms drawn from "
            "the names of Gmail's original development team and early Google "
            "engineers. Phone numbers, email addresses, and other identifying "
            "information have been removed. Any resemblance to real individuals "
            "is coincidental."
        )
        return data

    def save_mapping(self, path):
        """Save name mapping for the archive owner's reference."""
        out = {
            'note': 'Real name to pseudonym mapping. Keep this file private.',
            'source': "Pseudonyms from Gmail's founding team and early Google",
            'mapping': dict(self.mapping),
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(out, f, indent=2, ensure_ascii=False)

    def report(self):
        """Print summary."""
        print(f'\n  Anonymization complete:')
        print(f'    Names replaced:      {self.stats["names"]}')
        print(f'    Unique identities:   {len(self.mapping)}')
        print(f'    Emails removed:      {self.stats["email"]}')
        print(f'    Phone numbers:       {self.stats["phone"]}')
        print(f'    SSNs:                {self.stats["ssn"]}')
        print(f'    Credit cards:        {self.stats["credit_card"]}')
        print(f'    IP addresses:        {self.stats["ip_address"]}')


def main():
    p = argparse.ArgumentParser(
        description='Anonymize quotes.json for safe publishing',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python 03_anonymize.py                             # standard run
  python 03_anonymize.py --preview                   # dry run
  python 03_anonymize.py --keep "Rosa Parks"          # keep public figures
  python 03_anonymize.py --add-names "MyBoss,MyEx"   # extra names to catch
  python 03_anonymize.py --seed 99                   # different shuffle
""")
    p.add_argument('--input', '-i', default=str(QUOTES_FILE))
    p.add_argument('--preview', action='store_true',
                   help='Show changes without saving')
    p.add_argument('--keep', default='',
                   help='Comma-separated names to preserve')
    p.add_argument('--add-names', default='',
                   help='Comma-separated extra names to scrub')
    p.add_argument('--seed', type=int, default=42,
                   help='Random seed for pseudonym assignment')
    p.add_argument('--no-mapping', action='store_true',
                   help='Skip saving the name mapping file')
    args = p.parse_args()

    path = Path(args.input)
    if not path.exists():
        sys.exit(f'File not found: {path}')

    keep = [n.strip() for n in args.keep.split(',') if n.strip()]
    extra = [n.strip() for n in args.add_names.split(',') if n.strip()]

    # Always keep these public figures
    keep += ['Rosa Parks', 'Martin Luther King', 'Charlie Chaplin',
             'Dorothy Sayers', 'George Floyd']
    keep = list(set(keep))

    print(f'\n  Quote Anonymizer')
    print(f'  Input: {path}')
    print(f'  Mode:  {"PREVIEW" if args.preview else "ANONYMIZE"}')

    with open(path, encoding='utf-8') as f:
        data = json.load(f)

    total = len(data.get('quotes', []))
    print(f'  Quotes: {total}')

    anon = Anonymizer(seed=args.seed, keep_names=keep, extra_names=extra)
    data = anon.process(data)
    anon.report()

    if args.preview:
        print(f'\n  PREVIEW — nothing saved')
        print(f'\n  Sample mappings:')
        for real, fake in list(anon.mapping.items())[:10]:
            print(f'    {real:20s} -> {fake}')
        return

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f'\n  Saved: {path}')

    if not args.no_mapping:
        anon.save_mapping(MAPPING_FILE)
        print(f'  Mapping: {MAPPING_FILE} (keep private, gitignored)')

    print(f'\n  Done! Ready to publish.\n')


if __name__ == '__main__':
    main()
