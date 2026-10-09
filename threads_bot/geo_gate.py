"""Local IP-to-country lookup for the Cloud Run management API."""

import ipaddress
import argparse
from pathlib import Path

import maxminddb

DATABASE = Path(__file__).resolve().parents[1] / "data" / "dbip-country-lite-2026-10.mmdb"


class GeoGate:
    def __init__(self, path=DATABASE):
        self.reader = maxminddb.open_database(str(path))

    def country(self, ip: str | None) -> str | None:
        if not ip:
            return None
        address = ipaddress.ip_address(ip)
        if not address.is_global:
            return None
        result = self.reader.get(ip)
        return (result or {}).get("country", {}).get("iso_code")

    def country_name(self, ip: str) -> str | None:
        if not self.country(ip):
            return None
        result = self.reader.get(ip)
        names = result.get("country", {}).get("names", {})
        return names.get("ja") or names.get("en")


def main(argv=None):
    parser = argparse.ArgumentParser(description="IPの推定国をローカルの国別データで確認します。")
    parser.add_argument("ip")
    args = parser.parse_args(argv)
    ip = str(ipaddress.ip_address(args.ip))
    gate = GeoGate()
    code = gate.country(ip)
    print(f"{ip}\t{code or '不明'}\t{gate.country_name(ip) or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
