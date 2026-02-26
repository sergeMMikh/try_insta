# try_insta

## Media Presence Experiment

Script `run_media_experiment.py` runs a measurable experiment:
- takes a balanced sample from accounts (default target: 100 media from 10 accounts);
- stores media in PostgreSQL via SQLAlchemy (`media_samples` table);
- calculates presence ratios for `views`, `likes`, `comments`.

### Environment variables

- `DATABASE_URL` (or `DABASE_URL`): PostgreSQL DSN, for example
  `postgresql://postgres:password@localhost:5432/insta-test`
- `META_TOKEN`: Meta Graph API token (optional when running with `--skip-fetch`)
- `EXPERIMENT_ACCOUNTS`: comma-separated accounts in format
  `account_id[:niche],account_id[:niche],...`

Example:

```env
DATABASE_URL=postgresql://postgres:password@localhost:5432/insta-test
EXPERIMENT_ACCOUNTS=17841448609549086:fitness,17841400000000000:food
```

### Run

```powershell
.\.venv\Scripts\python.exe run_media_experiment.py --sample-size 100 --account-count 10
```

If data already exists in DB and you only want to recalculate:

```powershell
.\.venv\Scripts\python.exe run_media_experiment.py --sample-size 100 --account-count 10 --skip-fetch
```
