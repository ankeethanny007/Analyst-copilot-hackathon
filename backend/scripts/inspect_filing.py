import argparse
import json
from pathlib import Path

from app.services.html_xbrl import parse_html_xbrl


parser = argparse.ArgumentParser(description="Normalize an HTML/Inline XBRL filing for inspection.")
parser.add_argument("filing", type=Path)
parser.add_argument("--output", type=Path)
args = parser.parse_args()

extract = parse_html_xbrl(args.filing)
print(json.dumps(extract.summary(), indent=2))
if args.output:
    args.output.write_text(json.dumps(extract.to_dict(), indent=2))
