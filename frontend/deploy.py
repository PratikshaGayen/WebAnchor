"""Deploy both demo contracts to the local GenLayer Studio and record addresses.

The frontend needs two fresh contract addresses to talk to, and Studio's chain
state is wiped whenever the stack is recreated, so this has to be re-runnable.
It reuses gltest's deployment machinery (the same code path
``tests/integration/`` already exercises) rather than reimplementing GenVM's
multi-file archive packaging in JavaScript -- ``anchored_reader_multi`` is a
package with a sibling ``webanchor.py``, and gltest already knows how to zip
that up correctly.

gltest normally gets its configuration from its pytest plugin. There is no
pytest session here, so the config is populated by hand below with the same
defaults the plugin would have used (localnet, ``contracts/``, ``artifacts/``).

    python frontend/deploy.py

Writes frontend/src/contracts.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_FILE = Path(__file__).resolve().parent / "src" / "contracts.json"

from gltest_cli.config.general import get_general_config  # noqa: E402
from gltest_cli.config.types import PluginConfig  # noqa: E402
from gltest_cli.config.user import get_default_user_config  # noqa: E402


def configure() -> None:
    config = get_general_config()
    config.user_config = get_default_user_config()
    config.plugin_config = PluginConfig(
        contracts_dir=REPO_ROOT / "contracts",
        artifacts_dir=REPO_ROOT / "artifacts",
        network_name="localnet",
        chain_type="localnet",
    )


def main() -> int:
    configure()

    # Imported after configure() so the module-level client setup sees the
    # populated config.
    from gltest import get_contract_factory

    targets = {
        "naive": "naive_reader.py",
        "anchored": "anchored_reader_multi/__init__.py",
    }

    addresses = {}
    for label, path in targets.items():
        print(f"deploying {label} ({path}) ...", flush=True)
        factory = get_contract_factory(contract_file_path=path)
        contract = factory.deploy(args=[])
        addresses[label] = str(contract.address)
        print(f"  {label}: {addresses[label]}", flush=True)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8", newline="") as handle:
        json.dump(
            {
                "naiveWebReader": addresses["naive"],
                "anchoredWebReader": addresses["anchored"],
            },
            handle,
            indent=2,
        )
        handle.write("\n")

    print(f"wrote {OUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
