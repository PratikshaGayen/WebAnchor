"""Deploy both demo contracts to a GenLayer Studio node and record addresses.

The frontend needs two fresh contract addresses to talk to, and Studio's chain
state is wiped whenever the stack is recreated, so this has to be re-runnable.
It reuses gltest's deployment machinery (the same code path
``tests/integration/`` already exercises) rather than reimplementing GenVM's
multi-file archive packaging in JavaScript -- ``anchored_reader_multi`` is a
package with a sibling ``webanchor.py``, and gltest already knows how to zip
that up correctly. The plain ``genlayer`` CLI is not an option here: it reads
contract files as UTF-8 text, which corrupts the zip bytes of a multi-file
contract.

gltest normally gets its configuration from its pytest plugin. There is no
pytest session here, so the config is populated by hand below with the same
defaults the plugin would have used (``contracts/``, ``artifacts/``). Both
``localnet`` and ``studionet`` are networks gltest already knows about, so the
only thing ``--network`` has to do is pick between them.

    python frontend/deploy.py                       # local Studio stack
    python frontend/deploy.py --network studionet   # hosted studio.genlayer.com

Writes frontend/src/contracts.json by default; ``--out`` points it elsewhere,
which is how the studionet addresses are kept separate from the local ones.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = Path(__file__).resolve().parent / "src"

from gltest_cli.config.general import get_general_config  # noqa: E402
from gltest_cli.config.types import PluginConfig  # noqa: E402
from gltest_cli.config.user import get_default_user_config  # noqa: E402

# gltest's own names for these. Both are preconfigured in its default user
# config, complete with generated accounts, so nothing else needs setting up.
NETWORKS = {
    "localnet": "localnet",
    "studionet": "studionet",
    "testnet_asimov": "testnet_asimov",
}


def configure(network: str) -> None:
    config = get_general_config()
    config.user_config = get_default_user_config()
    config.plugin_config = PluginConfig(
        contracts_dir=REPO_ROOT / "contracts",
        artifacts_dir=REPO_ROOT / "artifacts",
        network_name=network,
        chain_type=NETWORKS[network],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", choices=sorted(NETWORKS), default="localnet")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    out_file = args.out
    if out_file is None:
        suffix = "" if args.network == "localnet" else f".{args.network}"
        out_file = SRC_DIR / f"contracts{suffix}.json"

    configure(args.network)
    print(f"network: {args.network}", flush=True)

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

    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8", newline="") as handle:
        json.dump(
            {
                "naiveWebReader": addresses["naive"],
                "anchoredWebReader": addresses["anchored"],
            },
            handle,
            indent=2,
        )
        handle.write("\n")

    print(f"wrote {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
