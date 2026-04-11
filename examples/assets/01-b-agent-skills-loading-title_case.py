#!/usr/bin/env python3
from __future__ import annotations

import argparse


def to_title_case(value: str) -> str:
    return " ".join(word[:1].upper() + word[1:].lower() for word in value.split())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", required=True)
    args = parser.parse_args()
    print(to_title_case(args.text))


if __name__ == "__main__":
    main()
