import argparse
import json
import sys
from pathlib import Path
from providers import read_prices
from config_io import CONFIG_PATH, parse_simple_yaml


def main() -> int:
    parser = argparse.ArgumentParser(description="Preise auslesen")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="Pfad zur config.yaml")
    args = parser.parse_args()

    try:
        config = parse_simple_yaml(Path(args.config))
        result = read_prices(config)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
