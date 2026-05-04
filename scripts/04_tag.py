#!/usr/bin/env python3
"""
Quote Tagger — Auto-detect subtopics in quotes
================================================
Scans quote text and context for recurring topics and adds a
`tags` array to each quote. Tags enable drill-down filtering
on the visualization (e.g. "suchu dance", "uber", "hurricane").

Designed to work out of the box with common email topics, but
you can add your own custom tags for topics specific to your life.

Usage:
  python scripts/04_tag.py                          # auto-tag quotes
  python scripts/04_tag.py --preview                # show tags without saving
  python scripts/04_tag.py --add "yoga:yoga,asana"  # add custom tag with keywords
  python scripts/04_tag.py --min-count 3            # only keep tags with 3+ matches

Output:
  data/quotes.json  — updated with `tags` array on every quote

Custom tags:
  You can add tags specific to your own life. Each tag is a name
  and a comma-separated list of keywords to search for:

    python scripts/04_tag.py --add "my band:rehearsal,gig,setlist"
    python scripts/04_tag.py --add "camping:campsite,tent,hiking,trail"

  Multiple --add flags can be used at once.
"""

import json
import sys
import argparse
from pathlib import Path
from collections import Counter

DATA_DIR = Path(__file__).parent.parent / 'data'
QUOTES_FILE = DATA_DIR / 'quotes.json'


# ═══════════════════════════════════════════════
# DEFAULT TAG DEFINITIONS
# ═══════════════════════════════════════════════
# Each tag has a list of keywords. If any keyword appears in
# the quote's text or context (case-insensitive), the tag is applied.
# These cover common email life topics — add your own with --add.

DEFAULT_TAGS = {
    # Arts & Culture
    'dance performance': ['performance', 'choreograph', 'rehearsal'],
    'theater': ['theater', 'theatre', 'play', 'stage'],
    'gallery': ['gallery', 'exhibit', 'exhibition'],
    'documentary': ['documentary', 'film'],
    'printmaking': ['printmak', 'etching', 'press', 'mosaic'],

    # Career
    'job search': ['resume', 'application', 'interview', 'hiring', 'position'],
    'freelance': ['freelance', 'contract', 'client'],
    'graphic design': ['design', 'logo', 'poster', 'flyer'],
    'uber': ['uber'],
    'doordash': ['doordash'],

    # Family & Social
    'wedding': ['wedding', 'engagement', 'married'],
    'baby': ['baby', 'pregnant', 'shower'],
    'birthday': ['birthday'],
    'dinner plans': ['dinner', 'restaurant', 'potluck'],
    'game night': ['game night'],
    'rodeo': ['rodeo'],

    # Community
    'volunteering': ['volunteer'],
    'bake sale': ['bake sale'],

    # Internet & Tech
    'facebook': ['facebook', 'wall post', 'tagged'],
    'livejournal': ['livejournal'],
    'craigslist': ['craigslist'],

    # Financial
    'debt': ['debt', 'collection', 'overdue'],
    'bills': ['bill', 'payment', 'declined'],

    # Crisis
    'hurricane': ['hurricane', 'katrina', 'harvey', 'rita', 'ike', 'evacuee'],
    'covid': ['covid', 'pandemic', 'mask', 'vaccine'],
    'cancer': ['cancer', 'diagnosis'],

    # Student
    'student life': ['student', 'university', 'campus', 'quiz', 'exam',
                     'semester', 'professor', 'instructor', 'grade'],

    # Cultural
    'diwali': ['diwali'],
    'dharma': ['dharma', 'hindu', 'vedas', 'mandir', 'puja'],

    # Food (mundane beautiful)
    'food': ['taco', 'pizza', 'lobster', 'shrimp', 'burger',
             'cheeseburger', 'turkey', 'cheese'],

    # Entertainment / Nostalgia
    'redbox': ['redbox'],
    'blockbuster': ['blockbuster'],
    'netflix': ['netflix'],
}


def tag_quotes(quotes, tag_defs, min_count=2):
    """Apply tags to quotes based on keyword matching."""

    # Tag every quote
    for q in quotes:
        blob = (q.get('text', '') + ' ' + q.get('context', '')).lower()
        tags = []
        for tag, keywords in tag_defs.items():
            if any(kw in blob for kw in keywords):
                tags.append(tag)
        q['tags'] = tags

    # Count frequencies
    counts = Counter()
    for q in quotes:
        for t in q['tags']:
            counts[t] += 1

    # Remove tags below min_count
    low_tags = {t for t, c in counts.items() if c < min_count}
    for q in quotes:
        q['tags'] = [t for t in q['tags'] if t not in low_tags]

    # Rebuild counts after removal
    final_counts = Counter()
    for q in quotes:
        for t in q['tags']:
            final_counts[t] += 1

    return final_counts


def main():
    p = argparse.ArgumentParser(
        description='Auto-tag quotes with subtopics for drill-down filtering',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python 04_tag.py                                    # standard tagging
  python 04_tag.py --preview                          # dry run
  python 04_tag.py --add "my band:rehearsal,gig"      # custom tag
  python 04_tag.py --add "camping:tent,trail,hike"    # another custom tag
  python 04_tag.py --min-count 3                      # minimum 3 matches
""")
    p.add_argument('--input', '-i', default=str(QUOTES_FILE))
    p.add_argument('--preview', action='store_true',
                   help='Show tags without saving')
    p.add_argument('--add', action='append', default=[],
                   help='Add custom tag as "name:keyword1,keyword2,..."')
    p.add_argument('--min-count', type=int, default=2,
                   help='Minimum matches to keep a tag (default: 2)')
    args = p.parse_args()

    path = Path(args.input)
    if not path.exists():
        sys.exit(f'File not found: {path}')

    # Build tag definitions
    tag_defs = dict(DEFAULT_TAGS)

    # Add custom tags
    for custom in args.add:
        if ':' not in custom:
            print(f'  Skipping invalid tag (use name:kw1,kw2): {custom}')
            continue
        name, kws = custom.split(':', 1)
        keywords = [k.strip().lower() for k in kws.split(',') if k.strip()]
        if keywords:
            tag_defs[name.strip().lower()] = keywords
            print(f'  Added custom tag: {name.strip()} ({len(keywords)} keywords)')

    print(f'\n  Quote Tagger')
    print(f'  Input: {path}')
    print(f'  Tags defined: {len(tag_defs)}')
    print(f'  Min count: {args.min_count}')

    with open(path, encoding='utf-8') as f:
        data = json.load(f)

    quotes = data.get('quotes', [])
    print(f'  Quotes loaded: {len(quotes)}')

    # Tag
    counts = tag_quotes(quotes, tag_defs, args.min_count)

    tagged = sum(1 for q in quotes if q.get('tags'))
    print(f'\n  Results:')
    print(f'    Quotes with tags: {tagged}/{len(quotes)}')
    print(f'    Active tags: {len(counts)}')
    print(f'\n  Tag counts:')
    for tag, count in counts.most_common():
        print(f'    {tag:>20}: {count}')

    if args.preview:
        print(f'\n  PREVIEW — nothing saved')
        return

    data['quotes'] = quotes

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f'\n  Saved to {path}')
    print(f'  Done! Tags are ready for the visualization.\n')


if __name__ == '__main__':
    main()
