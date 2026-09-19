"""Collect WHOOP entities and a separate daily report; preserve legacy CSVs."""

import argparse
from datetime import date

from whoop_auth import AuthError
from whoop_fetch import APIError, WhoopClient
from whoop_entities import collect_history, save_history


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end-date", type=date.fromisoformat, default=None,
                        help="Last reporting date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, default=30, help="Reporting window, minimum 30 days")
    args = parser.parse_args(argv)
    try:
        if args.days < 30:
            raise APIError("History requires at least 30 days.")
        collection = collect_history(WhoopClient(), args.end_date, args.days)
        counts = save_history(collection)
    except (AuthError, APIError) as error:
        print("WHOOP history failed:", error)
        return 1
    except OSError:
        print("WHOOP history failed: could not read or save local files. Check file permissions.")
        return 1
    except KeyboardInterrupt:
        print("\nWHOOP history cancelled.")
        return 1
    print(f"Saved report for {collection['first']} through {collection['last']} to data/daily_metrics.csv.")
    print("Stored " + ", ".join(f"{n} {kind}" for kind, n in counts.items()) + ".")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
