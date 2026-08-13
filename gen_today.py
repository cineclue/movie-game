"""One-off: generate today's puzzle (loosens filters automatically until one is created)."""
import datetime
from dotenv import load_dotenv
load_dotenv()
import puzzle_generator as pg

today = datetime.date.today()
used = pg.fetch_used_ids()
pg.generate_for_date(today.isoformat(), used, str(today.year))
