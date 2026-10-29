# AnyLog-Publisher-Examples

## MQTT factory

[MQTT/factory](MQTT/factory/README.md) publishes motor temperatures from one process. `config.yaml` sends `line_1` to the standalone node's broker and `line_2` to the operator node's broker. Each line has three motors, and the only measurement is temperature.

| timestamp | site | line | machine | temperature_c |
| --- | --- | --- | --- | --- |
| 2026-10-29 10:00:00 | factory_a | line_1 | motor_7 | 72.4 |

`line_1` publishes `motor_7`, `motor_8`, and `motor_9`. `line_2` publishes `motor_10`, `motor_11`, and `motor_12`. A reading is published on:

```text
company/factory_a/line_1/motor_7/temperature
```

From the repository root, after the virtualenv in [MQTT/factory/README.md](MQTT/factory/README.md) is installed:

```bash
.venv/bin/python "MQTT/factory/publisher.py" --dry-run
.venv/bin/python "MQTT/factory/publisher.py"
```

Two more paths add objects only for nodes that do not already exist. `siemens` selects `line_2`. `motor_10` and its temperature keep the objects created for `company/factory_a/line_2/motor_10`.

```text
manufacturer/abb/motor_7/temperature
enterprise/factory_a/line_1/motor_7/temperature
```

```bash
python3 MQTT/factory/uns/publish_manufacturer.py 127.0.0.1:32149 MQTT/factory/uns/manufacturer.json
python3 MQTT/factory/uns/publish_manufacturer.py 127.0.0.1:32149 MQTT/factory/uns/isa95.json
```

Node setup, the message client, and the SQL query are in [MQTT/factory/README.md](MQTT/factory/README.md).