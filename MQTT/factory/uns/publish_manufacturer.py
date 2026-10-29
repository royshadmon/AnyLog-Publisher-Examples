#!/usr/bin/env python3
"""Publish object and UNS policies.

An uns entry's object_id is an object name, or the description of an existing
object when several objects share one name. The script inserts any missing
object, reads the generated id, sets parent from the namespace above, and
inserts the uns policy.
"""

import argparse
import json
import urllib.request


def blockchain_get(conn: str, command: str) -> str:
    payload = json.dumps({"command": command, "AnyLog-Agent": "AnyLog/1.23"}).encode()
    request = urllib.request.Request(
        conn,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode().strip()
    if body in ("", "[]"):
        return ""
    return body


def insert_policy(conn: str, policy: dict) -> None:
    payload = f"<new_policy={json.dumps(policy)}>".encode()
    request = urllib.request.Request(
        conn,
        data=payload,
        headers={
            "User-Agent": "AnyLog/1.23",
            "AnyLog-Agent": "AnyLog/1.23",
            "command": "blockchain insert where policy=!new_policy and local=true and master=!ledger_conn",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        response.read()


def object_id(conn: str, reference: str) -> str:
    by_name = blockchain_get(conn, f'blockchain get object where name="{reference}" bring [*][id]')
    if len(by_name) == 32:
        return by_name
    by_description = blockchain_get(
        conn,
        f'blockchain get object where description="{reference}" bring [*][id]',
    )
    if len(by_description) == 32:
        return by_description
    raise SystemExit(f"No single object for {reference}")


def publish(conn: str, policies: list) -> None:
    ordered = [policy for policy in policies if "object" in policy]
    ordered.extend(policy for policy in policies if "uns" in policy)

    for policy in ordered:
        if "object" in policy:
            name = policy["object"]["name"]
            if blockchain_get(conn, f'blockchain get object where name="{name}" bring [*][id]'):
                print(f"exists object {name}")
                continue
            insert_policy(conn, policy)
            print(f"published object {name}")
            continue

        uns = dict(policy["uns"])
        namespace = uns["namespace"]
        reference = uns.get("object_id")
        if reference:
            uns["object_id"] = object_id(conn, reference)
        if "/" in namespace:
            parent = namespace.rsplit("/", 1)[0]
            uns["parent"] = blockchain_get(
                conn,
                f'blockchain get uns where namespace="{parent}" bring [*][id]',
            )
            if not uns["parent"]:
                raise SystemExit(f"No parent policy for {parent}")

        if blockchain_get(conn, f"blockchain get uns where namespace={namespace} bring [*][id]"):
            print(f"exists {namespace}")
            continue

        insert_policy(conn, {"uns": uns})
        print(f"published {namespace}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("conn", help="REST host:port of the standalone node")
    parser.add_argument("uns_file", help="UNS JSON file")
    args = parser.parse_args()

    conn = args.conn if args.conn.startswith("http") else f"http://{args.conn}"
    with open(args.uns_file, encoding="utf-8") as handle:
        policies = json.load(handle)
    publish(conn, policies)


if __name__ == "__main__":
    main()
