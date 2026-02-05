from __future__ import annotations

import argparse

from app.state.db import init_db
from app.state.repository import StateRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a learner record")
    parser.add_argument("learner_id")
    parser.add_argument("--timezone", default="UTC")
    args = parser.parse_args()

    init_db()
    repo = StateRepository()
    repo.ensure_learner(args.learner_id, timezone_name=args.timezone)
    print(f"Seeded learner '{args.learner_id}'")


if __name__ == "__main__":
    main()
