#!/usr/bin/env python3
"""Publish factory motor temperatures from one process.

config.yaml lists the brokers and which line each broker receives. This
process connects to every broker and publishes that line's three motors.

    company/factory_a/line_1/motor_7/temperature
    {"timestamp":"2026-10-29 10:00:00","site":"factory_a","line":"line_1",
     "machine":"motor_7","temperature_c":72.4}

From the repo root:

    .venv/bin/python "MQTT/factory/publisher.py" --dry-run
    .venv/bin/python "MQTT/factory/publisher.py"
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
import yaml

LOGGER = logging.getLogger("factory")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def segment(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise SystemExit(f"{name} must not be empty")
    if "/" in text or any(char in text for char in " \t+#"):
        raise SystemExit(f"{name} must be a single path segment")
    return text


@dataclass(frozen=True)
class PublishSettings:
    company: str
    site: str
    hertz: float
    qos: int
    retain: bool


@dataclass(frozen=True)
class Line:
    line_id: str
    temperature_c: float
    motors: tuple[str, ...]


@dataclass(frozen=True)
class BrokerTarget:
    host: str
    port: int
    line: Line

    @property
    def label(self) -> str:
        return f"{self.host}:{self.port}"


class Motor:
    def __init__(self, line_id: str, machine: str, temperature_c: float, rng: random.Random) -> None:
        self.line_id = line_id
        self.machine = machine
        self.center_c = temperature_c
        self.temperature_c = temperature_c
        self.rng = rng

    def sample(self, site: str, timestamp: str | None = None) -> dict[str, Any]:
        self.temperature_c += self.rng.gauss(0, 0.15)
        self.temperature_c += (self.center_c - self.temperature_c) * 0.05
        self.temperature_c = clamp(self.temperature_c, self.center_c - 8, self.center_c + 8)
        return {
            "timestamp": utc_timestamp() if timestamp is None else timestamp,
            "site": site,
            "line": self.line_id,
            "machine": self.machine,
            "temperature_c": round(self.temperature_c, 1),
        }


def topic(company: str, site: str, line_id: str, machine: str) -> str:
    return f"{company}/{site}/{line_id}/{machine}/temperature"


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SystemExit(f"{name} must be a mapping")
    return value


def _line(line_id: str, raw: Any) -> Line:
    entry = _mapping(raw, f"lines.{line_id}")
    motors_raw = entry.get("motors")
    if not isinstance(motors_raw, list) or len(motors_raw) != 3:
        raise SystemExit(f"lines.{line_id} must list exactly 3 motors")
    motors = tuple(segment(motor, f"lines.{line_id} motor") for motor in motors_raw)
    if len(set(motors)) != 3:
        raise SystemExit(f"lines.{line_id} repeats a motor")
    try:
        temperature_c = float(entry.get("temperature_c", 70))
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"lines.{line_id}.temperature_c must be a number") from exc
    return Line(line_id, temperature_c, motors)


def load_config(path: Path) -> tuple[list[BrokerTarget], PublishSettings]:
    if not path.exists():
        raise SystemExit(f"configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        raise SystemExit(f"configuration root must be a mapping: {path}")

    publish = _mapping(loaded.get("publish") or {}, "publish")
    try:
        hertz = float(publish.get("hertz", 1))
        qos = int(publish.get("qos", 0))
    except (TypeError, ValueError) as exc:
        raise SystemExit("publish.hertz and publish.qos must be numbers") from exc
    if hertz <= 0:
        raise SystemExit("publish.hertz must be greater than zero")
    if qos not in (0, 1, 2):
        raise SystemExit("publish.qos must be 0, 1, or 2")
    settings = PublishSettings(
        company=segment(publish.get("company", "company"), "publish.company"),
        site=segment(publish.get("site", "factory_a"), "publish.site"),
        hertz=hertz,
        qos=qos,
        retain=bool(publish.get("retain", False)),
    )

    lines_raw = _mapping(loaded.get("lines") or {}, "lines")
    if not lines_raw:
        raise SystemExit("lines must contain at least one line")
    lines = {segment(line_id, "lines key"): _line(segment(line_id, "lines key"), raw) for line_id, raw in lines_raw.items()}

    brokers_raw = loaded.get("brokers")
    if not isinstance(brokers_raw, list) or not brokers_raw:
        raise SystemExit("brokers must contain at least one broker")
    targets: list[BrokerTarget] = []
    for index, entry in enumerate(brokers_raw):
        broker = _mapping(entry, f"brokers[{index}]")
        host = str(broker.get("host", "")).strip()
        if not host:
            raise SystemExit(f"brokers[{index}].host is empty")
        try:
            port = int(broker["port"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit(f"brokers[{index}].port must be an integer") from exc
        if not 1 <= port <= 65535:
            raise SystemExit(f"brokers[{index}].port is out of range")
        line_id = segment(broker.get("line"), f"brokers[{index}].line")
        if line_id not in lines:
            known = ", ".join(lines)
            raise SystemExit(f"brokers[{index}].line {line_id!r} is not in lines ({known})")
        targets.append(BrokerTarget(host, port, lines[line_id]))
    return targets, settings


def motors_for(line: Line, salt: int) -> list[Motor]:
    motors: list[Motor] = []
    for index, machine in enumerate(line.motors):
        rng = random.Random(salt + index)
        start = line.temperature_c + (index - 1) * 1.6
        motors.append(Motor(line.line_id, machine, start, rng))
    return motors


class BrokerPublisher:
    def __init__(self, target: BrokerTarget, settings: PublishSettings) -> None:
        self.target = target
        self.settings = settings
        self.motors = motors_for(target.line, salt=target.port)
        client_id = f"factory-{target.line.line_id}-{target.port}"
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            protocol=mqtt.MQTTv311,
        )
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.connected = False

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        if reason_code.is_failure:
            self.connected = False
            LOGGER.error("%s connect failed: %s", self.target.label, reason_code)
            return
        self.connected = True
        LOGGER.info("connected %s line %s", self.target.label, self.target.line.line_id)

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.DisconnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        self.connected = False
        LOGGER.warning("disconnected %s: %s", self.target.label, reason_code)

    def connect(self) -> bool:
        try:
            self.client.connect(self.target.host, self.target.port, keepalive=60)
        except OSError as exc:
            LOGGER.error("cannot reach %s (%s)", self.target.label, exc)
            return False
        self.client.loop_start()
        return True

    def close(self) -> None:
        try:
            self.client.disconnect()
        except Exception:
            LOGGER.debug("disconnect failed for %s", self.target.label, exc_info=True)
        finally:
            self.client.loop_stop()

    def publish_once(self, timestamp: str | None = None) -> None:
        if timestamp is None and not self.connected:
            LOGGER.warning("skip %s; not connected", self.target.label)
            return
        for motor in self.motors:
            payload = motor.sample(self.settings.site, timestamp)
            path = topic(self.settings.company, self.settings.site, motor.line_id, motor.machine)
            encoded = json.dumps(payload, separators=(",", ":"))
            if timestamp is not None:
                LOGGER.info("%s %s %s", self.target.label, path, encoded)
                continue
            result = self.client.publish(path, payload=encoded, qos=self.settings.qos, retain=self.settings.retain)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                LOGGER.warning("publish to %s failed rc=%s", self.target.label, result.rc)
            else:
                LOGGER.info("%s %s %s", self.target.label, path, encoded)


def run_publish(targets: list[BrokerTarget], settings: PublishSettings, once: bool) -> int:
    publishers = [BrokerPublisher(target, settings) for target in targets]
    opened = [publisher for publisher in publishers if publisher.connect()]
    if not opened:
        LOGGER.error("no brokers accepted a connection")
        return 1

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not all(publisher.connected for publisher in opened):
        time.sleep(0.05)
    if not any(publisher.connected for publisher in opened):
        LOGGER.error("no brokers completed the handshake")
        for publisher in opened:
            publisher.close()
        return 1

    try:
        while True:
            for publisher in opened:
                publisher.publish_once()
            if once:
                break
            time.sleep(1.0 / settings.hertz)
    except KeyboardInterrupt:
        LOGGER.info("stopping")
    finally:
        for publisher in opened:
            publisher.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Publish factory motor temperatures from config.yaml.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("config.yaml"),
        help="YAML file with brokers, lines, and publish settings",
    )
    parser.add_argument("--hertz", type=float, help="Readings per second for each motor")
    parser.add_argument("--once", action="store_true", help="Publish one reading per motor and exit")
    parser.add_argument("--dry-run", action="store_true", help="Print one reading per motor without connecting")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        targets, settings = load_config(args.config)
    except SystemExit as exc:
        message = exc.code if isinstance(exc.code, str) else "invalid configuration"
        LOGGER.error("%s", message)
        return 1
    if args.hertz is not None:
        if args.hertz <= 0:
            LOGGER.error("--hertz must be greater than zero")
            return 1
        settings = PublishSettings(
            settings.company,
            settings.site,
            args.hertz,
            settings.qos,
            settings.retain,
        )

    if args.dry_run:
        stamp = "2026-10-29 10:00:00"
        for target in targets:
            publisher = BrokerPublisher(target, settings)
            publisher.publish_once(timestamp=stamp)
        return 0

    for target in targets:
        LOGGER.info("%s line %s motors %s", target.label, target.line.line_id, ", ".join(target.line.motors))
    LOGGER.info("publishing at %.3g Hz", settings.hertz)
    return run_publish(targets, settings, args.once)


if __name__ == "__main__":
    raise SystemExit(main())
